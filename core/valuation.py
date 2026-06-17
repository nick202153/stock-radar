import json


def compute_normal_valuation(ttm_eps: float, pe_set: list[float]) -> dict | None:
    """
    軌道二：常態本益比估值。
    pe_set = [low, mid, high]，例如 [12, 15, 18]。
    ttm_eps <= 0 則此軌道失效，回傳 None。
    """
    if ttm_eps is None or ttm_eps <= 0:
        return None
    low, mid, high = pe_set
    return {
        "support":    round(ttm_eps * low, 2),
        "fair":       round(ttm_eps * mid, 2),
        "expensive":  round(ttm_eps * high, 2),
        "pe_low":     low,
        "pe_mid":     mid,
        "pe_high":    high,
        "ttm_eps":    ttm_eps,
    }


def compute_sps(shares_issued: int, ttm_revenue: float) -> float | None:
    """SPS（每股營收）= TTM 營收 / 發行股數。任一資料缺失或營收 <= 0 則回傳 None。"""
    if shares_issued is None or shares_issued <= 0 or ttm_revenue is None or ttm_revenue <= 0:
        return None
    return ttm_revenue / shares_issued


def compute_dream_valuation(sps: float, psr_set: list[float]) -> dict | None:
    """
    軌道一：本夢比 PSR 重估估值。
    psr_set = [low, mid, high]，例如 [1.2, 1.8, 2.5]。
    sps（每股營收）<= 0 則此軌道失效，回傳 None。
    """
    if sps is None or sps <= 0:
        return None
    low, mid, high = psr_set
    return {
        "launch":   round(sps * low, 2),
        "target":   round(sps * mid, 2),
        "ceiling":  round(sps * high, 2),
        "psr_low":  low,
        "psr_mid":  mid,
        "psr_high": high,
        "sps":      sps,
    }


def price_zone(price: float, valuation: dict, mode: str) -> str:
    """
    回傳當前價格在估值區間的位置標籤。
    mode='normal': support/fair/expensive
    mode='dream':  launch/target/ceiling
    """
    if valuation is None or price is None:
        return "N/A"

    if mode == "normal":
        sup = valuation["support"]
        fair = valuation["fair"]
        exp = valuation["expensive"]
        if price < sup:
            return "低估區 🟢"
        elif price <= fair:
            return "合理區 🔵"
        elif price <= exp:
            return "偏貴區 🟡"
        else:
            return "昂貴區 🔴"

    if mode == "dream":
        launch = valuation["launch"]
        target = valuation["target"]
        ceiling = valuation["ceiling"]
        if price < launch:
            return "起跑前 ⚪"
        elif price <= target:
            return "作夢中 🚀"
        elif price <= ceiling:
            return "牛市頂 🌙"
        else:
            return "天花板 💀"

    return "N/A"


def parse_pe_set(pe_json: str, default: list) -> list[float]:
    try:
        parsed = json.loads(pe_json)
        if isinstance(parsed, list) and len(parsed) == 3:
            return [float(x) for x in parsed]
    except Exception:
        pass
    return [float(x) for x in default]
