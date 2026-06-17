import pandas as pd
import numpy as np


def _build_df(history: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(history)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for col in ("open", "close", "high", "low", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def compute_indicators(history: list[dict]) -> dict:
    """
    計算所有技術指標，回傳包含最新狀態的 dict。
    需要至少 60 根 K 棒（建議 260 根）。
    """
    df = _build_df(history)
    result = {
        "enough_data": False,
        "last_close": None,
        "ma10": None, "ma20": None, "ma60": None,
        "ma120": None, "ma240": None,
        "volume": None,
        "vol_ma5": None, "vol_ma20": None,
        "compression": False,
        "breakout": False,
        "dmi_bullish": False,
        "long_trend_ok": False,
        "state": "不足資料",
        # 評分引擎需要的細粒度 DMI 數值
        "adx": None, "adx_prev": None,
        "plus_di": None, "minus_di": None,
        "di_gap": None, "di_gap_prev": None,
        # 評分引擎需要的均線/乖離數值
        "ma_spread_pct": None,
        "ma20_deviation_pct": None,
    }

    if len(df) < 60:
        return result

    result["enough_data"] = True

    # ── Moving Averages ───────────────────────────────────────────────────────
    df["ma10"]  = df["close"].rolling(10).mean()
    df["ma20"]  = df["close"].rolling(20).mean()
    df["ma60"]  = df["close"].rolling(60).mean()
    df["ma120"] = df["close"].rolling(120).mean() if len(df) >= 120 else np.nan
    df["ma240"] = df["close"].rolling(240).mean() if len(df) >= 240 else np.nan

    df["vol_ma5"]  = df["volume"].rolling(5).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()

    last = df.iloc[-1]
    result["last_close"] = last["close"]
    result["ma10"]  = last["ma10"]
    result["ma20"]  = last["ma20"]
    result["ma60"]  = last["ma60"]
    result["ma120"] = last["ma120"] if len(df) >= 120 else None
    result["ma240"] = last["ma240"] if len(df) >= 240 else None
    result["volume"]   = last["volume"]
    result["vol_ma5"]  = last["vol_ma5"]
    result["vol_ma20"] = last["vol_ma20"]

    # ── Long Trend Filter ─────────────────────────────────────────────────────
    price = last["close"]
    ma120_ok = (result["ma120"] is not None and not np.isnan(result["ma120"])
                and price > result["ma120"])
    ma240_ok = (result["ma240"] is not None and not np.isnan(result["ma240"])
                and price > result["ma240"])
    result["long_trend_ok"] = ma120_ok or ma240_ok

    # ── Compression Detection ─────────────────────────────────────────────────
    def is_compressed(row):
        mas = [row["ma10"], row["ma20"], row["ma60"]]
        if any(pd.isna(v) for v in mas) or min(mas) <= 0:
            return False
        spread = (max(mas) - min(mas)) / min(mas)
        return spread < 0.03 and row["volume"] < row["vol_ma20"]

    df["compressed"] = df.apply(is_compressed, axis=1)
    result["compression"] = bool(df.iloc[-1]["compressed"])

    # ── Breakout Detection ────────────────────────────────────────────────────
    recent_3 = df.iloc[-4:-1] if len(df) >= 4 else df.iloc[:-1]
    had_compression = recent_3["compressed"].any()

    prev_vol_ma5 = df.iloc[-2]["vol_ma5"] if len(df) >= 2 else last["vol_ma5"]
    vol_ok   = (not pd.isna(prev_vol_ma5) and
                last["volume"] > 2 * prev_vol_ma5)
    red_k    = last["close"] > last["open"]
    above_ma = (not pd.isna(last["ma20"]) and last["close"] > last["ma20"] and
                not pd.isna(last["ma60"]) and last["close"] > last["ma60"])

    result["breakout"] = bool(had_compression and vol_ok and red_k and above_ma)

    # ── DMI / ADX ─────────────────────────────────────────────────────────────
    dmi_data = _compute_dmi(df)
    result.update(dmi_data)

    # ── MA Spread（壓縮程度，供評分用） ─────────────────────────────────────────
    ma10v, ma20v, ma60v = result["ma10"], result["ma20"], result["ma60"]
    def _valid(v):
        return v is not None and not np.isnan(v) and v > 0
    if _valid(ma10v) and _valid(ma20v) and _valid(ma60v):
        ma_vals = [ma10v, ma20v, ma60v]
        result["ma_spread_pct"] = round(
            (max(ma_vals) - min(ma_vals)) / min(ma_vals) * 100, 2
        )

    # ── MA20 Deviation（股價與 MA20 乖離率，供評分用） ─────────────────────────
    if _valid(ma20v) and price is not None and not np.isnan(price):
        result["ma20_deviation_pct"] = round((price - ma20v) / ma20v * 100, 2)

    # ── State Label ───────────────────────────────────────────────────────────
    if result["breakout"]:
        result["state"] = "🔥 首發紅K"
    elif result["compression"]:
        result["state"] = "🎯 極度壓縮"
    elif result["dmi_bullish"]:
        result["state"] = "⚡ DMI加速"
    else:
        result["state"] = "📊 觀察中"

    return result


def _compute_dmi(df: pd.DataFrame, n: int = 14) -> dict:
    """ADX/DMI 計算，回傳完整數值供評分引擎使用。"""
    base = {
        "dmi_bullish": False,
        "adx": None, "adx_prev": None,
        "plus_di": None, "minus_di": None,
        "di_gap": None, "di_gap_prev": None,
    }
    try:
        high  = df["high"]
        low   = df["low"]
        close = df["close"]

        prev_high  = high.shift(1)
        prev_low   = low.shift(1)
        prev_close = close.shift(1)

        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)

        up_move   = high - prev_high
        down_move = prev_low - low

        plus_dm  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        atr      = tr.ewm(alpha=1/n, adjust=False).mean()
        plus_di  = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr

        dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
        adx = dx.ewm(alpha=1/n, adjust=False).mean()

        if len(adx) < 2:
            return base

        last_adx   = adx.iloc[-1]
        prev_adx   = adx.iloc[-2]
        last_plus  = plus_di.iloc[-1]
        prev_plus  = plus_di.iloc[-2]
        last_minus = minus_di.iloc[-1]
        prev_minus = minus_di.iloc[-2]

        if any(pd.isna(v) for v in [last_adx, prev_adx, last_plus, last_minus]):
            return base

        return {
            "dmi_bullish": bool(
                last_plus > last_minus and
                last_adx >= 20 and
                last_adx > prev_adx
            ),
            "adx":         round(float(last_adx),  2),
            "adx_prev":    round(float(prev_adx),   2),
            "plus_di":     round(float(last_plus),  2),
            "minus_di":    round(float(last_minus), 2),
            "di_gap":      round(float(last_plus  - last_minus), 2),
            "di_gap_prev": round(float(prev_plus  - prev_minus), 2),
        }
    except Exception:
        return base
