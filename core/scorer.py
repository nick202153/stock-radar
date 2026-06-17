"""
綜合評分引擎 (滿分 100 分)
模組一：動能引擎   40 分
模組二：型態安全   30 分
模組三：燃料族群   30 分
"""


def _score_momentum(tech: dict) -> dict:
    """模組一：動能引擎 40 分"""
    bd = {}

    adx      = tech.get("adx")
    adx_prev = tech.get("adx_prev")
    di_gap      = tech.get("di_gap")
    di_gap_prev = tech.get("di_gap_prev")

    # 1. ADX 位階 (10 分)
    # 只看「當下位階」，趨勢是否在加速由 s3 斜率分負責，不在這裡重複
    s1 = 0
    if adx is not None and not _nan(adx):
        if 20 <= adx <= 30:
            s1 = 10   # 趨勢剛起步的最佳甜蜜區
        elif 30 < adx <= 38:
            s1 = 5    # 趨勢已行進，仍可參與
    bd["adx_level"] = s1

    # 2. 開口撕裂力道 (15 分)
    # 修正：用 abs(di_gap_prev) 做基準，避免前日負值讓任何正值都輕易得分
    s2 = 0
    if di_gap is not None and di_gap_prev is not None and not _nan(di_gap):
        abs_prev = abs(di_gap_prev)
        if di_gap > 8 and di_gap > abs_prev * 1.5:
            s2 = 15   # 開口大 AND 相對昨天大幅放大
        elif di_gap > 0 and di_gap > abs_prev:
            s2 = 5    # 正開口 AND 淨放大（含由負轉正且幅度更大）
    bd["di_gap"] = s2

    # 3. ADX 斜率加速度 (15 分)
    s3 = 0
    if adx is not None and adx_prev is not None and not _nan(adx):
        diff = adx - adx_prev
        if diff > 1.5:
            s3 = 15
        elif diff >= 0.5:
            s3 = 5
    bd["adx_slope"] = s3

    return {"total": s1 + s2 + s3, "breakdown": bd}


def _score_safety(tech: dict) -> dict:
    """模組二：型態與安全邊際 30 分"""
    bd = {}

    spread = tech.get("ma_spread_pct")
    dev    = tech.get("ma20_deviation_pct")

    # 4. 均線壓縮極值 (15 分)
    s4 = 0
    if spread is not None and not _nan(spread):
        if spread < 3.0:
            s4 = 15
        elif spread <= 5.0:
            s4 = 5
    bd["compression"] = s4

    # 5. 盈虧比/防守距離 (15 分，可扣 -10)
    # 修正：只有股價高於 MA20 超過 10% 才扣分（追高風險）
    # 低於 MA20 的情況已由長線趨勢保護篩除，不重複懲罰
    s5 = 0
    if dev is not None and not _nan(dev):
        abs_dev = abs(dev)
        if abs_dev < 5.0:
            s5 = 15
        elif abs_dev <= 8.0:
            s5 = 5
        elif dev > 15.0:          # 股價超過 MA20 15%+ = 追高風險（10% 為漲停板，不誤殺首發紅K）
            s5 = -10
    bd["ma20_deviation"] = s5

    return {"total": s4 + s5, "breakdown": bd}


def _score_fuel(inst_score: int, shares_issued: int | None,
                themes: list, theme_counts: dict) -> dict:
    """模組三：燃料與族群性 30 分"""
    bd = {}

    # 6. 法人第一根點火 (0/5/15，由呼叫方計算後傳入)
    bd["institutional"] = inst_score

    # 7. 籌碼輕盈度 (10 分)
    # 股本 = shares_issued (股) × 面額 10 元 / 1e9 = 億元
    s7 = 0
    if shares_issued:
        capital_bn = shares_issued * 10 / 1e9
        if capital_bn < 5:        # < 50億：真正輕盈小票，主力好拉
            s7 = 10
        elif capital_bn <= 10:    # 50~100億：中小型，仍有爆發空間
            s7 = 5
    bd["chip_lightness"] = s7

    # 8. 族群共振 (5 分)：同概念在篩選結果中出現 ≥ 3 檔
    s8 = 0
    if themes and theme_counts:
        for t in themes:
            if theme_counts.get(t, 0) >= 3:
                s8 = 5
                break
    bd["theme_cluster"] = s8

    return {"total": inst_score + s7 + s8, "breakdown": bd}


def score_stock(
    tech: dict,
    shares_issued: int | None = None,
    inst_score: int = 0,
    themes: list = None,
    theme_counts: dict = None,
) -> dict:
    """
    主評分函式。
    - inst_score: 0/5/15，由 compute_first_ignition_score() 計算後傳入
    - theme_counts: {theme: count}，全部篩選結果統計後傳入
    """
    m1 = _score_momentum(tech)
    m2 = _score_safety(tech)
    m3 = _score_fuel(inst_score, shares_issued, themes or [], theme_counts or {})

    total = max(0, min(100, m1["total"] + m2["total"] + m3["total"]))

    if total >= 90:
        grade = "🌟 極強"
    elif total >= 75:
        grade = "🔵 強烈"
    elif total >= 60:
        grade = "⚪ 普通"
    else:
        grade = "▫️ 弱"

    return {
        "total": total,
        "grade": grade,
        "breakdown": {
            "module1_momentum": m1,
            "module2_safety":   m2,
            "module3_fuel":     m3,
        },
    }


def compute_first_ignition_score(rows: list[dict], shares_issued: int | None) -> int:
    """
    法人第一根點火得分。
    rows: TaiwanStockInstitutionalInvestors，由舊到新排列。

    修正原設計固定 500 張門檻 → 改為相對門檻：
    threshold = max(50, 發行張數 × 0.05%)，小股至少 50 張
    """
    if not rows or len(rows) < 2:
        return 0

    def net_buy(r):
        fi = float(r.get("Foreign_Investors_net_buy") or 0)
        it = float(r.get("Investment_Trust_net_buy") or 0)
        return fi + it

    daily = [net_buy(r) for r in rows]

    # 最新一筆買超量
    latest_buy = daily[-1]

    # 相對門檻（張）
    if shares_issued:
        threshold_lots = max(200.0, shares_issued / 1000 * 0.0005)
    else:
        threshold_lots = 500.0

    # 第一根點火：過去 10 天（不含今日）全部 ≤ 0，今日買超 ≥ 門檻
    lookback = daily[:-1][-10:]
    first_ignition = (
        latest_buy >= threshold_lots and
        all(d <= 0 for d in lookback)
    )
    if first_ignition:
        return 15

    # 連續買超 ≤ 3 天
    consecutive = 0
    for d in reversed(daily):
        if d > 0:
            consecutive += 1
        else:
            break
    if 1 <= consecutive <= 3:
        return 5

    return 0


def _nan(v) -> bool:
    try:
        import math
        return math.isnan(v)
    except (TypeError, ValueError):
        return False
