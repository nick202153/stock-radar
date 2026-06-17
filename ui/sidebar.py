import os
import streamlit as st

from db.database import get_setting, set_setting

SETTINGS_KEY = "sidebar_params"


def render_sidebar() -> dict:
    """
    渲染左側參數控制面板，回傳所有參數的 dict。
    使用者上次設定的參數會自動載入並還原為預設值。
    """
    saved = get_setting(SETTINGS_KEY, {})

    with st.sidebar:
        st.title("⚙️ 系統參數")
        token_ok = bool(os.getenv("FINMIND_TOKEN", "").strip())
        st.divider()

        # ── 資料抓取流程（置頂，操作入口） ───────────────────────────────────────
        st.subheader("🔄 資料抓取流程")
        fetch_limit = st.slider(
            "② K棒更新上限（檔數）",
            min_value=10, max_value=500, value=saved.get("fetch_limit", 50), step=10,
            help="控制每次「更新K棒」步驟的股票數量（每檔消耗 1 次 API 配額）。① 清單下載固定 1 次配額，不受此限。"
        )
        do_fetch_list = st.button(
            "📋 ① 抓取全市場清單", disabled=not token_ok, use_container_width=True,
            help="呼叫 TaiwanStockInfo，1 次 API，取得所有股票代號"
        )
        do_fetch_history = st.button(
            "📥 ② 更新K棒資料", disabled=not token_ok, use_container_width=True,
            help=f"對本地清單中最多 {fetch_limit} 檔做增量更新（每檔消耗 1 次 API 配額）"
        )
        do_screen = st.button(
            "🔍 ③ 執行篩選", type="primary", use_container_width=True,
            help="純本地運算，不消耗 API 配額"
        )

        # ── 篩選後資料抓取（有結果才顯示） ──────────────────────────────────────
        screen_results = st.session_state.get("screen_results")
        n = len(screen_results) if screen_results else 0
        if screen_results is not None:
            st.caption(f"篩選結果：**{n}** 檔標的")
        do_fetch_eps = st.button(
            f"💰 ④ 抓 EPS（{n} 檔）" if n else "💰 ④ 抓 EPS",
            disabled=(not token_ok or n == 0), use_container_width=True,
            help="為篩選通過的標的抓取 TTM EPS，每檔消耗 1 次 API 配額",
        )
        do_fetch_revenue = st.button(
            f"📈 ⑤ 抓月營收+股本（{n} 檔）" if n else "📈 ⑤ 抓月營收+股本",
            disabled=(not token_ok or n == 0), use_container_width=True,
            help="每檔消耗 2 次 API 配額（月營收+股本各 1 次）",
        )
        do_fetch_chip = st.button(
            f"🏦 ⑥ 抓法人籌碼（{n} 檔）" if n else "🏦 ⑥ 抓法人籌碼",
            disabled=(not token_ok or n == 0), use_container_width=True,
            help="抓取近30日三大法人買賣超，每檔消耗 1 次 API 配額",
        )
        do_fetch_all = st.button(
            f"🚀 ⑦ 一鍵抓全部（{n} 檔）" if n else "🚀 ⑦ 一鍵抓全部",
            disabled=(not token_ok or n == 0), use_container_width=True,
            type="primary" if n else "secondary",
            help="④EPS + ⑤月營收+股本 + ⑥法人籌碼，每檔最多消耗 4 次 API 配額",
        )

        st.divider()

        # ── 產業分類 / 題材篩選 ───────────────────────────────────────────────
        from db.database import get_industry_categories, get_all_themes

        st.subheader("🏭 產業分類篩選")
        industry_options = get_industry_categories()
        industries = st.multiselect(
            "產業分類（不選＝全部）",
            options=industry_options,
            default=[i for i in saved.get("industries", []) if i in industry_options],
            help="FinMind 官方產業分類，例如「半導體業」「電子工業」「航運業」"
        )

        st.subheader("🏷️ 自訂題材篩選")
        all_themes = get_all_themes()
        if all_themes:
            theme_options = all_themes + ["未分類"]
            themes = st.multiselect(
                "題材標籤（不選＝全部）",
                options=theme_options,
                default=[t for t in saved.get("themes", []) if t in theme_options],
                help="標籤可在選股結果卡片上自行新增；「未分類」表示尚無標籤的股票"
            )
        else:
            themes = []
            st.caption("尚無自訂標籤，可在選股結果卡片上新增")

        st.divider()

        # ── 價格區間 ──────────────────────────────────────────────────────────
        st.subheader("💰 全域價格區間")
        saved_price_range = saved.get("price_range", [20, 100])
        col_min, col_max = st.columns(2)
        with col_min:
            price_min = st.number_input(
                "最低股價（元）", min_value=1, max_value=9998,
                value=int(saved_price_range[0]), step=1
            )
        with col_max:
            price_max = st.number_input(
                "最高股價（元）", min_value=2, max_value=9999,
                value=int(saved_price_range[1]), step=1
            )
        price_range = (price_min, price_max)

        st.divider()

        # ── 常態本益比 ────────────────────────────────────────────────────────
        st.subheader("🧱 常態本益比設定（軌道二）")
        saved_normal_pe = saved.get("normal_pe", [12.0, 15.0, 18.0])
        normal_pe_low  = st.number_input("支撐本益比（低）", value=float(saved_normal_pe[0]), step=1.0, min_value=1.0)
        normal_pe_mid  = st.number_input("合理本益比（中）", value=float(saved_normal_pe[1]), step=1.0, min_value=1.0)
        normal_pe_high = st.number_input("昂貴本益比（高）", value=float(saved_normal_pe[2]), step=1.0, min_value=1.0)

        st.divider()

        # ── 本夢比（PSR） ─────────────────────────────────────────────────────
        st.subheader("🚀 本夢比設定（軌道一・PSR）")
        saved_dream_psr = saved.get("dream_psr", [1.2, 1.8, 2.5])
        dream_psr_low  = st.number_input("起跑 PSR（低）", value=float(saved_dream_psr[0]), step=0.1, min_value=0.1)
        dream_psr_mid  = st.number_input("目標 PSR（中）", value=float(saved_dream_psr[1]), step=0.1, min_value=0.1)
        dream_psr_high = st.number_input("天花板 PSR（高）", value=float(saved_dream_psr[2]), step=0.1, min_value=0.1)

        st.divider()

        # ── 篩選保護條件 ──────────────────────────────────────────────────────
        require_long_trend = st.checkbox(
            "🛡️ 啟用長線趨勢保護（MA120/240）",
            value=saved.get("require_long_trend", True),
            help="股價須大於 120 日線或 240 日線才納入篩選"
        )
        min_vol_ma20 = st.slider(
            "🚫 殭屍股均量門檻（張）",
            min_value=0, max_value=2000,
            value=saved.get("min_vol_ma20", 500), step=100,
            help="20日均量須超過此值，防止滑價風險（0 = 停用）"
        )
        filter_gross_margin = st.checkbox(
            "📊 啟用毛利率保護（過濾假作夢）",
            value=saved.get("filter_gross_margin", True),
            help="過濾最新季度毛利率低於門檻的公司，防止低毛利或虧損題材陷阱（需先抓取 EPS 才有資料）"
        )
        min_gross_margin_pct = st.slider(
            "毛利率最低門檻（%）",
            min_value=0, max_value=50,
            value=saved.get("min_gross_margin_pct", 0), step=5,
            disabled=not filter_gross_margin,
            help="最新一季毛利率須高於此值（0 = 僅過濾虧損，即毛利率 > 0%）"
        )

        st.divider()

        # ── 停損停利 ──────────────────────────────────────────────────────────
        st.subheader("🎯 停損 / 停利設定")
        stop_loss   = st.slider("停損 %", min_value=1, max_value=30, value=saved.get("stop_loss_pct", 7))
        stop_profit = st.slider("停利 %", min_value=5, max_value=100, value=saved.get("stop_profit_pct", 20))

        st.divider()

        # ── 法人籌碼接力設定 ──────────────────────────────────────────────────
        st.subheader("👑 法人籌碼接力設定")
        relay_yoy_threshold = st.slider(
            "月營收 YoY 門檻 (%)",
            min_value=5, max_value=50,
            value=saved.get("relay_yoy_threshold", 15), step=5,
            help="單月 YoY 超過此值視為基本面兌現（或3個月連續 MoM 正成長亦可）"
        )
        relay_chip_multiplier = st.slider(
            "法人買超異常倍數",
            min_value=0.5, max_value=3.0,
            value=float(saved.get("relay_chip_multiplier", 1.0)), step=0.5,
            help="近5日外資+投信均買超量 > 前期均值 × N 倍視為異常增量（或近3日連續買超亦可）"
        )

        st.divider()
        st.subheader("📊 API 剩餘配額")
        if st.button("🔍 查詢今日配額", disabled=not token_ok, key="query_quota"):
            from api.finmind_client import fetch_user_info
            body = fetch_user_info()
            if body.get("status") == 200:
                used      = int(body.get("user_count", 0))
                limit     = int(body.get("api_request_limit", 0))
                remaining = max(0, limit - used)
                qa, qb, qc = st.columns(3)
                qa.metric("已用", used)
                qb.metric("上限", limit)
                qc.metric("剩餘", remaining)
                if limit > 0:
                    pct = used / limit
                    st.progress(min(pct, 1.0), text=f"{pct*100:.0f}% 已消耗")
                    if pct > 1.0:
                        st.error("⚠️ 已超過今日配額上限，API 呼叫將會失敗")
            else:
                st.warning(f"查詢失敗（HTTP {body.get('status')}）：{body.get('msg', '未知錯誤')}")

        st.divider()
        st.caption("© 台股雷達系統 V3 | nick202153@gmail.com")

    persisted = {
        "industries":            industries,
        "themes":                themes,
        "price_range":           [float(price_range[0]), float(price_range[1])],
        "normal_pe":             [normal_pe_low, normal_pe_mid, normal_pe_high],
        "dream_psr":             [dream_psr_low, dream_psr_mid, dream_psr_high],
        "stop_loss_pct":         stop_loss,
        "stop_profit_pct":       stop_profit,
        "fetch_limit":           fetch_limit,
        "require_long_trend":    require_long_trend,
        "min_vol_ma20":          min_vol_ma20,
        "filter_gross_margin":   filter_gross_margin,
        "min_gross_margin_pct":  min_gross_margin_pct,
        "relay_yoy_threshold":   relay_yoy_threshold,
        "relay_chip_multiplier": relay_chip_multiplier,
    }
    if persisted != saved:
        set_setting(SETTINGS_KEY, persisted)

    return {
        **persisted,
        "min_price":             float(price_range[0]),
        "max_price":             float(price_range[1]),
        "min_vol_ma20":          min_vol_ma20,
        "filter_gross_margin":   filter_gross_margin,
        "min_gross_margin_pct":  float(min_gross_margin_pct),
        "do_fetch_list":         do_fetch_list,
        "do_fetch_history":      do_fetch_history,
        "do_screen":             do_screen,
        "do_fetch_eps":          do_fetch_eps,
        "do_fetch_revenue":      do_fetch_revenue,
        "do_fetch_chip":         do_fetch_chip,
        "do_fetch_all":          do_fetch_all,
    }
