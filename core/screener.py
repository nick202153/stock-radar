import json

from db.database import get_all_stocks, get_price_history, get_latest_close, get_ttm_revenue
from core.valuation import compute_normal_valuation, compute_dream_valuation, compute_sps, parse_pe_set
from core.technical import compute_indicators


def _parse_themes(raw) -> list[str]:
    try:
        return json.loads(raw) or []
    except (json.JSONDecodeError, TypeError):
        return []


def run_screener(
    min_price: float = 20.0,
    max_price: float = 100.0,
    normal_pe: list[float] = None,
    dream_psr: list[float] = None,
    require_long_trend: bool = True,
    industries: list[str] = None,
    themes: list[str] = None,
    min_vol_ma20: int = 500,
    filter_gross_margin: bool = True,
    min_gross_margin_pct: float = 0.0,
) -> list[dict]:
    """
    執行完整的五層漏斗選股流程。
    回傳通過篩選的標的列表，每個元素包含估值、技術狀態等完整資訊。
    """
    normal_pe = normal_pe or [12.0, 15.0, 18.0]
    dream_psr = dream_psr or [1.2, 1.8, 2.5]

    stocks = get_all_stocks()
    results = []

    for s in stocks:
        sid = s["stock_id"]

        # ── 層0：產業分類 / 自訂題材篩選 ─────────────────────────────────────────
        if industries and s.get("industry_category") not in industries:
            continue

        stock_themes = _parse_themes(s.get("themes"))
        if themes:
            if "未分類" in themes and not stock_themes:
                pass
            elif not set(stock_themes) & set(themes):
                continue

        # ── 層0b：毛利率保護（過濾本業虧損 / 假作夢題材） ─────────────────────────
        if filter_gross_margin:
            gm = s.get("gross_margin_pct")
            if gm is not None and gm <= min_gross_margin_pct:
                continue

        # ── 層1：價格區間篩選 ─────────────────────────────────────────────────
        latest_close = get_latest_close(sid)
        if latest_close is None or not (min_price <= latest_close <= max_price):
            continue

        # ── 層2：技術指標計算 ─────────────────────────────────────────────────
        history = get_price_history(sid, n=280)
        try:
            tech = compute_indicators(history)
        except Exception:
            continue

        if not tech["enough_data"]:
            continue

        # ── 層2b：絕對均量保護（殭屍股過濾） ─────────────────────────────────────
        # vol_ma20 來自 daily_prices.volume，單位是「股」（FinMind Trading_Volume 原始值）；
        # min_vol_ma20 滑桿單位是「張」，故比對前需 /1000 換算。
        if min_vol_ma20 > 0:
            vol_ma20 = tech.get("vol_ma20")
            if vol_ma20 is None or (vol_ma20 / 1000) < min_vol_ma20:
                continue

        # ── 層3：長線趨勢保護 ─────────────────────────────────────────────────
        if require_long_trend and not tech["long_trend_ok"]:
            continue

        # ── 層4：估值引擎 ─────────────────────────────────────────────────────
        stock_normal_pe = parse_pe_set(s.get("normal_pe_set", ""), normal_pe)
        stock_dream_psr = parse_pe_set(s.get("dream_psr_set", ""), dream_psr)

        shares_issued = s.get("shares_issued")
        ttm_revenue   = get_ttm_revenue(sid)
        sps           = compute_sps(shares_issued, ttm_revenue)

        normal_val = compute_normal_valuation(s.get("ttm_eps"), stock_normal_pe)
        dream_val  = compute_dream_valuation(sps, stock_dream_psr)

        results.append({
            "stock_id":         sid,
            "stock_name":       s.get("stock_name") or "",
            "industry":         s.get("industry_category", ""),
            "themes":           stock_themes,
            "close":            latest_close,
            "volume":           tech.get("volume"),
            "ttm_eps":          s.get("ttm_eps"),
            "shares_issued":    shares_issued,
            "ttm_revenue":      ttm_revenue,
            "sps":              sps,
            "normal_val":       normal_val,
            "dream_val":        dream_val,
            "tech":             tech,
            "state":            tech["state"],
            "long_trend_ok":    tech["long_trend_ok"],
            "compression":      tech["compression"],
            "breakout":         tech["breakout"],
            "dmi_bullish":      tech["dmi_bullish"],
        })

    # 排序：首發紅K > 極度壓縮 > DMI加速 > 觀察中
    state_order = {"🔥 首發紅K": 0, "🎯 極度壓縮": 1, "⚡ DMI加速": 2, "📊 觀察中": 3, "不足資料": 4}
    results.sort(key=lambda x: state_order.get(x["state"], 99))
    return results


def analyze_single(stock_id: str,
                   normal_pe: list[float] = None,
                   dream_psr: list[float] = None) -> dict | None:
    """
    對單一標的執行完整分析（不做價格篩選）。
    用於「新增標的」頁面的即時估值展示。
    """
    from db.database import get_stock
    normal_pe = normal_pe or [12.0, 15.0, 18.0]
    dream_psr = dream_psr or [1.2, 1.8, 2.5]

    s = get_stock(stock_id)
    if not s:
        return None

    history = get_price_history(stock_id, n=280)
    tech     = compute_indicators(history)
    latest_close = get_latest_close(stock_id)

    stock_normal_pe = parse_pe_set(s.get("normal_pe_set", ""), normal_pe)
    stock_dream_psr = parse_pe_set(s.get("dream_psr_set", ""), dream_psr)

    shares_issued = s.get("shares_issued")
    ttm_revenue   = get_ttm_revenue(stock_id)
    sps           = compute_sps(shares_issued, ttm_revenue)

    return {
        "stock_id":      stock_id,
        "stock_name":    s.get("stock_name") or "",
        "industry":      s.get("industry_category", ""),
        "themes":        _parse_themes(s.get("themes")),
        "close":         latest_close,
        "ttm_eps":       s.get("ttm_eps"),
        "shares_issued": shares_issued,
        "ttm_revenue":   ttm_revenue,
        "sps":           sps,
        "normal_val":    compute_normal_valuation(s.get("ttm_eps"), stock_normal_pe),
        "dream_val":     compute_dream_valuation(sps, stock_dream_psr),
        "tech":          tech,
        "state":         tech["state"],
    }
