"""SQLite database for congress trader."""

import sqlite3
import config


def get_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS disclosures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            politician_name TEXT NOT NULL,
            ticker TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            filing_date TEXT NOT NULL,
            amount_min REAL DEFAULT 0,
            amount_max REAL DEFAULT 0,
            tx_type TEXT DEFAULT 'purchase',
            chamber TEXT DEFAULT '',
            party TEXT DEFAULT '',
            state TEXT DEFAULT '',
            reporting_delay_days INTEGER DEFAULT 0,
            price_at_trade REAL DEFAULT 0,
            price_at_filing REAL DEFAULT 0,
            price_change_pct REAL DEFAULT 0,
            risk_score INTEGER DEFAULT 0,
            source_url TEXT DEFAULT '',
            raw_json TEXT DEFAULT '{}',
            processed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(politician_name, ticker, trade_date, tx_type)
        );

        CREATE TABLE IF NOT EXISTS politicians (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            party TEXT DEFAULT '',
            state TEXT DEFAULT '',
            chamber TEXT DEFAULT '',
            total_trades INTEGER DEFAULT 0,
            winning_trades INTEGER DEFAULT 0,
            avg_return_pct REAL DEFAULT 0,
            avg_reporting_delay_days REAL DEFAULT 0,
            last_trade_date TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disclosure_id INTEGER,
            ticker TEXT NOT NULL,
            politician_name TEXT DEFAULT '',
            entry_price REAL DEFAULT 0,
            entry_date TEXT DEFAULT '',
            quantity REAL DEFAULT 0,
            cost_basis REAL DEFAULT 0,
            stop_loss_price REAL DEFAULT 0,
            target_exit_date TEXT DEFAULT '',
            exit_price REAL DEFAULT 0,
            exit_date TEXT DEFAULT '',
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending','open','closed','stopped','cancelled')),
            pnl_dollars REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            paper_or_live TEXT DEFAULT 'paper',
            alpaca_order_id TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (disclosure_id) REFERENCES disclosures(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_value REAL DEFAULT 0,
            cash REAL DEFAULT 0,
            invested REAL DEFAULT 0,
            daily_pnl REAL DEFAULT 0,
            sp500_value REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_disclosures_filing ON disclosures(filing_date DESC);
        CREATE INDEX IF NOT EXISTS idx_disclosures_ticker ON disclosures(ticker);
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
    """)
    conn.close()


# ── Settings helpers ──

def get_setting(key, default=""):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


# ── Disclosure helpers ──

def insert_disclosure(data):
    conn = get_db()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO disclosures
            (politician_name, ticker, trade_date, filing_date, amount_min, amount_max,
             tx_type, chamber, party, state, reporting_delay_days, price_at_trade,
             price_at_filing, price_change_pct, risk_score, source_url, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data["politician_name"], data["ticker"], data["trade_date"],
            data["filing_date"], data.get("amount_min", 0), data.get("amount_max", 0),
            data.get("tx_type", "purchase"), data.get("chamber", ""),
            data.get("party", ""), data.get("state", ""),
            data.get("reporting_delay_days", 0), data.get("price_at_trade", 0),
            data.get("price_at_filing", 0), data.get("price_change_pct", 0),
            data.get("risk_score", 0), data.get("source_url", ""),
            data.get("raw_json", "{}"),
        ))
        conn.commit()
        inserted = conn.execute(
            "SELECT id FROM disclosures WHERE politician_name=? AND ticker=? AND trade_date=? AND tx_type=?",
            (data["politician_name"], data["ticker"], data["trade_date"], data.get("tx_type", "purchase")),
        ).fetchone()
        conn.close()
        return inserted["id"] if inserted else None
    except Exception:
        conn.close()
        return None


def get_disclosures(limit=50, offset=0, tx_type=None, min_score=0):
    conn = get_db()
    query = "SELECT * FROM disclosures WHERE risk_score >= ?"
    params = [min_score]
    if tx_type:
        query += " AND tx_type = ?"
        params.append(tx_type)
    query += " ORDER BY filing_date DESC, created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_disclosure(disclosure_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM disclosures WHERE id=?", (disclosure_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_disclosure_score(disclosure_id, score, price_at_trade=0, price_at_filing=0, price_change_pct=0):
    conn = get_db()
    conn.execute("""
        UPDATE disclosures SET risk_score=?, price_at_trade=?, price_at_filing=?, price_change_pct=?, processed=1
        WHERE id=?
    """, (score, price_at_trade, price_at_filing, price_change_pct, disclosure_id))
    conn.commit()
    conn.close()


# ── Politician helpers ──

def upsert_politician(name, party="", state="", chamber=""):
    conn = get_db()
    conn.execute("""
        INSERT INTO politicians (name, party, state, chamber)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            party=COALESCE(NULLIF(excluded.party,''), politicians.party),
            state=COALESCE(NULLIF(excluded.state,''), politicians.state),
            chamber=COALESCE(NULLIF(excluded.chamber,''), politicians.chamber),
            updated_at=CURRENT_TIMESTAMP
    """, (name, party, state, chamber))
    conn.commit()
    conn.close()


def get_politicians(limit=50, order_by="winning_trades DESC"):
    conn = get_db()
    rows = conn.execute(
        f"SELECT * FROM politicians ORDER BY {order_by} LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_politician_stats(name):
    conn = get_db()
    stats = conn.execute("""
        SELECT COUNT(*) as total,
               AVG(reporting_delay_days) as avg_delay
        FROM disclosures WHERE politician_name=?
    """, (name,)).fetchone()

    trade_stats = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               AVG(pnl_pct) as avg_return
        FROM trades WHERE politician_name=? AND status='closed'
    """, (name,)).fetchone()

    conn.execute("""
        UPDATE politicians SET
            total_trades=?, avg_reporting_delay_days=?,
            winning_trades=?, avg_return_pct=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE name=?
    """, (
        stats["total"] or 0, round(stats["avg_delay"] or 0, 1),
        trade_stats["wins"] or 0, round(trade_stats["avg_return"] or 0, 2),
        name,
    ))
    conn.commit()
    conn.close()


# ── Trade helpers ──

def insert_trade(data):
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO trades
        (disclosure_id, ticker, politician_name, entry_price, entry_date, quantity,
         cost_basis, stop_loss_price, target_exit_date, status, paper_or_live)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.get("disclosure_id"), data["ticker"], data.get("politician_name", ""),
        data.get("entry_price", 0), data.get("entry_date", ""),
        data.get("quantity", 0), data.get("cost_basis", 0),
        data.get("stop_loss_price", 0), data.get("target_exit_date", ""),
        data.get("status", "pending"), data.get("paper_or_live", "paper"),
    ))
    conn.commit()
    trade_id = cur.lastrowid
    conn.close()
    return trade_id


def get_trades(status=None, limit=50):
    conn = get_db()
    if status:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_trade(trade_id, **kwargs):
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [trade_id]
    conn.execute(f"UPDATE trades SET {sets} WHERE id=?", values)
    conn.commit()
    conn.close()


# ── Portfolio snapshot ──

def insert_snapshot(data):
    conn = get_db()
    conn.execute("""
        INSERT INTO portfolio_snapshots (snapshot_date, total_value, cash, invested, daily_pnl, sp500_value)
        VALUES (?,?,?,?,?,?)
    """, (
        data["snapshot_date"], data.get("total_value", 0), data.get("cash", 0),
        data.get("invested", 0), data.get("daily_pnl", 0), data.get("sp500_value", 0),
    ))
    conn.commit()
    conn.close()


def get_snapshots(days=90):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM portfolio_snapshots
        ORDER BY snapshot_date DESC LIMIT ?
    """, (days,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
