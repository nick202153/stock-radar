"""
獨立批次更新腳本 - 針對「不延遲超過 2 天」訴求優化。

執行優先序：
  Phase 3  Watchlist 出場確認（最高優先，保命）
  Phase 2  篩選結果法人訊號更新（確保 UI 看到的標的有最新法人資料）
  Phase 1  【核心】全市場 K 棒追齊（確保篩選漏斗不漏標的，目標延遲 < 1 天）
  Phase 0  深度財務資料補齊（TTM EPS, 月營收，消耗剩餘配額）
"""

from datetime import datetime, timedelta
from pathlib import Path

from db.database import (
    init_db,
    get_all_stocks,
    get_price_coverage,
    get_revenue_coverage,
    update_ttm_eps,
    update_gross_margin,
    update_shares_issued,
    get_setting,
    upsert_institutional_signal,
    get_watchlist,
    get_latest_close,
    get_price_history,
    update_institutional_status,
    increment_ma20_break_days,
    reset_ma20_break_days,
    update_last_update_attempt,
    update_last_price_attempt,
    get_active_stock_ids,
)
from api.finmind_client import (
    populate_stock_pool,
    fetch_price_history,
    fetch_financial_data,
    fetch_and_store_revenue,
    fetch_shares_issued,
    fetch_institutional_investors,
    get_effective_trading_date,
)
from core.institutional import check_relay_condition, get_ma20, check_ma20_exit

QUOTA_LIMIT = 580
LOG_FILE = Path(__file__).parent / "batch_update.log"


def log(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def is_quota_exhausted(e: Exception) -> bool:
    msg = str(e)
    return "402" in msg or "429" in msg or "limit" in msg.lower()


# ── Phase 2：篩選標的法人籌碼訊號 ─────────────────────────────────────────────

def run_phase2_screened(quota_used: int) -> int:
    screen_ids = get_setting("last_screen_stock_ids", [])
    if not screen_ids: return 0

    params = get_setting("sidebar_params", {})
    yoy_threshold = float(params.get("relay_yoy_threshold", 15.0))
    mult = float(params.get("relay_chip_multiplier", 1.0))
    today = datetime.now().strftime("%Y-%m-%d")

    consumed = 0
    for sid in screen_ids:
        if quota_used + consumed >= QUOTA_LIMIT: break
        try:
            rows = fetch_institutional_investors(sid, days=30)
            consumed += 1
            res = check_relay_condition(sid, rows, yoy_threshold, mult)
            upsert_institutional_signal(
                stock_id=sid,
                chip_signal=int(res["chip"]["signal"]),
                revenue_signal=int(res["revenue"]["signal"]),
                chip_reason=res["chip"]["reason"],
                revenue_reason=res["revenue"]["reason"],
                checked_date=today,
            )
        except Exception as e:
            if is_quota_exhausted(e): break
    return consumed


# ── Phase 3：Watchlist relay_locked 出場確認 ──────────────────────────────────

def run_phase3_watchlist_exit(quota_used: int) -> int:
    watchlist = get_watchlist()
    relay_items = [i for i in watchlist if i.get("institutional_status") == "relay_locked"]
    if not relay_items: return 0

    today = datetime.now().strftime("%Y-%m-%d")
    consumed = 0
    for item in relay_items:
        if quota_used + consumed >= QUOTA_LIMIT: break
        sid = item["stock_id"]
        close = get_latest_close(sid) or item["cost_price"]
        ph = get_price_history(sid, n=60)
        ma20 = get_ma20(ph)
        if ma20 is None: continue

        if close >= ma20:
            if item.get("ma20_break_days", 0) > 0: reset_ma20_break_days(sid)
            continue

        break_days = increment_ma20_break_days(sid)
        if break_days >= 2:
            try:
                rows = fetch_institutional_investors(sid, days=10)
                consumed += 1
                exit_info = check_ma20_exit(ph, rows)
                if exit_info["chip_turning"]:
                    update_institutional_status(sid, "exit_signal", check_date=today, ma20_guard=0)
                    reset_ma20_break_days(sid)
                    log(f"{sid} 🚨 籌碼轉賣，觸發出場訊號")
            except Exception as e:
                if is_quota_exhausted(e): break
    return consumed


# ── 並發鎖（避免排程與手動執行同時打真實配額） ──────────────────────────────────

LOCK_FILE = Path(__file__).parent / "batch_update.lock"
LOCK_STALE_MINUTES = 20  # 正常一輪約7-9分鐘，超過此時間視為上次異常中斷留下的殘留鎖


def _acquire_lock() -> bool:
    if LOCK_FILE.exists():
        age_min = (datetime.now().timestamp() - LOCK_FILE.stat().st_mtime) / 60
        if age_min < LOCK_STALE_MINUTES:
            return False
    LOCK_FILE.write_text(datetime.now().isoformat())
    return True


def _release_lock():
    LOCK_FILE.unlink(missing_ok=True)


# ── 主流程 ────────────────────────────────────────────────────────────────────

PRICE_RETRY_HOURS = 6   # 價格抓不到新資料（下市/停牌）時，冷卻時間，避免無限重試燒配額
PHASE0_RESERVE    = 50  # 保留給Phase0（財務資料/股本）的最低配額，避免Phase1吃光導致股本永遠補不到


def main():
    if not _acquire_lock():
        log("偵測到另一個排程仍在執行中（鎖檔未過期），本次跳過避免配額衝突")
        return
    try:
        _run()
    finally:
        _release_lock()


def _run():
    init_db()
    today = get_effective_trading_date()
    # 兩天前的日期，作為「必須更新」的臨界點
    fresh_cutoff = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

    log(f"=== 排程啟動 (有效交易日: {today}, 鮮度門檻: {fresh_cutoff}) ===")

    pool = get_all_stocks()
    if not pool:
        populate_stock_pool()
        pool = get_all_stocks()

    coverage = get_price_coverage()
    rev_coverage = get_revenue_coverage()
    quota_used = 0

    # 1. 優先保護資產與既有結果
    quota_used += run_phase3_watchlist_exit(quota_used)
    quota_used += run_phase2_screened(quota_used)

    # 2. 全市場價格同步 (確保篩選器有最新 K 棒)
    # 優先處理：資料太舊的 (>2天) > 活躍標的 (30天有成交) > 其他
    active_ids = set(get_active_stock_ids(days=30))

    def price_priority(s):
        sid = s["stock_id"]
        last_date = coverage.get(sid, "1970-01-01")
        if last_date < fresh_cutoff: return 0  # 最急：過期標的
        if sid in active_ids: return 1         # 次急：有交易標的
        return 2                               # 緩：權證或殭屍股

    def _price_cooled_down(s) -> bool:
        # 用獨立的 last_price_attempt（非財務資料的 last_update_attempt），
        # 避免兩個Phase互相干擾彼此的冷卻判斷
        last_try = s.get("last_price_attempt")
        if not last_try:
            return True
        try:
            return datetime.now() - datetime.strptime(last_try, "%Y-%m-%d %H:%M:%S") > timedelta(hours=PRICE_RETRY_HOURS)
        except ValueError:
            return True

    price_todo = [
        s for s in pool
        if coverage.get(s["stock_id"], "") < today and _price_cooled_down(s)
    ]
    price_todo.sort(key=price_priority)

    log(f"[Phase 1] 待同步 K 棒共 {len(price_todo)} 檔")

    price_count = 0
    for s in price_todo:
        if quota_used >= QUOTA_LIMIT - PHASE0_RESERVE: break  # 保留配額給Phase0
        sid = s["stock_id"]
        try:
            fetch_price_history(sid)
            quota_used += 1
            price_count += 1
            update_last_price_attempt(sid)
        except Exception as e:
            update_last_price_attempt(sid)
            if is_quota_exhausted(e): break

    # 3. 深度資料更新 (利用剩餘配額補齊 EPS/月營收/股本)
    deep_todo = [
        s for s in pool
        if (s["ttm_eps"] is None or s["stock_id"] not in rev_coverage or s["shares_issued"] is None)
    ]
    # 已經嘗試過的 20 小時內不重複嘗試（用 last_update_attempt，與價格Phase的冷卻互不干擾）
    deep_todo = [s for s in deep_todo if not s.get("last_update_attempt") or
                 (datetime.now() - datetime.strptime(s["last_update_attempt"], "%Y-%m-%d %H:%M:%S") > timedelta(hours=20))]

    log(f"[Phase 0] 剩餘配額 {QUOTA_LIMIT - quota_used}，準備補齊財務資料（含股本）")

    deep_count = 0
    for s in deep_todo:
        if quota_used >= QUOTA_LIMIT: break
        sid = s["stock_id"]
        try:
            if s["ttm_eps"] is None:
                fin = fetch_financial_data(sid)
                update_ttm_eps(sid, fin["ttm_eps"])
                update_gross_margin(sid, fin["gross_margin_pct"])
                quota_used += 1

            if quota_used < QUOTA_LIMIT and sid not in rev_coverage:
                fetch_and_store_revenue(sid)
                quota_used += 1

            if quota_used < QUOTA_LIMIT and s["shares_issued"] is None:
                shares = fetch_shares_issued(sid)
                if shares:
                    update_shares_issued(sid, shares)
                quota_used += 1

            update_last_update_attempt(sid)
            deep_count += 1
        except Exception as e:
            if is_quota_exhausted(e): break

    # 剩餘待補統計（重新查DB，反映本次排程的實際結果）
    fresh_pool     = get_all_stocks()
    fresh_coverage = get_price_coverage()
    fresh_rev      = get_revenue_coverage()
    price_remaining = sum(1 for s in fresh_pool if fresh_coverage.get(s["stock_id"], "") < today)
    deep_remaining  = sum(
        1 for s in fresh_pool
        if s["ttm_eps"] is None or s["shares_issued"] is None or s["stock_id"] not in fresh_rev
    )

    log(
        f"排程結束。同步價格 {price_count} 檔（剩餘待同步 {price_remaining} 檔），"
        f"補齊財務 {deep_count} 檔（剩餘待補 {deep_remaining} 檔），總配額消耗 {quota_used}"
    )


if __name__ == "__main__":
    main()
