import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from core.valuation import price_zone


CARD_CSS = """
<style>
.stock-card {
    background: #1e2130;
    border-radius: 12px;
    padding: 12px 14px;
    margin-bottom: 12px;
    border: 2px solid #2d3250;
    position: relative;
    transition: all 0.3s ease;
}
.stock-card.profit {
    border-color: #00ff88;
    box-shadow: 0 0 18px #00ff8866, 0 0 40px #00ff8833;
    animation: aurora-pulse 2s infinite alternate;
}
.stock-card.loss {
    border-color: #ff2255;
    box-shadow: 0 0 18px #ff225566, 0 0 40px #ff225533;
    animation: neon-flicker 1.5s infinite alternate;
}
@keyframes aurora-pulse {
    from { box-shadow: 0 0 18px #00ff8866, 0 0 40px #00ff8833; }
    to   { box-shadow: 0 0 30px #00ff88aa, 0 0 60px #00ff8855; }
}
@keyframes neon-flicker {
    from { box-shadow: 0 0 18px #ff225566, 0 0 40px #ff225533; }
    to   { box-shadow: 0 0 28px #ff2255aa, 0 0 55px #ff225555; }
}
.stock-card.relay-locked {
    border-color: #ffd700;
    box-shadow: 0 0 18px #ffd70066, 0 0 40px #ffd70033;
    animation: crown-pulse 2.5s infinite alternate;
}
.stock-card.exit-signal {
    border-color: #ff6600;
    box-shadow: 0 0 24px #ff660088, 0 0 50px #ff660044;
    animation: exit-flicker 1s infinite alternate;
}
@keyframes crown-pulse {
    from { box-shadow: 0 0 18px #ffd70066, 0 0 40px #ffd70033; }
    to   { box-shadow: 0 0 35px #ffd700bb, 0 0 65px #ffd70066; }
}
@keyframes exit-flicker {
    from { box-shadow: 0 0 20px #ff660088, 0 0 45px #ff660044; }
    to   { box-shadow: 0 0 35px #ff6600cc, 0 0 70px #ff660077; }
}
.val-band {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 0.8em;
    font-weight: 600;
    margin: 2px;
}
.val-green  { background:#00ff8822; color:#00ff88; border:1px solid #00ff8855; }
.val-blue   { background:#2277ff22; color:#66aaff; border:1px solid #2277ff55; }
.val-yellow { background:#ffaa0022; color:#ffcc44; border:1px solid #ffaa0055; }
.val-red    { background:#ff222222; color:#ff6666; border:1px solid #ff222255; }
.state-tag {
    font-size: 0.85em;
    font-weight: 700;
    padding: 2px 10px;
    border-radius: 20px;
    background: #2d3250;
}
.score-bar-wrap {
    background: #2d3250;
    border-radius: 6px;
    height: 8px;
    margin: 6px 0 10px 0;
    overflow: hidden;
}
.score-bar-fill {
    height: 100%;
    border-radius: 6px;
    transition: width 0.4s ease;
}
</style>
"""


def _render_kline(sid: str, nv: dict | None, dv: dict | None):
    from db.database import get_price_history
    ph = get_price_history(sid, n=120)
    if not ph or len(ph) < 5:
        st.caption("K棒資料不足，執行 ② 更新K棒 後顯示")
        return

    df = pd.DataFrame(ph)
    df["date"]  = pd.to_datetime(df["date"])
    df["ma20"]  = df["close"].rolling(20).mean()
    df["ma60"]  = df["close"].rolling(60).mean()

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.03,
    )

    # K 棒（台股紅漲綠跌）
    fig.add_trace(go.Candlestick(
        x=df["date"],
        open=df["open"], high=df["high"],
        low=df["low"],   close=df["close"],
        name="K棒",
        increasing_line_color="#ff4466", increasing_fillcolor="#ff4466",
        decreasing_line_color="#00ff88", decreasing_fillcolor="#00ff88",
        line_width=1,
    ), row=1, col=1)

    # MA 線
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["ma20"],
        name="MA20", line=dict(color="#66aaff", width=1.5), hoverinfo="skip",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["ma60"],
        name="MA60", line=dict(color="#ffaa44", width=1.5), hoverinfo="skip",
    ), row=1, col=1)

    # 估值水平線（常態軌道）
    if nv:
        for price, label, color in [
            (nv["support"],   "支撐", "#00ff88"),
            (nv["fair"],      "合理", "#66aaff"),
            (nv["expensive"], "昂貴", "#ff4466"),
        ]:
            fig.add_hline(
                y=price, row=1, col=1,
                line_dash="dot", line_color=color, line_width=1,
                annotation_text=f" {label} {price:.1f}",
                annotation_font_color=color,
                annotation_font_size=10,
            )

    # 成交量（紅漲綠跌）
    vol_colors = [
        "#ff4466" if c >= o else "#00ff88"
        for c, o in zip(df["close"], df["open"])
    ]
    fig.add_trace(go.Bar(
        x=df["date"], y=df["volume"],
        name="成交量", marker_color=vol_colors, showlegend=False,
    ), row=2, col=1)

    fig.update_layout(
        height=380,
        paper_bgcolor="#1e2130",
        plot_bgcolor="#1e2130",
        font=dict(color="#c4c9e2", size=11),
        xaxis_rangeslider_visible=False,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1, font_size=11,
        ),
        margin=dict(l=4, r=4, t=8, b=4),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#2d3250", showgrid=True, zeroline=False)
    fig.update_yaxes(gridcolor="#2d3250", showgrid=True, zeroline=False)

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_score_breakdown(score_data: dict):
    """評分明細區塊：進度條 + 三模組分項。"""
    total = score_data["total"]
    grade = score_data["grade"]
    bd    = score_data["breakdown"]
    m1    = bd["module1_momentum"]
    m2    = bd["module2_safety"]
    m3    = bd["module3_fuel"]

    if total >= 90:
        bar_color = "#ffd700"
    elif total >= 75:
        bar_color = "#66aaff"
    elif total >= 60:
        bar_color = "#888888"
    else:
        bar_color = "#444455"

    st.markdown(f"##### 📊 綜合評分　{grade}　**{total} 分 / 100**")
    st.markdown(
        f'<div class="score-bar-wrap">'
        f'<div class="score-bar-fill" style="width:{total}%;background:{bar_color};"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    mc1, mc2, mc3 = st.columns(3)
    m1bd = m1["breakdown"]
    m2bd = m2["breakdown"]
    m3bd = m3["breakdown"]

    mc1.markdown(
        f"**模組一：動能引擎** `{m1['total']}/40`\n\n"
        f"- ADX 位階：**{m1bd['adx_level']}**\n"
        f"- 開口力道：**{m1bd['di_gap']}**\n"
        f"- ADX 斜率：**{m1bd['adx_slope']}**"
    )
    mc2.markdown(
        f"**模組二：安全邊際** `{m2['total']}/30`\n\n"
        f"- 壓縮極值：**{m2bd['compression']}**\n"
        f"- 乖離防守：**{m2bd['ma20_deviation']}**"
    )
    mc3.markdown(
        f"**模組三：燃料族群** `{m3['total']}/30`\n\n"
        f"- 法人點火：**{m3bd['institutional']}**\n"
        f"- 股本輕盈：**{m3bd['chip_lightness']}**\n"
        f"- 族群共振：**{m3bd['theme_cluster']}**"
    )


def inject_css():
    st.markdown(CARD_CSS, unsafe_allow_html=True)


def _fmt_revenue(r: float) -> str:
    if r >= 1e8:  return f"{r/1e8:.1f} 億"
    if r >= 1e4:  return f"{r/1e4:.1f} 萬"
    return f"{r:.0f}"


def _revenue_summary(trend: list[dict]) -> dict | None:
    if not trend or len(trend) < 2:
        return None
    latest   = trend[-1]
    rev      = float(latest["revenue"] or 0)
    year     = latest["revenue_year"]
    month    = latest["revenue_month"]
    prev_rev = float(trend[-2]["revenue"] or 0)
    mom      = (rev - prev_rev) / prev_rev * 100 if prev_rev else 0

    yoy = None
    for r in trend:
        if r["revenue_year"] == year - 1 and r["revenue_month"] == month:
            base = float(r["revenue"] or 0)
            if base:
                yoy = (rev - base) / base * 100
            break

    accel = None
    if len(trend) >= 6:
        r3 = sum(float(r["revenue"] or 0) for r in trend[-3:]) / 3
        p3 = sum(float(r["revenue"] or 0) for r in trend[-6:-3]) / 3
        accel = r3 > p3

    return {
        "rev": rev, "year": year, "month": month,
        "mom": round(mom, 1),
        "yoy": round(yoy, 1) if yoy is not None else None,
        "accel": accel,
    }


def _val_band(label: str, price: float) -> str:
    if price is None:
        return ""
    return f'<span class="val-band val-blue">{label} {price:.1f}</span>'


def render_screener_card(stock: dict, params: dict):
    """渲染選股結果卡片（可展開詳細資訊）."""
    sid   = stock["stock_id"]
    name  = stock["stock_name"] or sid
    close = stock["close"] or 0
    state = stock["state"]
    tech  = stock["tech"]

    nv = stock.get("normal_val")
    dv = stock.get("dream_val")

    normal_zone = price_zone(close, nv, "normal") if nv else "N/A"
    dream_zone  = price_zone(close, dv, "dream")  if dv else "N/A"

    long_ok    = "✅" if stock.get("long_trend_ok") else "❌"
    score_data = stock.get("score")
    score_tag  = f"　{score_data['grade']} {score_data['total']}分" if score_data else ""

    with st.expander(
        f"**{sid} {name}**　{state}{score_tag}　收盤 {close:.1f} 元　長趨 {long_ok}",
        expanded=False,
    ):
        # ── 法人籌碼訊號（由批次排程抓取，無資料時不顯示） ──────────────────────
        inst = stock.get("inst_signal")
        if inst:
            both     = inst["chip_signal"] and inst["revenue_signal"]
            chip_only = inst["chip_signal"] and not inst["revenue_signal"]
            rev_only  = not inst["chip_signal"] and inst["revenue_signal"]
            date_str  = inst.get("checked_date", "")
            if both:
                st.success(
                    f"👑 **籌碼+營收雙訊號**（{date_str}）｜"
                    f"{inst['chip_reason']}｜{inst['revenue_reason']}"
                )
            elif chip_only:
                st.warning(
                    f"🔶 **法人買超訊號**（{date_str}）｜{inst['chip_reason']}"
                )
            elif rev_only:
                st.info(
                    f"🔷 **營收成長訊號**（{date_str}）｜{inst['revenue_reason']}"
                )

        # ── K 線圖 ────────────────────────────────────────────────────────────
        _render_kline(sid, nv, dv)

        # ── 評分明細 ──────────────────────────────────────────────────────────
        if score_data:
            st.divider()
            _render_score_breakdown(score_data)

        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### 🧱 軌道二：常態估值")
            if nv:
                st.markdown(
                    f'<span class="val-band val-green">支撐 {nv["support"]:.1f}</span>'
                    f'<span class="val-band val-blue">合理 {nv["fair"]:.1f}</span>'
                    f'<span class="val-band val-red">昂貴 {nv["expensive"]:.1f}</span>'
                    f'<br>TTM EPS: **{nv["ttm_eps"]:.2f}**　位置：{normal_zone}',
                    unsafe_allow_html=True
                )
            else:
                st.caption("TTM EPS ≤ 0，此軌道失效")

        with col2:
            st.markdown("##### 🚀 軌道一：本夢比估值（PSR）")
            if dv:
                st.markdown(
                    f'<span class="val-band val-green">起跑 {dv["launch"]:.1f}</span>'
                    f'<span class="val-band val-blue">目標 {dv["target"]:.1f}</span>'
                    f'<span class="val-band val-red">天花板 {dv["ceiling"]:.1f}</span>'
                    f'<br>每股營收 SPS: **{dv["sps"]:.2f}**　位置：{dream_zone}',
                    unsafe_allow_html=True
                )
                if dream_zone == "天花板 💀":
                    st.error("🔥 本夢比過熱警告：PSR 已達天花板倍數")
            else:
                st.caption("尚未取得股本或近12個月營收資料，執行 ⑤ 抓月營收+股本 後顯示")

        st.divider()
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("MA20",  f'{tech["ma20"]:.1f}'  if tech["ma20"]  else "N/A")
        mc2.metric("MA60",  f'{tech["ma60"]:.1f}'  if tech["ma60"]  else "N/A")
        mc3.metric("MA120", f'{tech["ma120"]:.1f}' if tech["ma120"] else "N/A")
        mc4.metric("MA240", f'{tech["ma240"]:.1f}' if tech["ma240"] else "N/A")

        # ── 月營收 ────────────────────────────────────────────────────────────
        rev_trend = stock.get("revenue_trend", [])
        rs = _revenue_summary(rev_trend)
        st.divider()
        st.markdown("##### 📊 月營收")
        if rs:
            rc1, rc2, rc3 = st.columns(3)
            mom_sign = "+" if rs["mom"] >= 0 else ""
            rc1.metric(
                f"最新月 ({rs['year']}/{rs['month']:02d})",
                _fmt_revenue(rs["rev"]),
                f"MoM {mom_sign}{rs['mom']:.1f}%",
            )
            if rs["yoy"] is not None:
                yoy_sign = "+" if rs["yoy"] >= 0 else ""
                rc2.metric("年增率 YoY", f"{yoy_sign}{rs['yoy']:.1f}%")
            else:
                rc2.metric("年增率 YoY", "N/A")
            if rs["accel"] is True:
                rc3.metric("近期趨勢", "加速 📈")
            elif rs["accel"] is False:
                rc3.metric("近期趨勢", "減速 📉")
            else:
                rc3.metric("近期趨勢", "N/A")
        elif rev_trend:
            st.caption("資料不足（需至少 2 個月）")
        else:
            st.caption("尚未抓取月營收，執行 ④ 抓月營收後顯示")

        # ── 題材標籤 ──────────────────────────────────────────────────────────
        st.divider()
        st.markdown("##### 🏷️ 題材標籤")
        current_themes = stock.get("themes") or []
        if current_themes:
            st.markdown(
                "".join(f'<span class="val-band val-blue">{t}</span>' for t in current_themes),
                unsafe_allow_html=True
            )
        tc1, tc2 = st.columns([4, 1])
        themes_input = tc1.text_input(
            "標籤（逗號分隔）", value="、".join(current_themes),
            key=f"themes_input_{sid}", label_visibility="collapsed",
            placeholder="例如：AI、CPO、GPU"
        )
        if tc2.button("儲存標籤", key=f"themes_save_{sid}"):
            from db.database import update_themes
            import re
            new_themes = [t.strip() for t in re.split(r"[、,，]", themes_input) if t.strip()]
            update_themes(sid, new_themes)
            st.success("標籤已更新")
            st.rerun()

        st.divider()
        c1, c2 = st.columns(2)
        if c1.button("❤️ 加入追蹤", key=f"watch_{sid}"):
            from db.database import add_watchlist
            add_watchlist(sid, close)
            st.success(f"{sid} 已加入追蹤，成本基準：{close:.1f} 元")
            st.rerun()
        if c2.button("🗑️ 從清單移除", key=f"del_{sid}"):
            from db.database import delete_stock
            delete_stock(sid)
            st.warning(f"{sid} 已從系統移除")
            st.rerun()


def render_watchlist_card(item: dict, params: dict):
    """渲染追蹤清單卡片，含停損停利光暈效果與法人籌碼接力鎖狀態。"""
    from db.database import get_stock, get_latest_close, remove_watchlist, update_institutional_status, get_price_history
    from core.institutional import get_ma20

    sid        = item["stock_id"]
    cost       = item["cost_price"]
    tracked_at = item["tracked_at"][:10]

    s     = get_stock(sid)
    name  = (s.get("stock_name") or sid) if s else sid
    close = get_latest_close(sid) or cost

    pnl_pct = (close - cost) / cost * 100 if cost else 0

    stop_loss_pct   = params["stop_loss_pct"]
    stop_profit_pct = params["stop_profit_pct"]
    inst_status     = item.get("institutional_status")
    ma20_guard      = item.get("ma20_guard_active", 0)

    # 法人狀態優先覆蓋卡片樣式
    if inst_status == "relay_locked":
        card_class = "relay-locked"
    elif inst_status == "exit_signal":
        card_class = "exit-signal"
    elif pnl_pct >= stop_profit_pct:
        card_class = "profit"
    elif pnl_pct <= -stop_loss_pct:
        card_class = "loss"
    else:
        card_class = ""

    pnl_color = "#00ff88" if pnl_pct > 0 else "#ff4466"
    pnl_sign  = "+" if pnl_pct > 0 else ""

    # 狀態徽章
    if inst_status == "relay_locked":
        status_badge = '<span style="color:#ffd700; font-weight:700; margin-left:8px;">👑 法人波段認養</span>'
    elif inst_status == "exit_signal":
        status_badge = '<span style="color:#ff6600; font-weight:700; margin-left:8px;">🚨 MA20 出場警示</span>'
    else:
        status_badge = ""

    st.markdown(
        f'<div class="stock-card {card_class}">'
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
        f'<span style="font-size:1.0em;font-weight:700;">{sid} {name}</span>'
        f'<span style="font-size:1.1em;font-weight:700;color:{pnl_color};">{pnl_sign}{pnl_pct:.2f}%</span>'
        f'</div>'
        f'<div style="color:#888;font-size:0.75em;">建倉 {tracked_at}{status_badge}</div>'
        f'<div style="margin-top:6px;color:#aaa;font-size:0.85em;">'
        f'成本 {cost:.2f}　現價 {close:.2f}　'
        f'<span style="color:{pnl_color};">{pnl_sign}{close-cost:.2f}</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True
    )

    # 法人波段認養：顯示 MA20 防守線
    if inst_status == "relay_locked" and ma20_guard:
        ph   = get_price_history(sid, n=60)
        ma20 = get_ma20(ph)
        if ma20:
            guard_color = "#ff6600" if close < ma20 else "#ffd700"
            st.markdown(
                f'<div style="color:{guard_color}; font-size:0.8em; padding:2px 0;">'
                f'🛡️ MA20 {ma20:.2f}　'
                f'（{"低於" if close < ma20 else "高於"}防守線）'
                f'</div>',
                unsafe_allow_html=True
            )

    # MA20 出場警示：紅色說明
    if inst_status == "exit_signal":
        st.caption("🚨 跌破MA20，建議確認籌碼並考慮出場")

    # 操作按鈕
    btn_cols = st.columns(2)
    if btn_cols[0].button("移除", key=f"rm_{sid}", use_container_width=True):
        remove_watchlist(sid)
        st.rerun()

    if inst_status in ("relay_locked", "exit_signal"):
        if btn_cols[1].button("🔓解除", key=f"unlock_{sid}", use_container_width=True,
                              help="將此標的重置為一般追蹤模式"):
            update_institutional_status(sid, None, ma20_guard=0)
            st.rerun()


def render_valuation_matrix(close: float, normal_val: dict | None,
                            dream_val: dict | None):
    """在「新增標的」頁面顯示雙軌估值矩陣卡片."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 🧱 軌道二：常態估值")
        if normal_val:
            nv = normal_val
            zone = price_zone(close, nv, "normal")
            st.metric("支撐價（低估）",  f"{nv['support']:.2f} 元",
                      delta=f"{close - nv['support']:.2f}")
            st.metric("合理價（中立）",  f"{nv['fair']:.2f} 元",
                      delta=f"{close - nv['fair']:.2f}")
            st.metric("昂貴價（高估）",  f"{nv['expensive']:.2f} 元",
                      delta=f"{close - nv['expensive']:.2f}")
            st.info(f"當前位置：{zone}　TTM EPS = {nv['ttm_eps']:.2f}")
        else:
            st.warning("TTM EPS ≤ 0，常態估值軌道失效")

    with col2:
        st.markdown("### 🚀 軌道一：本夢比估值（PSR）")
        if dream_val:
            dv = dream_val
            zone = price_zone(close, dv, "dream")
            st.metric("起跑點（低）",    f"{dv['launch']:.2f} 元",
                      delta=f"{close - dv['launch']:.2f}")
            st.metric("本夢比目標（中）", f"{dv['target']:.2f} 元",
                      delta=f"{close - dv['target']:.2f}")
            st.metric("天花板（高）",    f"{dv['ceiling']:.2f} 元",
                      delta=f"{close - dv['ceiling']:.2f}")
            st.info(f"當前位置：{zone}　每股營收 SPS = {dv['sps']:.2f}")
            if zone == "天花板 💀":
                st.error("🔥 本夢比過熱警告：PSR 已達天花板倍數")
        else:
            st.warning("尚未取得股本或近12個月營收資料，本夢比軌道未啟動")
