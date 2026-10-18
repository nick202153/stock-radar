import os
import time
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.finmindtrade.com/api/v4/data"
_RATE_DELAY = 0.5  # seconds between API calls
_MARKET_CLOSE_HOUR = 14  # 台股收盤13:30 + FinMind資料更新緩衝，14點前視為昨日才是最新交易日


def get_effective_trading_date(cutoff_hour: int = _MARKET_CLOSE_HOUR) -> str:
    """
    回傳「目前可預期已有資料」的最新交易日（非單純日曆日）。
    跨日後到收盤緩衝時間前，當日資料尚未出爐，視為前一天，避免對空資料發送無效API call。
    再跳過週六/週日，避免長假期間目標日落在非交易日導致永遠抓不到資料（無法判斷國定假日，仍可能卡住）。
    """
    now = datetime.now()
    eff = now if now.hour >= cutoff_hour else now - timedelta(days=1)
    while eff.weekday() >= 5:  # 5=Sat, 6=Sun
        eff -= timedelta(days=1)
    return eff.strftime("%Y-%m-%d")


def _token() -> str:
    t = os.getenv("FINMIND_TOKEN", "").strip()
    if not t:
        raise RuntimeError(
            "FINMIND_TOKEN 未設定。請複製 .env.example 為 .env 並填入您的 Token。"
        )
    # FinMind token 是 JWT，header 必為 base64url("{"...) → 開頭固定是 eyJ
    # 常見貼錯：多打一個 y 變成 yeyJ
    if t.startswith("ye") and not t.startswith("eyJ"):
        raise RuntimeError(
            "FINMIND_TOKEN 格式錯誤：Token 開頭多了一個 'y'，"
            "請開啟 .env 把最前面的 'y' 刪除（正確格式以 eyJ 開頭）。"
        )
    return t


def _get(dataset: str, stock_id: str = None, start_date: str = None,
         end_date: str = None) -> list[dict]:
    params = {"dataset": dataset, "token": _token()}
    if stock_id:
        params["data_id"] = stock_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    time.sleep(_RATE_DELAY)
    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.HTTPError as e:
        # 不暴露 token，只顯示狀態碼與回應內容
        try:
            msg = resp.json().get("msg", resp.text[:200])
        except Exception:
            msg = resp.text[:200]
        raise RuntimeError(
            f"FinMind API HTTP {resp.status_code}｜dataset={dataset}｜{msg}"
        ) from None

    body = resp.json()
    if body.get("status") != 200:
        raise RuntimeError(f"FinMind API 錯誤：{body.get('msg', 'unknown')}｜dataset={dataset}")
    return body.get("data", [])


# ── Stock Info ────────────────────────────────────────────────────────────────

def fetch_all_stock_info() -> list[dict]:
    """
    一次 API call 取得全台股上市上櫃清單（含名稱、產業）。
    回傳只保留 4 碼數字代號的標的。
    """
    rows = _get("TaiwanStockInfo")
    return [
        {
            "stock_id":         r.get("stock_id", ""),
            "stock_name":       r.get("stock_name", ""),
            "industry_category": r.get("industry_category", ""),
        }
        for r in rows
        if str(r.get("stock_id", "")).isdigit() and len(str(r.get("stock_id", ""))) == 4
    ]


def fetch_stock_info(stock_id: str) -> dict | None:
    """取得單一股票基本資料並寫入 DB。"""
    from db.database import upsert_stock
    rows = _get("TaiwanStockInfo", stock_id=stock_id)
    if not rows:
        return None
    r = rows[0]
    upsert_stock(
        stock_id=r.get("stock_id", stock_id),
        stock_name=r.get("stock_name", ""),
        industry_category=r.get("industry_category", ""),
    )
    return r


# ── Price History ─────────────────────────────────────────────────────────────

def fetch_price_history(stock_id: str, start_date: str = None,
                        end_date: str = None) -> list[tuple]:
    """
    增量更新單一股票的 OHLCV 歷史資料。
    自動從 DB 查詢最新日期，只抓新資料。
    回傳寫入 DB 的筆數 tuple list。
    """
    from db.database import get_latest_date, upsert_prices

    latest = get_latest_date(stock_id)
    if latest:
        day_after = (
            datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        start_date = start_date or day_after
    else:
        start_date = start_date or (
            datetime.now() - timedelta(days=400)
        ).strftime("%Y-%m-%d")

    end_date = end_date or get_effective_trading_date()

    if start_date > end_date:
        return []  # 已是最新

    rows = _get("TaiwanStockPrice", stock_id=stock_id,
                start_date=start_date, end_date=end_date)

    records = [
        (
            r.get("stock_id", stock_id),
            r["date"],
            float(r.get("open", 0) or 0),
            float(r.get("close", 0) or 0),
            float(r.get("max", 0) or 0),
            float(r.get("min", 0) or 0),
            int(r.get("Trading_Volume", 0) or 0),
        )
        for r in rows
        if r.get("date")
    ]

    if records:
        upsert_prices(records)
    return records


# ── Stock List Fetch (2-Phase) ────────────────────────────────────────────────

def populate_stock_pool(max_count: int = None) -> list[dict]:
    """
    Phase 1：用 TaiwanStockInfo（1次 API call）取得全市場清單，
    寫入 stock_pool DB，回傳候選清單。
    max_count：限制寫入筆數（None = 全部）。
    """
    from db.database import upsert_stock

    all_info = fetch_all_stock_info()
    if max_count:
        all_info = all_info[:max_count]

    for s in all_info:
        upsert_stock(
            stock_id=s["stock_id"],
            stock_name=s["stock_name"],
            industry_category=s["industry_category"],
        )
    return all_info


def update_price_history_batch(stock_ids: list[str],
                               progress_cb=None) -> dict[str, int]:
    """
    Phase 2：批次增量更新指定股票清單的歷史K棒。
    progress_cb(i, total, stock_id)：進度回呼。
    回傳 {stock_id: 新增筆數}。
    """
    results = {}
    total = len(stock_ids)
    for i, sid in enumerate(stock_ids):
        if progress_cb:
            progress_cb(i, total, sid)
        try:
            records = fetch_price_history(sid)
            results[sid] = len(records)
        except Exception as e:
            results[sid] = -1  # 標記失敗
    return results


# ── User Info ─────────────────────────────────────────────────────────────────

def fetch_user_info() -> dict:
    """查詢 FinMind 帳戶今日 API 使用量。回傳完整 response body。"""
    try:
        resp = requests.get(
            "https://api.web.finmindtrade.com/v2/user_info",
            params={"token": _token()},
            timeout=10
        )
        return resp.json()
    except Exception as e:
        return {"status": -1, "msg": str(e), "data": {}}


# ── Monthly Revenue ───────────────────────────────────────────────────────────

def fetch_and_store_revenue(stock_id: str) -> int:
    """抓取月營收並存入 DB，回傳寫入筆數。"""
    from db.database import upsert_revenue
    start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    end   = datetime.now().strftime("%Y-%m-%d")
    rows  = _get("TaiwanStockMonthRevenue",
                 stock_id=stock_id, start_date=start, end_date=end)
    records = []
    for r in rows:
        year  = r.get("revenue_year")
        month = r.get("revenue_month")
        rev   = r.get("revenue")
        if year and month and rev is not None:
            records.append((stock_id, int(year), int(month), round(float(rev))))
    if records:
        upsert_revenue(records)
    return len(records)


# ── Shares Issued (for PSR/SPS) ─────────────────────────────────────────────────

def fetch_shares_issued(stock_id: str) -> int | None:
    """取得最新一筆發行股數（TaiwanStockShareholding，帶 data_id 為 Free tier 可用）。"""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = _get("TaiwanStockShareholding", stock_id=stock_id,
                start_date=start, end_date=end)
    if not rows:
        return None
    rows.sort(key=lambda x: x.get("date", ""), reverse=True)
    shares = rows[0].get("NumberOfSharesIssued")
    try:
        s = int(shares)
    except (TypeError, ValueError):
        return None
    return s if s > 0 else None


# ── Institutional Investors (三大法人) ──────────────────────────────────────────

def fetch_institutional_investors(stock_id: str, days: int = 30) -> list[dict]:
    """
    抓取三大法人買賣超明細（TaiwanStockInstitutionalInvestorsBuySell），由舊到新排列。
    原始資料每日有多行（各法人種類），此函式聚合為每日一行：
      Foreign_Investors_net_buy = 外陸資 + 外資自營商 合計淨買超
      Investment_Trust_net_buy  = 投信 淨買超
    每次呼叫消耗 1 次 API 配額。
    """
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end   = datetime.now().strftime("%Y-%m-%d")
    rows  = _get("TaiwanStockInstitutionalInvestorsBuySell", stock_id=stock_id,
                 start_date=start, end_date=end)

    # 聚合：外資=外陸資+外資自營商，投信=投信
    by_date: dict[str, dict] = {}
    for r in rows:
        date = r.get("date", "")
        name = r.get("name", "")
        buy  = float(r.get("buy",  0) or 0)
        sell = float(r.get("sell", 0) or 0)
        net  = buy - sell
        if date not in by_date:
            by_date[date] = {"date": date, "Foreign_Investors_net_buy": 0.0,
                             "Investment_Trust_net_buy": 0.0}
        if "外陸資" in name or "外資自營商" in name:
            by_date[date]["Foreign_Investors_net_buy"] += net
        elif name == "投信":
            by_date[date]["Investment_Trust_net_buy"] += net

    return sorted(by_date.values(), key=lambda x: x["date"])


# ── TAIEX MA20 Status ────────────────────────────────────────────────────────

def fetch_taiex_ma20_status() -> dict:
    """
    抓取加權股價指數（TaiwanStockPrice / TAIEX）近60日收盤，
    計算MA20，判斷大盤是否在月線之上。消耗 1 次 API 配額。
    """
    try:
        start = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        end   = datetime.now().strftime("%Y-%m-%d")
        rows  = _get("TaiwanStockPrice", stock_id="TAIEX",
                     start_date=start, end_date=end)
        if not rows:
            return {"above_ma20": None, "taiex_close": None, "ma20": None,
                    "error": "TaiwanStockPrice/TAIEX 無資料"}
        rows.sort(key=lambda x: x.get("date", ""))
        closes = [float(r.get("close", 0) or 0) for r in rows
                  if float(r.get("close", 0) or 0) > 0]
        if len(closes) < 20:
            return {"above_ma20": None, "taiex_close": None, "ma20": None,
                    "error": f"有效資料不足（{len(closes)} 筆，需 ≥ 20）"}
        ma20       = sum(closes[-20:]) / 20
        last_close = closes[-1]
        return {
            "above_ma20":   last_close >= ma20,
            "taiex_close":  round(last_close, 2),
            "ma20":         round(ma20, 2),
            "error":        None,
        }
    except Exception as e:
        return {"above_ma20": None, "taiex_close": None, "ma20": None, "error": str(e)}


# ── EPS ───────────────────────────────────────────────────────────────────────

def fetch_financial_data(stock_id: str) -> dict:
    """
    一次 API call 取得 TTM EPS 與最新一季毛利率。
    回傳 {"ttm_eps": float, "gross_margin_pct": float | None}。
    gross_margin_pct 若找不到資料則為 None（安全 fallback，不影響篩選）。
    type 字串實測確認：EPS / GrossProfit / Revenue（2330 2025-Q1 驗證 ≈ 58.8%）。
    """
    start = (datetime.now() - timedelta(days=550)).strftime("%Y-%m-%d")
    end   = datetime.now().strftime("%Y-%m-%d")
    rows  = _get("TaiwanStockFinancialStatements",
                 stock_id=stock_id, start_date=start, end_date=end)

    # TTM EPS（最近 4 季）
    eps_rows = sorted(
        [r for r in rows if r.get("type") == "EPS"],
        key=lambda x: x.get("date", ""), reverse=True
    )
    ttm_eps = round(sum(float(r.get("value", 0) or 0) for r in eps_rows[:4]), 2)

    # 最新一季毛利率 = GrossProfit / Revenue * 100
    gp_rows  = sorted(
        [r for r in rows if r.get("type") == "GrossProfit"],
        key=lambda x: x.get("date", ""), reverse=True
    )
    rev_rows = sorted(
        [r for r in rows if r.get("type") == "Revenue"],
        key=lambda x: x.get("date", ""), reverse=True
    )

    gross_margin_pct = None
    if gp_rows and rev_rows:
        latest_date = gp_rows[0].get("date", "")
        rev_same = next((r for r in rev_rows if r.get("date", "") == latest_date), None)
        if rev_same:
            gp  = float(gp_rows[0].get("value", 0) or 0)
            rev = float(rev_same.get("value", 0) or 0)
            if rev > 0:
                gross_margin_pct = round(gp / rev * 100, 2)

    return {"ttm_eps": ttm_eps, "gross_margin_pct": gross_margin_pct}


def fetch_ttm_eps(stock_id: str) -> float:
    """取得最近 4 季 EPS 加總（TTM）。"""
    return fetch_financial_data(stock_id)["ttm_eps"]
