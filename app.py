import streamlit as st
import os
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="賺大錢雷達",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 初始化 ─────────────────────────────────────────────────────────────────────
from db.database import init_db
init_db()

from ui.sidebar import render_sidebar
from ui.components import inject_css, render_screener_card, render_watchlist_card, render_valuation_matrix

inject_css()

# ── 全域 CSS ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 2px solid #2d3250; }
    .stTabs [data-baseweb="tab"] {
        background: transparent !important;
        border-radius: 6px 6px 0 0;
        padding: 8px 20px;
        font-weight: 600;
        color: #6b7280 !important;
        border-bottom: 2px solid transparent;
    }
    .stTabs [aria-selected="true"] {
        background: transparent !important;
        color: #ffffff !important;
        border-bottom: 2px solid #7c83d4 !important;
    }
    .stTabs [data-baseweb="tab"]:hover {
        color: #c4c9e2 !important;
        background: #1e2130 !important;
    }
    .stExpander { border: 1px solid #2d3250 !important; border-radius: 10px; }
    div[data-testid="metric-container"] > label { font-size: 0.8em; color: #888; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
params = render_sidebar()

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown("## 📡 要賺錢要先懂選股")

token_set = bool(os.getenv("FINMIND_TOKEN", "").strip())
if not token_set:
    st.warning(
        "⚠️ **FINMIND_TOKEN 未設定**。"
        "請複製 `.env.example` 為 `.env` 並填入您的 FinMind API Token，"
        "然後重新啟動 Streamlit。"
    )

# ── 大盤位階偵測（每次 session 抓一次，消耗 1 次配額） ──────────────────────────
if token_set and "taiex_status" not in st.session_state:
    from api.finmind_client import fetch_taiex_ma20_status
    st.session_state["taiex_status"] = fetch_taiex_ma20_status()

_taiex = st.session_state.get("taiex_status")
if _taiex and _taiex.get("error") is None and not _taiex["above_ma20"]:
    st.error(
        f"⚠️ **大盤偏弱（低於 MA20）**｜"
        f"加權指數 {_taiex['taiex_close']:,.0f}，MA20 {_taiex['ma20']:,.0f}｜"
        "突破成功率降低，建議縮小資金部位"
    )

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_screen, tab_watch, tab_add = st.tabs(["🔍 選股雷達", "📋 追蹤清單", "➕ 新增標的"])


# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: 選股雷達
# ══════════════════════════════════════════════════════════════════════════════
with tab_screen:
    st.markdown(f"#### 篩選條件：{params['min_price']:.0f} ~ {params['max_price']:.0f} 元")
    st.caption(
        "**操作流程**（按鈕位於左側 Sidebar「🔄 資料抓取流程」）："
        "① 抓取清單（1 次 API call） → "
        "② 更新K棒（依限制批次） → ③ 執行篩選（本地運算）"
    )

    do_fetch_list    = params["do_fetch_list"]
    do_fetch_history = params["do_fetch_history"]
    do_screen        = params["do_screen"]

    # ── Step 1: 抓取全市場清單 ────────────────────────────────────────────────
    if do_fetch_list:
        with st.spinner("呼叫 TaiwanStockInfo（1 次 API call）..."):
            try:
                from api.finmind_client import populate_stock_pool
                stocks = populate_stock_pool()   # 1 次 API call，全市場清單
                st.success(f"清單更新完成！共 {len(stocks)} 檔台股寫入本地 DB。")
            except Exception as e:
                st.error(f"抓取清單失敗：{e}")

    # ── Step 2: 批次更新K棒 ───────────────────────────────────────────────────
    if do_fetch_history:
        from db.database import get_all_stocks, get_price_coverage
        from api.finmind_client import fetch_price_history
        pool = get_all_stocks()
        if not pool:
            st.warning("本地清單為空，請先執行「① 抓取全市場清單」。")
        else:
            coverage = get_price_coverage()
            total_pool = len(pool)
            fetched_count = len(coverage)

            # 未抓過的排前面，已有資料的排後面（增量更新幾乎不消耗配額）
            unfetched = [s for s in pool if s["stock_id"] not in coverage]
            already   = [s for s in pool if s["stock_id"] in coverage]
            ordered   = unfetched + already

            limit   = params["fetch_limit"]
            targets = ordered[:limit]

            st.caption(
                f"📊 總清單 {total_pool} 檔｜已有K棒 {fetched_count} 檔｜"
                f"**尚未抓取 {len(unfetched)} 檔**｜"
                f"本次目標 {len(targets)} 檔（優先補齊未抓取）"
            )

            prog = st.progress(0, text="準備更新...")
            errors = []
            for i, s in enumerate(targets):
                sid = s["stock_id"]
                prog.progress((i + 1) / len(targets),
                              text=f"更新 {sid} {s['stock_name']} ({i+1}/{len(targets)})")
                try:
                    fetch_price_history(sid)
                except Exception as e:
                    errors.append(f"{sid}: {e}")
            prog.empty()
            newly_done = len(unfetched[:limit])
            if errors:
                st.warning(
                    f"完成（{len(targets) - len(errors)}/{len(targets)} 成功）\n" +
                    "\n".join(errors[:5])
                )
            else:
                st.success(
                    f"K棒更新完成！新補 {newly_done} 檔，增量更新 {len(targets)-newly_done} 檔。"
                    f"　全市場進度：{min(fetched_count+newly_done, total_pool)}/{total_pool}"
                )

    if do_screen:
        with st.spinner("執行五層漏斗篩選..."):
            from core.screener import run_screener
            from core.scorer import score_stock
            from db.database import set_setting, get_institutional_signals_bulk
            from collections import Counter
            results_tmp = run_screener(
                min_price=params["min_price"],
                max_price=params["max_price"],
                normal_pe=params["normal_pe"],
                dream_psr=params["dream_psr"],
                require_long_trend=params["require_long_trend"],
                industries=params["industries"],
                themes=params["themes"],
                min_vol_ma20=params["min_vol_ma20"],
                filter_gross_margin=params.get("filter_gross_margin", True),
                min_gross_margin_pct=params.get("min_gross_margin_pct", 0.0),
            )
            # 族群共振計數（全部結果一起統計）
            theme_counts = Counter(t for r in results_tmp for t in r.get("themes", []))
            # 讀取已有法人點火分數（若之前抓過）
            inst_sigs = get_institutional_signals_bulk([r["stock_id"] for r in results_tmp])
            for r in results_tmp:
                inst_sc = inst_sigs.get(r["stock_id"], {}).get("first_ignition_score", 0) or 0
                r["score"] = score_stock(
                    r["tech"],
                    shares_issued=r.get("shares_issued"),
                    inst_score=int(inst_sc),
                    themes=r.get("themes", []),
                    theme_counts=theme_counts,
                )
            # 按評分降序排列（分數相同則保留原狀態優先序）
            results_tmp.sort(key=lambda x: x["score"]["total"], reverse=True)
            st.session_state["screen_results"] = results_tmp
            _SCREEN_PARAM_KEYS = ["min_price", "max_price", "normal_pe", "dream_psr",
                                   "require_long_trend", "industries", "themes"]
            st.session_state["last_screen_params"] = {k: params.get(k) for k in _SCREEN_PARAM_KEYS}
            # 儲存篩選通過 ID，供批次排程抓法人籌碼資料
            set_setting("last_screen_stock_ids", [r["stock_id"] for r in results_tmp])

    # ── 顯示篩選結果（從 session_state 讀，跨互動持久） ───────────────────────
    results = st.session_state.get("screen_results")

    if results is not None:
        _SCREEN_PARAM_KEYS = ["min_price", "max_price", "normal_pe", "dream_psr",
                               "require_long_trend", "industries", "themes"]
        _last = st.session_state.get("last_screen_params", {})
        if any(str(params.get(k)) != str(_last.get(k)) for k in _SCREEN_PARAM_KEYS):
            st.warning("⚠️ 篩選條件已變更，目前顯示的是舊結果，請重新執行左側 **③ 執行篩選**。")
        if not results:
            st.info("無標的通過篩選條件。請先抓取股票資料，或放寬篩選參數。")
        else:
            do_fetch_eps     = params["do_fetch_eps"]
            do_fetch_revenue = params["do_fetch_revenue"]
            do_fetch_chip    = params["do_fetch_chip"]
            do_fetch_all     = params["do_fetch_all"]

            st.success(f"通過篩選：**{len(results)}** 檔標的")

            # ── 顯示控制列 ───────────────────────────────────────────────────────
            ctrl_left, ctrl_right = st.columns([4, 1])

            STATE_ORDER = ["🔥 首發紅K", "🎯 極度壓縮", "⚡ DMI加速", "📊 觀察中"]
            available_states = [s for s in STATE_ORDER if any(r["state"] == s for r in results)]
            with ctrl_left:
                selected_states = st.multiselect(
                    "篩選狀態",
                    options=available_states,
                    default=available_states,
                    help="只影響下方摘要表與卡片顯示，④⑤ 批次抓取仍處理全部通過篩選的標的",
                )
            with ctrl_right:
                top_n = st.number_input(
                    "顯示前 N 名",
                    min_value=1,
                    max_value=max(len(results), 1),
                    value=min(5, len(results)),
                    step=1,
                    help="依評分由高到低，只顯示前 N 檔（不影響 ④⑤⑥⑦ 批次抓取範圍）",
                )

            state_filtered = [r for r in results if r["state"] in selected_states]
            filtered_results = state_filtered[:int(top_n)]

            caption_parts = []
            if len(state_filtered) != len(results):
                caption_parts.append(f"狀態篩選 {len(state_filtered)}/{len(results)} 檔")
            caption_parts.append(f"顯示評分前 **{len(filtered_results)}** 名")
            st.caption("　｜　".join(caption_parts))

            # ── ④⑤⑥ 共用：單檔抓取邏輯 ─────────────────────────────────────────
            def _fetch_eps_for_result(r, params):
                from api.finmind_client import fetch_financial_data
                from db.database import update_ttm_eps, update_gross_margin, get_stock
                from core.valuation import compute_normal_valuation, parse_pe_set
                sid = r["stock_id"]
                data = fetch_financial_data(sid)
                ttm = data["ttm_eps"]
                gm  = data["gross_margin_pct"]
                update_ttm_eps(sid, ttm)
                update_gross_margin(sid, gm)
                r["ttm_eps"] = ttm
                s = get_stock(sid)
                stock_normal_pe = parse_pe_set(s.get("normal_pe_set", ""), params["normal_pe"])
                r["normal_val"] = compute_normal_valuation(ttm, stock_normal_pe)

            def _fetch_revenue_for_result(r, params):
                from api.finmind_client import fetch_and_store_revenue, fetch_shares_issued
                from db.database import update_shares_issued, get_stock, get_ttm_revenue
                from core.valuation import compute_sps, compute_dream_valuation, parse_pe_set
                sid = r["stock_id"]
                fetch_and_store_revenue(sid)
                if r.get("shares_issued") is None:
                    shares = fetch_shares_issued(sid)
                    if shares:
                        update_shares_issued(sid, shares)
                        r["shares_issued"] = shares
                r["ttm_revenue"] = get_ttm_revenue(sid)
                r["sps"] = compute_sps(r.get("shares_issued"), r["ttm_revenue"])
                s = get_stock(sid)
                stock_dream_psr = parse_pe_set(s.get("dream_psr_set", ""), params["dream_psr"])
                r["dream_val"] = compute_dream_valuation(r["sps"], stock_dream_psr)

            # ── ④ 批次抓 TTM EPS ─────────────────────────────────────────────
            if do_fetch_eps:
                prog = st.progress(0, text="準備抓取 EPS...")
                errors = []
                for i, r in enumerate(results):
                    sid = r["stock_id"]
                    prog.progress((i + 1) / len(results),
                                  text=f"抓取 {sid} EPS ({i+1}/{len(results)})")
                    try:
                        _fetch_eps_for_result(r, params)
                    except Exception as e:
                        errors.append(f"{sid}: {e}")
                prog.empty()
                st.session_state["screen_results"] = results
                if errors:
                    st.warning(f"完成（部分失敗）：{'、'.join(errors[:5])}")
                else:
                    st.success(f"EPS 抓取完成！共 {len(results)} 檔")
                st.rerun()

            # ── ⑤ 批次抓月營收+股本 ─────────────────────────────────────────────
            if do_fetch_revenue:
                prog = st.progress(0, text="準備抓取月營收+股本...")
                errors = []
                for i, r in enumerate(results):
                    sid = r["stock_id"]
                    prog.progress((i + 1) / len(results),
                                  text=f"抓取 {sid} 月營收+股本 ({i+1}/{len(results)})")
                    try:
                        _fetch_revenue_for_result(r, params)
                    except Exception as e:
                        errors.append(f"{sid}: {e}")
                prog.empty()
                st.session_state["screen_results"] = results
                if errors:
                    st.warning(f"完成（部分失敗）：{'、'.join(errors[:5])}")
                else:
                    st.success(f"月營收+股本抓取完成！共 {len(results)} 檔")
                st.rerun()

            # ── ⑥ 批次抓法人籌碼 ──────────────────────────────────────────────────
            if do_fetch_chip:
                from api.finmind_client import fetch_institutional_investors
                from core.institutional import compute_institutional_signal, compute_revenue_signal
                from core.scorer import compute_first_ignition_score, score_stock
                from db.database import upsert_institutional_signal
                from collections import Counter
                from datetime import date as _date
                prog = st.progress(0, text="準備抓取法人籌碼...")
                errors = []
                for i, r in enumerate(results):
                    sid = r["stock_id"]
                    prog.progress((i + 1) / len(results),
                                  text=f"抓取 {sid} 法人籌碼 ({i+1}/{len(results)})")
                    try:
                        rows = fetch_institutional_investors(sid, days=30)
                        chip = compute_institutional_signal(
                            rows,
                            abnormal_multiplier=params.get("relay_chip_multiplier", 1.0),
                        )
                        rev  = compute_revenue_signal(
                            sid,
                            yoy_threshold=params.get("relay_yoy_threshold", 15.0),
                        )
                        ignition_score = compute_first_ignition_score(rows, r.get("shares_issued"))
                        upsert_institutional_signal(
                            stock_id             = sid,
                            chip_signal          = int(chip["signal"]),
                            revenue_signal       = int(rev["signal"]),
                            chip_reason          = chip["reason"],
                            revenue_reason       = rev["reason"],
                            checked_date         = _date.today().isoformat(),
                            first_ignition_score = ignition_score,
                        )
                        r["_inst_score"] = ignition_score
                    except Exception as e:
                        errors.append(f"{sid}: {e}")
                prog.empty()
                # 補算評分（加入法人點火分數）
                theme_counts = Counter(t for r in results for t in r.get("themes", []))
                for r in results:
                    r["score"] = score_stock(
                        r["tech"],
                        shares_issued=r.get("shares_issued"),
                        inst_score=r.pop("_inst_score", 0),
                        themes=r.get("themes", []),
                        theme_counts=theme_counts,
                    )
                results.sort(key=lambda x: x["score"]["total"], reverse=True)
                st.session_state["screen_results"] = results
                if errors:
                    st.warning(f"完成（部分失敗）：{'、'.join(errors[:5])}")
                else:
                    st.success(f"法人籌碼抓取完成！共 {len(results)} 檔")
                st.rerun()

            # ── ⑦ 一鍵抓取全部（④EPS + ⑤月營收+股本 + ⑥法人籌碼） ───────────────
            if do_fetch_all:
                from api.finmind_client import fetch_institutional_investors
                from core.institutional import compute_institutional_signal, compute_revenue_signal
                from core.scorer import compute_first_ignition_score, score_stock
                from db.database import upsert_institutional_signal
                from collections import Counter
                from datetime import date as _date
                prog = st.progress(0, text="準備抓取全部資料...")
                errors = []
                for i, r in enumerate(results):
                    sid = r["stock_id"]
                    prog.progress((i + 1) / len(results),
                                  text=f"抓取 {sid} ({i+1}/{len(results)})")
                    try:
                        _fetch_eps_for_result(r, params)
                        _fetch_revenue_for_result(r, params)
                        rows = fetch_institutional_investors(sid, days=30)
                        chip = compute_institutional_signal(
                            rows,
                            abnormal_multiplier=params.get("relay_chip_multiplier", 1.0),
                        )
                        rev  = compute_revenue_signal(
                            sid,
                            yoy_threshold=params.get("relay_yoy_threshold", 15.0),
                        )
                        ignition_score = compute_first_ignition_score(rows, r.get("shares_issued"))
                        upsert_institutional_signal(
                            stock_id             = sid,
                            chip_signal          = int(chip["signal"]),
                            revenue_signal       = int(rev["signal"]),
                            chip_reason          = chip["reason"],
                            revenue_reason       = rev["reason"],
                            checked_date         = _date.today().isoformat(),
                            first_ignition_score = ignition_score,
                        )
                        r["_inst_score"] = ignition_score
                    except Exception as e:
                        errors.append(f"{sid}: {e}")
                prog.empty()
                # 補算評分
                theme_counts = Counter(t for r in results for t in r.get("themes", []))
                for r in results:
                    r["score"] = score_stock(
                        r["tech"],
                        shares_issued=r.get("shares_issued"),
                        inst_score=r.pop("_inst_score", 0),
                        themes=r.get("themes", []),
                        theme_counts=theme_counts,
                    )
                results.sort(key=lambda x: x["score"]["total"], reverse=True)
                st.session_state["screen_results"] = results
                if errors:
                    st.warning(f"完成（部分失敗）：{'、'.join(errors[:5])}")
                else:
                    st.success(f"全部資料抓取完成！共 {len(results)} 檔（EPS + 月營收+股本 + 法人籌碼）")
                st.rerun()

            # 摘要表格
            import pandas as pd
            from db.database import get_revenue_trend, get_institutional_signals_bulk
            inst_signals = get_institutional_signals_bulk(
                [r["stock_id"] for r in filtered_results]
            )

            def _val_price(val, key):
                return f"{val[key]:.1f}" if val else "—"

            def _inst_label(sig: dict | None) -> str:
                if not sig:
                    return "—"
                if sig["chip_signal"] and sig["revenue_signal"]:
                    return "👑 雙訊號"
                if sig["chip_signal"]:
                    return "🔶 法人買超"
                if sig["revenue_signal"]:
                    return "🔷 營收成長"
                return "—"

            summary = pd.DataFrame([{
                "評分":     f'{r["score"]["total"]}　{r["score"]["grade"]}' if r.get("score") else "—",
                "代號":     r["stock_id"],
                "名稱":     r["stock_name"],
                "產業":     r["industry"] or "—",
                "題材":     "、".join(r["themes"]) if r["themes"] else "—",
                "收盤":     r["close"],
                "本日成交量": f'{int(r["volume"] / 1000):,} 張' if r.get("volume") else "—",
                "狀態":     r["state"],
                "法人訊號": _inst_label(inst_signals.get(r["stock_id"])),
                "長趨勢":   "✅" if r["long_trend_ok"] else "❌",
                "TTM EPS":  f'{r["ttm_eps"]:.2f}' if r["ttm_eps"] else "—",
                "支撐價":   _val_price(r.get("normal_val"), "support"),
                "合理價":   _val_price(r.get("normal_val"), "fair"),
                "SPS":      f'{r["sps"]:.2f}' if r.get("sps") else "—",
                # 保留數值型，供 Styler 上色用；CSV 匯出同此
                "PSR合理":  r["dream_val"]["target"]  if r.get("dream_val") else None,
                "PSR天花板": r["dream_val"]["ceiling"] if r.get("dream_val") else None,
            } for r in filtered_results])

            # PSR 欄位依「收盤 vs PSR 價」上色（綠=低於目標/天花板，紅=已超過）
            def _style_psr(row):
                styles = [""] * len(row)
                close = row.get("收盤", None)
                if close is None:
                    return styles
                for col in ("PSR合理", "PSR天花板"):
                    if col not in row.index:
                        continue
                    val = row[col]
                    idx = row.index.get_loc(col)
                    if val is not None and not pd.isna(val):
                        if close < val:
                            styles[idx] = "color: #00cc66; font-weight: 600"
                        else:
                            styles[idx] = "color: #ff4455; font-weight: 600"
                return styles

            styled_summary = (
                summary.style
                .apply(_style_psr, axis=1)
                .format({
                    "收盤":     "{:.2f}",
                    "PSR合理":  lambda x: f"{x:.1f}" if (x is not None and not pd.isna(x)) else "—",
                    "PSR天花板": lambda x: f"{x:.1f}" if (x is not None and not pd.isna(x)) else "—",
                })
            )

            tbl_col, dl_col = st.columns([5, 1])
            tbl_col.dataframe(styled_summary, use_container_width=True, hide_index=True)
            from datetime import date
            dl_col.download_button(
                "📥 匯出 CSV",
                data=summary.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"screener_{date.today():%Y%m%d}.csv",
                mime="text/csv",
            )

            st.divider()
            st.markdown("#### 詳細分析卡片")

            # 為每檔注入月營收 + 法人訊號
            for r in filtered_results:
                r["revenue_trend"] = get_revenue_trend(r["stock_id"])
                r["inst_signal"]   = inst_signals.get(r["stock_id"])
                render_screener_card(r, params)


# ══════════════════════════════════════════════════════════════════════════════
# Tab 2: 追蹤清單
# ══════════════════════════════════════════════════════════════════════════════
with tab_watch:
    from db.database import (get_watchlist, get_latest_close, update_institutional_status,
                             get_price_history, increment_ma20_break_days, reset_ma20_break_days)
    from core.institutional import get_ma20
    from datetime import date as _date

    watchlist = get_watchlist()

    # ── 自動抓取 Watchlist 當日股價（每 session 一次，每檔消耗 1 次配額） ──────────
    if watchlist and token_set and "watchlist_auto_fetched" not in st.session_state:
        from api.finmind_client import fetch_price_history
        for item in watchlist:
            try:
                fetch_price_history(item["stock_id"])
            except Exception:
                pass
        st.session_state["watchlist_auto_fetched"] = True

    if not watchlist:
        st.info("追蹤清單為空。在「選股雷達」或「新增標的」頁面點擊 ❤️ 加入追蹤。")
    else:
        # 計算每檔損益，供後續邏輯判斷
        enriched = []
        for item in watchlist:
            close   = get_latest_close(item["stock_id"]) or item["cost_price"]
            cost    = item["cost_price"] or 1
            pnl_pct = (close - cost) / cost * 100
            enriched.append({**dict(item), "close": close, "pnl_pct": pnl_pct})

        # ── 自動偵測 MA20 出場訊號（本地 K 棒，不消耗配額） ──────────────────────
        # 防洗盤：連續 2 天跌破 MA20 才觸發 exit_signal
        # session_state 防止同一 session 重複累計計數
        _breach_done = st.session_state.get("ma20_breach_checked", set())
        for item in enriched:
            if item.get("institutional_status") == "relay_locked" and item.get("ma20_guard_active"):
                sid  = item["stock_id"]
                ph   = get_price_history(sid, n=60)
                ma20 = get_ma20(ph)
                if ma20:
                    if item["close"] >= ma20:
                        if item.get("ma20_break_days", 0) > 0:
                            reset_ma20_break_days(sid)
                            item["ma20_break_days"] = 0
                    elif sid not in _breach_done:
                        new_days = increment_ma20_break_days(sid)
                        item["ma20_break_days"] = new_days
                        _breach_done.add(sid)
                        if new_days >= 2:
                            update_institutional_status(
                                sid, "exit_signal",
                                check_date=_date.today().isoformat(),
                                ma20_guard=0,
                            )
                            item["institutional_status"] = "exit_signal"
        st.session_state["ma20_breach_checked"] = _breach_done

        # ── 出場警示摘要 ────────────────────────────────────────────────────────
        exit_items = [i for i in enriched if i.get("institutional_status") == "exit_signal"]
        if exit_items:
            sids = "、".join(i["stock_id"] for i in exit_items)
            st.error(f"🚨 **{len(exit_items)} 檔**跌破 MA20 防守線，建議確認出場：{sids}")

        # ── 待法人驗證（達停利門檻，尚未驗證） ──────────────────────────────────
        pending = [
            i for i in enriched
            if i.get("institutional_status") is None
            and i["pnl_pct"] >= params["stop_profit_pct"]
        ]
        if pending:
            cols = st.columns([3, 1])
            cols[0].warning(
                f"⚠️ **{len(pending)} 檔**達停利 +{params['stop_profit_pct']}%，"
                f"尚未進行法人籌碼驗證"
            )
            do_verify = cols[1].button(
                f"🔍 法人驗證（{len(pending)} 檔）",
                disabled=not token_set,
                help="每檔消耗 1 次 API 配額（TaiwanStockInstitutionalInvestors）",
            )
            if do_verify:
                from api.finmind_client import fetch_institutional_investors
                from core.institutional import check_relay_condition
                prog = st.progress(0)
                for i, item in enumerate(pending):
                    sid = item["stock_id"]
                    prog.progress((i + 1) / len(pending), text=f"驗證 {sid}...")
                    try:
                        rows   = fetch_institutional_investors(sid, days=30)
                        result = check_relay_condition(
                            sid, rows,
                            yoy_threshold=params.get("relay_yoy_threshold", 15.0),
                            abnormal_multiplier=params.get("relay_chip_multiplier", 1.0),
                        )
                        if result["passed"]:
                            update_institutional_status(
                                sid, "relay_locked",
                                check_date=_date.today().isoformat(),
                                ma20_guard=1,
                            )
                            item["institutional_status"] = "relay_locked"
                            st.success(
                                f"✅ {sid} 通過法人驗證 → 👑 法人波段認養｜"
                                f"籌碼：{result['chip']['reason']}｜"
                                f"營收：{result['revenue']['reason']}"
                            )
                        else:
                            st.info(
                                f"ℹ️ {sid} 未達法人接力標準｜"
                                f"籌碼：{result['chip']['reason']}｜"
                                f"營收：{result['revenue']['reason']}"
                            )
                    except Exception as e:
                        st.error(f"{sid} 驗證失敗：{e}")
                prog.empty()
                st.rerun()

        st.markdown(
            f"追蹤中 **{len(watchlist)}** 檔｜"
            f"停損：**{params['stop_loss_pct']}%**｜"
            f"停利：**{params['stop_profit_pct']}%**"
        )
        st.divider()
        WATCHLIST_GRID_COLS = 3
        grid_cols = st.columns(WATCHLIST_GRID_COLS)
        for i, item in enumerate(enriched):
            with grid_cols[i % WATCHLIST_GRID_COLS]:
                render_watchlist_card(item, params)

        if st.button("🔄 更新所有追蹤標的最新行情"):
            if not token_set:
                st.error("請先設定 FINMIND_TOKEN")
            else:
                from api.finmind_client import fetch_price_history
                prog = st.progress(0)
                for i, item in enumerate(watchlist):
                    try:
                        fetch_price_history(item["stock_id"])
                    except Exception:
                        pass
                    prog.progress((i + 1) / len(watchlist))
                prog.empty()
                st.success("行情更新完成！")
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Tab 3: 新增標的
# ══════════════════════════════════════════════════════════════════════════════
with tab_add:
    st.markdown("#### 新增標的至選股池")

    with st.form("add_stock_form"):
        c1, c2 = st.columns(2)
        stock_id_input = c1.text_input("股票代號", placeholder="e.g. 6275")
        industry_input = c2.text_input("產業標籤（選填）", placeholder="e.g. AI液冷, 車用")

        ttm_eps_input = st.number_input("TTM EPS（留空則自動抓取）",
                                        value=0.0, step=0.1, format="%.2f")

        st.markdown("##### 個股本益比 / PSR 設定（選填，留空用全域設定）")
        d1, d2, d3 = st.columns(3)
        ind_normal_low  = d1.number_input("常態低",  value=0.0, step=1.0)
        ind_normal_mid  = d2.number_input("常態中",  value=0.0, step=1.0)
        ind_normal_high = d3.number_input("常態高",  value=0.0, step=1.0)

        e1, e2, e3 = st.columns(3)
        ind_dream_low   = e1.number_input("起跑PSR",  value=0.0, step=0.1)
        ind_dream_mid   = e2.number_input("目標PSR",  value=0.0, step=0.1)
        ind_dream_high  = e3.number_input("天花板PSR", value=0.0, step=0.1)

        submitted = st.form_submit_button("➕ 新增 / 更新", type="primary")

    if submitted and stock_id_input.strip():
        sid = stock_id_input.strip()
        with st.spinner(f"處理 {sid}..."):
            try:
                from db.database import upsert_stock, update_pe_sets, update_ttm_eps
                from api.finmind_client import fetch_price_history

                # 抓股票基本資料
                if token_set:
                    from api.finmind_client import fetch_stock_info
                    fetch_stock_info(sid)

                # 處理 TTM EPS
                actual_ttm = ttm_eps_input if ttm_eps_input != 0.0 else None
                if actual_ttm is None and token_set:
                    from api.finmind_client import fetch_ttm_eps
                    actual_ttm = fetch_ttm_eps(sid)
                    st.info(f"自動抓取 TTM EPS = {actual_ttm:.2f}")

                upsert_stock(
                    sid,
                    industry_category=industry_input,
                    ttm_eps=actual_ttm,
                )

                # 個股 PE / PSR 設定（留空＝沿用全域設定，不寫入該欄位）
                if any([ind_normal_low, ind_normal_mid, ind_normal_high]):
                    update_pe_sets(sid, normal_pe=[ind_normal_low, ind_normal_mid, ind_normal_high])
                if any([ind_dream_low, ind_dream_mid, ind_dream_high]):
                    update_pe_sets(sid, dream_psr=[ind_dream_low, ind_dream_mid, ind_dream_high])

                # 抓歷史 K 棒
                if token_set:
                    records = fetch_price_history(sid)
                    st.info(f"已更新 {len(records)} 筆 K 棒資料")

                st.success(f"✅ {sid} 已加入選股池！")

            except Exception as e:
                st.error(f"新增失敗：{e}")

    # ── 即時估值預覽 ───────────────────────────────────────────────────────────
    st.divider()
    st.markdown("#### 即時估值矩陣預覽")
    preview_id = st.text_input("輸入代號預覽估值", placeholder="e.g. 6275", key="preview_id")

    if preview_id.strip():
        from core.screener import analyze_single
        analysis = analyze_single(
            preview_id.strip(),
            normal_pe=params["normal_pe"],
            dream_psr=params["dream_psr"],
        )
        if analysis is None:
            st.warning(f"{preview_id} 不在選股池中，請先新增。")
        else:
            close = analysis["close"] or 0
            st.markdown(f"**{analysis['stock_id']} {analysis['stock_name']}**"
                        f"　收盤 {close:.2f} 元　{analysis['state']}")

            render_valuation_matrix(close, analysis["normal_val"], analysis["dream_val"])

            if analysis["tech"]["enough_data"]:
                st.markdown("##### 技術指標")
                t = analysis["tech"]
                tm1, tm2, tm3, tm4, tm5 = st.columns(5)
                tm1.metric("MA20",  f'{t["ma20"]:.1f}'  if t["ma20"]  else "N/A")
                tm2.metric("MA60",  f'{t["ma60"]:.1f}'  if t["ma60"]  else "N/A")
                tm3.metric("MA120", f'{t["ma120"]:.1f}' if t["ma120"] else "N/A")
                tm4.metric("MA240", f'{t["ma240"]:.1f}' if t["ma240"] else "N/A")
                tm5.metric("長趨勢保護", "✅ 通過" if t["long_trend_ok"] else "❌ 未過")

            if analysis["close"]:
                if st.button(f"❤️ 加入追蹤 {preview_id.strip()}", key="watch_preview"):
                    from db.database import add_watchlist
                    add_watchlist(preview_id.strip(), analysis["close"])
                    st.success(f"已加入追蹤，成本基準：{analysis['close']:.2f} 元")
                    st.rerun()
