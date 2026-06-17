import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "stock_system.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_schema(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(stock_pool)").fetchall()}
    if "shares_issued" not in cols:
        conn.execute("ALTER TABLE stock_pool ADD COLUMN shares_issued INTEGER")
    if "future_dream_eps" in cols:
        conn.execute("ALTER TABLE stock_pool DROP COLUMN future_dream_eps")
    if "dream_pe_set" in cols:
        conn.execute("ALTER TABLE stock_pool DROP COLUMN dream_pe_set")
    if "dream_psr_set" not in cols:
        conn.execute(
            "ALTER TABLE stock_pool ADD COLUMN dream_psr_set TEXT DEFAULT '[1.2, 1.8, 2.5]'"
        )
    if "themes" not in cols:
        conn.execute("ALTER TABLE stock_pool ADD COLUMN themes TEXT DEFAULT '[]'")
    if "last_update_attempt" not in cols:
        conn.execute("ALTER TABLE stock_pool ADD COLUMN last_update_attempt TEXT DEFAULT NULL")
    if "last_price_attempt" not in cols:
        conn.execute("ALTER TABLE stock_pool ADD COLUMN last_price_attempt TEXT DEFAULT NULL")

    # 舊版欄位預設值是硬編碼的 JSON 字串，導致 parse_pe_set() 永遠命中「個股設定」，
    # Sidebar 全域 PSR/PE 設定形同無效。改為 NULL = 未自訂，交給 parse_pe_set() fallback 到全域設定。
    conn.execute("UPDATE stock_pool SET normal_pe_set = NULL WHERE normal_pe_set = '[12, 15, 18]'")
    conn.execute("UPDATE stock_pool SET dream_psr_set = NULL WHERE dream_psr_set = '[1.2, 1.8, 2.5]'")

    # institutional_signals：補 first_ignition_score 欄位
    inst_cols = {row["name"] for row in conn.execute("PRAGMA table_info(institutional_signals)").fetchall()}
    if "first_ignition_score" not in inst_cols:
        conn.execute(
            "ALTER TABLE institutional_signals ADD COLUMN first_ignition_score INTEGER DEFAULT 0"
        )

    # watchlist：法人籌碼接力鎖模組新欄位
    watch_cols = {row["name"] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    if "institutional_status" not in watch_cols:
        conn.execute("ALTER TABLE watchlist ADD COLUMN institutional_status TEXT DEFAULT NULL")
    if "institutional_check_date" not in watch_cols:
        conn.execute("ALTER TABLE watchlist ADD COLUMN institutional_check_date TEXT DEFAULT NULL")
    if "ma20_guard_active" not in watch_cols:
        conn.execute("ALTER TABLE watchlist ADD COLUMN ma20_guard_active INTEGER DEFAULT 0")
    if "ma20_break_days" not in watch_cols:
        conn.execute("ALTER TABLE watchlist ADD COLUMN ma20_break_days INTEGER DEFAULT 0")

    # stock_pool：毛利率欄位
    if "gross_margin_pct" not in cols:
        conn.execute("ALTER TABLE stock_pool ADD COLUMN gross_margin_pct REAL DEFAULT NULL")

    # monthly_revenue.revenue：REAL → INTEGER（避免大數浮點誤差影響 SPS 計算）
    rev_col_types = {c["name"]: c["type"] for c in conn.execute("PRAGMA table_info(monthly_revenue)").fetchall()}
    if rev_col_types.get("revenue", "").upper() == "REAL":
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_revenue_v2 (
                stock_id      TEXT,
                revenue_year  INTEGER,
                revenue_month INTEGER,
                revenue       INTEGER,
                PRIMARY KEY (stock_id, revenue_year, revenue_month)
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO monthly_revenue_v2
                SELECT stock_id, revenue_year, revenue_month, CAST(revenue AS INTEGER)
                FROM monthly_revenue
        """)
        conn.execute("DROP TABLE monthly_revenue")
        conn.execute("ALTER TABLE monthly_revenue_v2 RENAME TO monthly_revenue")


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stock_pool (
                stock_id          TEXT PRIMARY KEY,
                stock_name        TEXT DEFAULT '',
                industry_category TEXT DEFAULT '',
                ttm_eps           REAL,
                shares_issued     INTEGER,
                normal_pe_set     TEXT DEFAULT NULL,
                dream_psr_set     TEXT DEFAULT NULL,
                themes            TEXT DEFAULT '[]',
                gross_margin_pct  REAL DEFAULT NULL,
                last_update_attempt TEXT DEFAULT NULL,
                last_price_attempt  TEXT DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_prices (
                stock_id TEXT,
                date     TEXT,
                open     REAL,
                close    REAL,
                high     REAL,
                low      REAL,
                volume   INTEGER,
                PRIMARY KEY (stock_id, date)
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                stock_id                 TEXT PRIMARY KEY,
                cost_price               REAL,
                tracked_at               TEXT,
                institutional_status     TEXT DEFAULT NULL,
                institutional_check_date TEXT DEFAULT NULL,
                ma20_guard_active        INTEGER DEFAULT 0,
                ma20_break_days          INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS monthly_revenue (
                stock_id      TEXT,
                revenue_year  INTEGER,
                revenue_month INTEGER,
                revenue       REAL,
                PRIMARY KEY (stock_id, revenue_year, revenue_month)
            );

            CREATE TABLE IF NOT EXISTS user_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS institutional_signals (
                stock_id             TEXT PRIMARY KEY,
                chip_signal          INTEGER DEFAULT 0,
                revenue_signal       INTEGER DEFAULT 0,
                chip_reason          TEXT DEFAULT '',
                revenue_reason       TEXT DEFAULT '',
                checked_date         TEXT,
                first_ignition_score INTEGER DEFAULT 0
            );
        """)
        _migrate_schema(conn)


# ── user_settings ─────────────────────────────────────────────────────────────

def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM user_settings WHERE key=?", (key,)
        ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return default


def set_setting(key: str, value):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO user_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, json.dumps(value, ensure_ascii=False)))


# ── institutional_signals ─────────────────────────────────────────────────────

def upsert_institutional_signal(stock_id: str, chip_signal: int, revenue_signal: int,
                                chip_reason: str, revenue_reason: str, checked_date: str,
                                first_ignition_score: int = 0):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO institutional_signals
                (stock_id, chip_signal, revenue_signal, chip_reason, revenue_reason,
                 checked_date, first_ignition_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_id) DO UPDATE SET
                chip_signal          = excluded.chip_signal,
                revenue_signal       = excluded.revenue_signal,
                chip_reason          = excluded.chip_reason,
                revenue_reason       = excluded.revenue_reason,
                checked_date         = excluded.checked_date,
                first_ignition_score = excluded.first_ignition_score
        """, (stock_id, chip_signal, revenue_signal, chip_reason, revenue_reason,
               checked_date, first_ignition_score))


def get_institutional_signals_bulk(stock_ids: list[str]) -> dict[str, dict]:
    """批次取得多檔的法人訊號，回傳 {stock_id: signal_dict}。"""
    if not stock_ids:
        return {}
    placeholders = ",".join("?" * len(stock_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM institutional_signals WHERE stock_id IN ({placeholders})",
            stock_ids,
        ).fetchall()
    return {dict(r)["stock_id"]: dict(r) for r in rows}


# ── stock_pool ────────────────────────────────────────────────────────────────

def upsert_stock(stock_id, stock_name=None, industry_category=None, ttm_eps=None):
    """stock_name/industry_category/ttm_eps 留 None 代表不覆寫既有值（COALESCE 對空字串無效，必須是 NULL）。"""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO stock_pool (stock_id, stock_name, industry_category, ttm_eps,
                                     normal_pe_set, dream_psr_set)
            VALUES (?, ?, ?, ?, NULL, NULL)
            ON CONFLICT(stock_id) DO UPDATE SET
                stock_name        = COALESCE(excluded.stock_name, stock_pool.stock_name),
                industry_category = COALESCE(excluded.industry_category,
                                             stock_pool.industry_category),
                ttm_eps           = COALESCE(excluded.ttm_eps, stock_pool.ttm_eps)
        """, (stock_id, stock_name, industry_category, ttm_eps))


def update_pe_sets(stock_id, normal_pe: list = None, dream_psr: list = None):
    """更新個股 PE/PSR 自訂設定，僅更新有傳入的欄位。傳 None 代表沿用全域設定。"""
    sets, vals = [], []
    if normal_pe is not None:
        sets.append("normal_pe_set=?")
        vals.append(json.dumps(normal_pe))
    if dream_psr is not None:
        sets.append("dream_psr_set=?")
        vals.append(json.dumps(dream_psr))
    if not sets:
        return
    vals.append(stock_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE stock_pool SET {', '.join(sets)} WHERE stock_id=?", vals)


def update_themes(stock_id, themes: list[str]):
    with get_conn() as conn:
        conn.execute(
            "UPDATE stock_pool SET themes=? WHERE stock_id=?",
            (json.dumps(themes, ensure_ascii=False), stock_id)
        )


def get_all_themes() -> list[str]:
    """掃描所有股票的題材標籤，回傳去重排序後的標籤清單。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT themes FROM stock_pool WHERE themes IS NOT NULL AND themes != '[]'"
        ).fetchall()
    tags = set()
    for r in rows:
        try:
            for t in json.loads(r["themes"]):
                if t:
                    tags.add(t)
        except (json.JSONDecodeError, TypeError):
            continue
    return sorted(tags)


def get_industry_categories() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT industry_category FROM stock_pool "
            "WHERE industry_category != '' ORDER BY industry_category"
        ).fetchall()
    return [r["industry_category"] for r in rows]


def update_shares_issued(stock_id, shares: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE stock_pool SET shares_issued=? WHERE stock_id=?",
            (shares, stock_id)
        )


def update_ttm_eps(stock_id, ttm_eps: float):
    with get_conn() as conn:
        conn.execute(
            "UPDATE stock_pool SET ttm_eps=? WHERE stock_id=?",
            (ttm_eps, stock_id)
        )


def get_stock(stock_id) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM stock_pool WHERE stock_id=?", (stock_id,)
        ).fetchone()
    return dict(row) if row else None


def get_all_stocks() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM stock_pool ORDER BY stock_id").fetchall()
    return [dict(r) for r in rows]


def delete_stock(stock_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM stock_pool WHERE stock_id=?", (stock_id,))
        conn.execute("DELETE FROM daily_prices WHERE stock_id=?", (stock_id,))
        conn.execute("DELETE FROM watchlist WHERE stock_id=?", (stock_id,))


# ── daily_prices ──────────────────────────────────────────────────────────────

def upsert_prices(records: list[tuple]):
    """records: list of (stock_id, date, open, close, high, low, volume)"""
    with get_conn() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO daily_prices
                (stock_id, date, open, close, high, low, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, records)


def get_price_coverage() -> dict[str, str]:
    """回傳 {stock_id: 最新日期}，沒有資料的股票不在 dict 內。"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT stock_id, MAX(date) AS latest
            FROM daily_prices GROUP BY stock_id
        """).fetchall()
    return {r["stock_id"]: r["latest"] for r in rows}


def get_latest_date(stock_id) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM daily_prices WHERE stock_id=?", (stock_id,)
        ).fetchone()
    return row["d"] if row and row["d"] else None


def get_price_history(stock_id, n: int = 260) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT date, open, close, high, low, volume
            FROM daily_prices WHERE stock_id=?
            ORDER BY date DESC LIMIT ?
        """, (stock_id, n)).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_latest_close(stock_id) -> float | None:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT close FROM daily_prices
            WHERE stock_id=? ORDER BY date DESC LIMIT 1
        """, (stock_id,)).fetchone()
    return row["close"] if row else None


# ── watchlist ─────────────────────────────────────────────────────────────────

def add_watchlist(stock_id: str, cost_price: float):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO watchlist (stock_id, cost_price, tracked_at)
            VALUES (?, ?, ?)
        """, (stock_id, cost_price, datetime.now().isoformat()))


def remove_watchlist(stock_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE stock_id=?", (stock_id,))


def get_watchlist() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM watchlist ORDER BY tracked_at DESC").fetchall()
    return [dict(r) for r in rows]


def update_institutional_status(stock_id: str, status: str | None,
                                check_date: str = None, ma20_guard: int = None):
    """更新 watchlist 中的法人籌碼接力鎖狀態。status=None 代表重置為未驗證。"""
    sets = ["institutional_status=?"]
    vals = [status]
    if check_date is not None:
        sets.append("institutional_check_date=?")
        vals.append(check_date)
    if ma20_guard is not None:
        sets.append("ma20_guard_active=?")
        vals.append(ma20_guard)
    vals.append(stock_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE watchlist SET {', '.join(sets)} WHERE stock_id=?", vals)


def is_in_watchlist(stock_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE stock_id=?", (stock_id,)
        ).fetchone()
    return row is not None


# ── monthly_revenue ───────────────────────────────────────────────────────────

def upsert_revenue(records: list[tuple]):
    """records: list of (stock_id, revenue_year, revenue_month, revenue)"""
    with get_conn() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO monthly_revenue
                (stock_id, revenue_year, revenue_month, revenue)
            VALUES (?, ?, ?, ?)
        """, records)


def get_revenue_trend(stock_id: str, n: int = 13) -> list[dict]:
    """取得最近 n 個月營收，由舊到新排列。"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT revenue_year, revenue_month, revenue
            FROM monthly_revenue
            WHERE stock_id=?
            ORDER BY revenue_year DESC, revenue_month DESC
            LIMIT ?
        """, (stock_id, n)).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_ttm_revenue(stock_id: str) -> float | None:
    """近 12 個月營收總和（TTM revenue）。資料不足 12 個月則回傳 None。"""
    trend = get_revenue_trend(stock_id, n=12)
    if len(trend) < 12:
        return None
    return sum(float(r["revenue"] or 0) for r in trend)


def get_revenue_coverage() -> set[str]:
    """回傳已有月營收資料的 stock_id 集合。"""
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT stock_id FROM monthly_revenue").fetchall()
    return {r["stock_id"] for r in rows}


# ── gross_margin ──────────────────────────────────────────────────────────────

def update_gross_margin(stock_id: str, pct: float | None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE stock_pool SET gross_margin_pct=? WHERE stock_id=?",
            (pct, stock_id)
        )


# ── MA20 跌破計數（防洗盤：連續 2 天才觸發出場） ──────────────────────────────

def increment_ma20_break_days(stock_id: str) -> int:
    """跌破 MA20 計數 +1，回傳新計數。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE watchlist SET ma20_break_days = ma20_break_days + 1 WHERE stock_id=?",
            (stock_id,)
        )
        row = conn.execute(
            "SELECT ma20_break_days FROM watchlist WHERE stock_id=?", (stock_id,)
        ).fetchone()
    return row["ma20_break_days"] if row else 1


def reset_ma20_break_days(stock_id: str):
    """站回 MA20 之上，清零跌破計數。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE watchlist SET ma20_break_days=0 WHERE stock_id=?",
            (stock_id,)
        )


# ── Update Tracking ─────────────────────────────────────────────────────────────

def update_last_update_attempt(stock_id: str):
    """記錄深度財務資料（EPS/月營收/股本）嘗試時間，供 Phase0 20小時冷卻判斷。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE stock_pool SET last_update_attempt=? WHERE stock_id=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), stock_id)
        )


def update_last_price_attempt(stock_id: str):
    """記錄K棒價格嘗試時間，獨立於財務資料冷卻，避免兩個Phase互相干擾。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE stock_pool SET last_price_attempt=? WHERE stock_id=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), stock_id)
        )


def get_active_stock_ids(days: int = 30) -> list[str]:
    """取得最近 n 天內有成交量記錄的股票代號清單（視為活躍股票）。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT stock_id FROM daily_prices WHERE date >= ? AND volume > 0",
            (cutoff,)
        ).fetchall()
    return [r["stock_id"] for r in rows]
