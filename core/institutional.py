from db.database import get_revenue_trend


def compute_institutional_signal(rows: list[dict], abnormal_multiplier: float = 1.0) -> dict:
    """
    判斷外資+投信合計買賣超是否達到法人認養訊號。
    rows: 由舊到新排列的 TaiwanStockInstitutionalInvestors 資料（至少5筆）
    """
    if not rows or len(rows) < 5:
        return {"signal": False, "reason": "資料不足（需至少 5 個交易日）", "avg_5d": 0, "avg_20d": 0}

    def net_buy(r):
        fi = float(r.get("Foreign_Investors_net_buy") or 0)
        it = float(r.get("Investment_Trust_net_buy") or 0)
        return fi + it

    daily    = [net_buy(r) for r in rows]
    recent_5 = daily[-5:]
    prior    = daily[:-5]

    avg_5d  = sum(recent_5) / 5
    avg_20d = sum(prior) / len(prior) if prior else 0

    # 條件1：近5日均買超量明顯高於前期均值
    if avg_20d > 0:
        abnormal = avg_5d > avg_20d * (1 + abnormal_multiplier)
    else:
        abnormal = avg_5d > 0

    # 條件2：近3日連續買超
    continuous = all(d > 0 for d in recent_5[-3:])

    signal = abnormal or continuous
    parts  = []
    if abnormal:
        parts.append(f"近5日均 {avg_5d:.0f} 張 vs 前期均 {avg_20d:.0f} 張（異常增量）")
    if continuous:
        parts.append("近3日連續買超")

    return {
        "signal": signal,
        "reason": "、".join(parts) if parts else "無明顯法人動向",
        "avg_5d":  avg_5d,
        "avg_20d": avg_20d,
    }


def compute_revenue_signal(stock_id: str, yoy_threshold: float = 15.0) -> dict:
    """判斷月營收是否達到基本面兌現條件（YoY 或連續 MoM 成長）。"""
    trend = get_revenue_trend(stock_id, n=13)
    if not trend or len(trend) < 2:
        return {"signal": False, "reason": "無月營收資料", "yoy": None, "consecutive_mom_up": False}

    latest = trend[-1]
    rev    = float(latest["revenue"] or 0)
    year   = latest["revenue_year"]
    month  = latest["revenue_month"]

    yoy = None
    for r in trend:
        if r["revenue_year"] == year - 1 and r["revenue_month"] == month:
            base = float(r["revenue"] or 0)
            if base:
                yoy = (rev - base) / base * 100
            break

    consecutive_mom_up = False
    if len(trend) >= 4:
        consecutive_mom_up = all(
            float(trend[-i - 1]["revenue"] or 0) > float(trend[-i - 2]["revenue"] or 0)
            for i in range(3)
        )

    yoy_ok = yoy is not None and yoy > yoy_threshold
    signal = yoy_ok or consecutive_mom_up

    parts = []
    if yoy_ok:
        parts.append(f"YoY +{yoy:.1f}% > 門檻 {yoy_threshold:.0f}%")
    if consecutive_mom_up:
        parts.append("近3個月 MoM 連續正成長")

    return {
        "signal": signal,
        "reason": "、".join(parts) if parts else "營收未達兌現條件",
        "yoy": round(yoy, 1) if yoy is not None else None,
        "consecutive_mom_up": consecutive_mom_up,
    }


def check_relay_condition(stock_id: str, institutional_rows: list[dict],
                           yoy_threshold: float = 15.0,
                           abnormal_multiplier: float = 1.0) -> dict:
    """
    法人籌碼接力鎖雙重驗證入口：籌碼面 AND 基本面同時通過才算認養。
    Returns: {"passed": bool, "chip": dict, "revenue": dict}
    """
    chip    = compute_institutional_signal(institutional_rows, abnormal_multiplier)
    revenue = compute_revenue_signal(stock_id, yoy_threshold)
    return {
        "passed":  chip["signal"] and revenue["signal"],
        "chip":    chip,
        "revenue": revenue,
    }


def get_ma20(price_history: list[dict]) -> float | None:
    """從本地 K 棒資料計算 MA20，無需 API。"""
    if not price_history or len(price_history) < 20:
        return None
    closes = [r["close"] for r in price_history]
    return round(sum(closes[-20:]) / 20, 2)


def check_ma20_exit(price_history: list[dict], institutional_rows: list[dict]) -> dict:
    """
    MA20 防守出場訊號：股價跌破 MA20 AND 近2日法人由買轉賣，兩條件同時成立才觸發。
    institutional_rows 可傳 [] 表示未抓取籌碼，此時僅判斷價格條件。
    """
    ma20 = get_ma20(price_history)
    if ma20 is None:
        return {"exit": False, "ma20": None, "price_below_ma20": False, "chip_turning": False}

    latest_close = price_history[-1]["close"]
    price_below  = latest_close < ma20

    chip_turning = False
    if institutional_rows and len(institutional_rows) >= 2:
        def net_buy(r):
            fi = float(r.get("Foreign_Investors_net_buy") or 0)
            it = float(r.get("Investment_Trust_net_buy") or 0)
            return fi + it
        chip_turning = all(net_buy(r) < 0 for r in institutional_rows[-2:])

    return {
        "exit":            price_below and chip_turning,
        "ma20":            ma20,
        "price_below_ma20": price_below,
        "chip_turning":    chip_turning,
    }
