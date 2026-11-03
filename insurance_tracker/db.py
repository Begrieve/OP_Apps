"""SQLite database setup and helpers for the insurance tracker."""
import sqlite3
from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    alt_phone TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    dob TEXT,
    preferred_contact TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    policy_number TEXT,
    policy_type TEXT NOT NULL,
    carrier TEXT,
    status TEXT NOT NULL DEFAULT 'Active',
    effective_date TEXT,
    expiration_date TEXT,
    premium REAL,
    payment_frequency TEXT,
    next_due_date TEXT,
    payment_method TEXT,
    coverage_amount REAL,
    deductible REAL,
    commission_rate REAL,
    insured_item TEXT,
    beneficiaries TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    paid_on TEXT NOT NULL,
    amount REAL NOT NULL,
    method TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    meeting_type TEXT,
    date TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    location TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    due_date TEXT NOT NULL,
    client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    policy_id INTEGER REFERENCES policies(id) ON DELETE CASCADE,
    notes TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS commissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER REFERENCES policies(id) ON DELETE SET NULL,
    client_name TEXT,
    statement_month TEXT,
    invoice_no TEXT UNIQUE,
    class_code TEXT,
    invoice_date TEXT,
    term_from TEXT,
    term_to TEXT,
    rate REAL,
    amount REAL NOT NULL,
    producer_code TEXT,
    source_file TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_commissions_policy ON commissions(policy_id);
CREATE INDEX IF NOT EXISTS idx_policies_client ON policies(client_id);
CREATE INDEX IF NOT EXISTS idx_policies_due ON policies(next_due_date);
CREATE INDEX IF NOT EXISTS idx_payments_policy ON payments(policy_id);
CREATE INDEX IF NOT EXISTS idx_meetings_date ON meetings(date);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_date);
"""


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# Columns added after the first release; existing databases get them on start-up.
MIGRATIONS = {"commissions": [("premium", "REAL"), ("inv_type", "TEXT")]}


def init_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, ctype in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ctype}")
    conn.commit()
    conn.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    conn = get_db()
    cur = conn.execute(sql, args)
    if not g.get("in_transaction"):
        conn.commit()
    return cur.lastrowid


class transaction:
    """Group many writes so they are saved together, or not at all if anything fails."""

    def __enter__(self):
        get_db()
        g.in_transaction = True
        return self

    def __exit__(self, exc_type, _exc, _tb):
        g.in_transaction = False
        conn = get_db()
        if exc_type is None:
            conn.commit()
        else:
            conn.rollback()
        return False
