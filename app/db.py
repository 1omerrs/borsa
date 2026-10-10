"""SQLite storage for instruments, quotes, alerts, and reports."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager

from app.config import db_path, now_iso

DEFAULT_INSTRUMENTS = (
    {"symbol": "XU100.IS", "name": "BIST 100", "category": "endeks", "currency": "TRY", "decimals": 2},
    {"symbol": "GC=F", "name": "Ons Altın", "category": "altin", "currency": "USD", "decimals": 2},
    {"symbol": "GRAM.ALTIN", "name": "Gram Altın (spot)", "category": "altin", "currency": "TRY", "decimals": 2, "computed": True},
    {"symbol": "TRY=X", "name": "Dolar/TL", "category": "doviz", "currency": "TRY", "decimals": 4},
    {"symbol": "EURTRY=X", "name": "Euro/TL", "category": "doviz", "currency": "TRY", "decimals": 4},
    {"symbol": "THYAO.IS", "name": "Türk Hava Yolları", "category": "hisse", "currency": "TRY", "decimals": 2},
    {"symbol": "GARAN.IS", "name": "Garanti BBVA", "category": "hisse", "currency": "TRY", "decimals": 2},
    {"symbol": "AKBNK.IS", "name": "Akbank", "category": "hisse", "currency": "TRY", "decimals": 2},
    {"symbol": "ASELS.IS", "name": "Aselsan", "category": "hisse", "currency": "TRY", "decimals": 2},
    {"symbol": "KCHOL.IS", "name": "Koç Holding", "category": "hisse", "currency": "TRY", "decimals": 2},
    {"symbol": "SISE.IS", "name": "Şişecam", "category": "hisse", "currency": "TRY", "decimals": 2},
    {"symbol": "EREGL.IS", "name": "Ereğli Demir Çelik", "category": "hisse", "currency": "TRY", "decimals": 2},
    {"symbol": "BIMAS.IS", "name": "BİM", "category": "hisse", "currency": "TRY", "decimals": 2},
    {"symbol": "TUPRS.IS", "name": "Tüpraş", "category": "hisse", "currency": "TRY", "decimals": 2},
    {"symbol": "BTC-USD", "name": "Bitcoin", "category": "kripto", "currency": "USD", "decimals": 2},
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    currency TEXT NOT NULL,
    decimals INTEGER NOT NULL DEFAULT 2,
    computed INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    symbol TEXT PRIMARY KEY REFERENCES instruments(symbol),
    price REAL,
    previous_close REAL,
    change_amount REAL,
    change_percent REAL,
    change_amount_7d REAL,
    change_percent_7d REAL,
    volume REAL,
    market_time TEXT,
    source TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS history (
    symbol TEXT NOT NULL REFERENCES instruments(symbol),
    day TEXT NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    PRIMARY KEY (symbol, day)
);

CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    condition TEXT NOT NULL,
    threshold REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_triggered_on TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER,
    symbol TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    price REAL,
    change_percent REAL,
    email_status TEXT,
    webhook_status TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,
    period_key TEXT NOT NULL,
    title TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (period, period_key)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@contextmanager
def session():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        _seed_instruments(conn)
        _seed_settings(conn)


def _seed_instruments(conn: sqlite3.Connection) -> None:
    for item in DEFAULT_INSTRUMENTS:
        conn.execute(
            """
            INSERT OR IGNORE INTO instruments
                (symbol, name, category, currency, decimals, computed, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                item["symbol"],
                item["name"],
                item["category"],
                item["currency"],
                item["decimals"],
                1 if item.get("computed") else 0,
                now_iso(),
            ),
        )


def _seed_settings(conn: sqlite3.Connection) -> None:
    defaults = {
        "fetch_interval_minutes": os.getenv("FETCH_INTERVAL_MINUTES", "15"),
        "smtp_host": os.getenv("SMTP_HOST", ""),
        "smtp_port": os.getenv("SMTP_PORT", "587"),
        "smtp_user": os.getenv("SMTP_USER", ""),
        "smtp_password": os.getenv("SMTP_PASSWORD", ""),
        "smtp_from": os.getenv("SMTP_FROM", ""),
        "alert_email": os.getenv("ALERT_EMAIL", ""),
        "n8n_webhook_url": os.getenv("N8N_WEBHOOK_URL", ""),
        "send_daily_email": "0",
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            (key, value),
        )


def get_setting(key: str, default: str = "") -> str:
    with session() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with session() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )


def all_settings() -> dict[str, str]:
    with session() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}


def fetch_targets() -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT * FROM instruments WHERE active=1 AND computed=0 ORDER BY symbol"
        ).fetchall()
        return [dict(row) for row in rows]


def get_instrument(symbol: str) -> dict | None:
    with session() as conn:
        row = conn.execute("SELECT * FROM instruments WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else None


def upsert_instrument(symbol: str, name: str, category: str, currency: str, decimals: int) -> dict:
    with session() as conn:
        existing = conn.execute("SELECT * FROM instruments WHERE symbol=?", (symbol,)).fetchone()
        active_count = conn.execute("SELECT COUNT(*) AS c FROM instruments WHERE active=1").fetchone()["c"]
        if existing is None and active_count >= 40:
            raise ValueError("En fazla 40 varlık izlenebilir.")
        if existing and existing["computed"]:
            conn.execute("UPDATE instruments SET active=1, name=? WHERE symbol=?", (name, symbol))
        elif existing:
            conn.execute(
                """
                UPDATE instruments
                SET name=?, category=?, currency=?, decimals=?, active=1
                WHERE symbol=?
                """,
                (name, category, currency, decimals, symbol),
            )
        else:
            conn.execute(
                """
                INSERT INTO instruments
                    (symbol, name, category, currency, decimals, computed, active, created_at)
                VALUES (?, ?, ?, ?, ?, 0, 1, ?)
                """,
                (symbol, name, category, currency, decimals, now_iso()),
            )
        row = conn.execute("SELECT * FROM instruments WHERE symbol=?", (symbol,)).fetchone()
        return dict(row)


def deactivate_instrument(symbol: str) -> None:
    with session() as conn:
        row = conn.execute("SELECT symbol FROM instruments WHERE symbol=?", (symbol,)).fetchone()
        if not row:
            raise ValueError("Varlık bulunamadı.")
        conn.execute("UPDATE instruments SET active=0 WHERE symbol=?", (symbol,))


def save_market_quote(quote: dict) -> None:
    with session() as conn:
        conn.execute(
            """
            INSERT INTO quotes (
                symbol, price, previous_close, change_amount, change_percent,
                change_amount_7d, change_percent_7d, volume, market_time, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                price=excluded.price,
                previous_close=excluded.previous_close,
                change_amount=excluded.change_amount,
                change_percent=excluded.change_percent,
                change_amount_7d=excluded.change_amount_7d,
                change_percent_7d=excluded.change_percent_7d,
                volume=excluded.volume,
                market_time=excluded.market_time,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                quote["symbol"],
                quote.get("price"),
                quote.get("previous_close"),
                quote.get("change_amount"),
                quote.get("change_percent"),
                quote.get("change_amount_7d"),
                quote.get("change_percent_7d"),
                quote.get("volume"),
                quote.get("market_time"),
                quote.get("source"),
                now_iso(),
            ),
        )
        series = quote.get("series") or []
        if series:
            conn.executemany(
                """
                INSERT INTO history(symbol, day, close, volume) VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, day) DO UPDATE SET
                    close=excluded.close,
                    volume=COALESCE(excluded.volume, history.volume)
                """,
                [
                    (quote["symbol"], point["day"], point["close"], point.get("volume"))
                    for point in series
                ],
            )


def load_full(symbol: str) -> dict | None:
    with session() as conn:
        quote = conn.execute("SELECT * FROM quotes WHERE symbol=?", (symbol,)).fetchone()
        if not quote:
            return None
        history_rows = conn.execute(
            "SELECT day, close, volume FROM history WHERE symbol=? ORDER BY day",
            (symbol,),
        ).fetchall()
        data = dict(quote)
        data["series"] = [dict(row) for row in history_rows]
        return data


def history(symbol: str) -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT day, close FROM history WHERE symbol=? ORDER BY day",
            (symbol,),
        ).fetchall()
        return [{"day": row["day"], "close": row["close"]} for row in rows]


def market_rows() -> list[dict]:
    with session() as conn:
        instruments = conn.execute(
            """
            SELECT * FROM instruments
            WHERE active=1
            ORDER BY CASE category
                WHEN 'endeks' THEN 0
                WHEN 'altin' THEN 1
                WHEN 'doviz' THEN 2
                WHEN 'hisse' THEN 3
                ELSE 4 END, name
            """
        ).fetchall()
        rows = []
        for inst in instruments:
            quote = conn.execute("SELECT * FROM quotes WHERE symbol=?", (inst["symbol"],)).fetchone()
            spark_rows = conn.execute(
                "SELECT close FROM history WHERE symbol=? ORDER BY day DESC LIMIT 30",
                (inst["symbol"],),
            ).fetchall()
            item = {
                "symbol": inst["symbol"],
                "name": inst["name"],
                "category": inst["category"],
                "currency": inst["currency"],
                "decimals": inst["decimals"],
                "computed": bool(inst["computed"]),
                "price": None,
                "previous_close": None,
                "change_amount": None,
                "change_percent": None,
                "change_amount_7d": None,
                "change_percent_7d": None,
                "volume": None,
                "market_time": None,
                "source": None,
                "updated_at": None,
                "sparkline": [row["close"] for row in reversed(spark_rows)],
            }
            if quote:
                for key in (
                    "price",
                    "previous_close",
                    "change_amount",
                    "change_percent",
                    "change_amount_7d",
                    "change_percent_7d",
                    "volume",
                    "market_time",
                    "source",
                    "updated_at",
                ):
                    item[key] = quote[key]
            rows.append(item)
        return rows


def list_rules() -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT r.*, COALESCE(i.name, r.symbol) AS name,
                   COALESCE(i.currency, '') AS currency,
                   COALESCE(i.decimals, 2) AS decimals
            FROM alert_rules r
            LEFT JOIN instruments i ON i.symbol = r.symbol
            ORDER BY r.id DESC
            """
        ).fetchall()
        return [_rule(row) for row in rows]


def count_rules() -> int:
    with session() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM alert_rules").fetchone()["c"]


def add_rule(symbol: str, condition: str, threshold: float, note: str) -> dict:
    with session() as conn:
        inst = conn.execute(
            "SELECT symbol FROM instruments WHERE symbol=? AND active=1",
            (symbol,),
        ).fetchone()
        if not inst:
            raise ValueError("Bu sembol listede yok.")
        total = conn.execute("SELECT COUNT(*) AS c FROM alert_rules").fetchone()["c"]
        if total >= 50:
            raise ValueError("En fazla 50 uyarı kuralı eklenebilir.")
        cur = conn.execute(
            """
            INSERT INTO alert_rules(symbol, condition, threshold, note, enabled, last_triggered_on, created_at)
            VALUES (?, ?, ?, ?, 1, NULL, ?)
            """,
            (symbol, condition, threshold, note[:200], now_iso()),
        )
        row = conn.execute(
            """
            SELECT r.*, COALESCE(i.name, r.symbol) AS name,
                   COALESCE(i.currency, '') AS currency,
                   COALESCE(i.decimals, 2) AS decimals
            FROM alert_rules r
            LEFT JOIN instruments i ON i.symbol = r.symbol
            WHERE r.id=?
            """,
            (cur.lastrowid,),
        ).fetchone()
        return _rule(row)


def set_rule_enabled(rule_id: int, enabled: bool) -> dict:
    with session() as conn:
        found = conn.execute("SELECT id FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
        if not found:
            raise ValueError("Kural bulunamadı.")
        conn.execute("UPDATE alert_rules SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))
        row = conn.execute(
            """
            SELECT r.*, COALESCE(i.name, r.symbol) AS name,
                   COALESCE(i.currency, '') AS currency,
                   COALESCE(i.decimals, 2) AS decimals
            FROM alert_rules r
            LEFT JOIN instruments i ON i.symbol = r.symbol
            WHERE r.id=?
            """,
            (rule_id,),
        ).fetchone()
        return _rule(row)


def delete_rule(rule_id: int) -> None:
    with session() as conn:
        conn.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))


def mark_triggered(rule_id: int, day: str) -> None:
    with session() as conn:
        conn.execute(
            "UPDATE alert_rules SET last_triggered_on=? WHERE id=?",
            (day, rule_id),
        )


def add_event(event: dict) -> dict:
    with session() as conn:
        cur = conn.execute(
            """
            INSERT INTO alert_events (
                rule_id, symbol, subject, message, price, change_percent,
                email_status, webhook_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("rule_id"),
                event["symbol"],
                event["subject"],
                event["message"],
                event.get("price"),
                event.get("change_percent"),
                event.get("email_status"),
                event.get("webhook_status"),
                now_iso(),
            ),
        )
        row = conn.execute("SELECT * FROM alert_events WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_events(limit: int = 40) -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT * FROM alert_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_event(event_id: int) -> dict | None:
    with session() as conn:
        row = conn.execute("SELECT * FROM alert_events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row else None


def update_event_status(event_id: int, email_status: str, webhook_status: str) -> None:
    with session() as conn:
        conn.execute(
            "UPDATE alert_events SET email_status=?, webhook_status=? WHERE id=?",
            (email_status, webhook_status, event_id),
        )


def save_report(report: dict) -> None:
    with session() as conn:
        conn.execute(
            """
            INSERT INTO reports(period, period_key, title, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(period, period_key) DO UPDATE SET
                title=excluded.title,
                payload=excluded.payload,
                created_at=excluded.created_at
            """,
            (
                report["period"],
                report["period_key"],
                report["title"],
                json.dumps(report, ensure_ascii=False),
                now_iso(),
            ),
        )


def list_reports(limit: int = 30) -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT id, payload, created_at
            FROM reports
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        items = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["id"] = row["id"]
            payload["created_at"] = row["created_at"]
            items.append(payload)
        return items


def _rule(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    return data
