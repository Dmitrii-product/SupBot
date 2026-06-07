"""SQLite + sqlite-vec storage.

One embedded database holds every shop's catalog (multi-tenant via a partition
key), escalation state, contact info, and full conversation history.
"""
import hashlib
import os
import secrets
import sqlite3

import sqlite_vec

from bot import config, embeddings

_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.enable_load_extension(True)
        sqlite_vec.load(_conn)
        _conn.enable_load_extension(False)
    return _conn


def init_db() -> None:
    db = _db()
    d = embeddings.dim()
    db.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS catalog USING vec0(
            shop_id TEXT partition key,
            +content TEXT,
            embedding float[{d}] distance_metric=cosine
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS escalations (
            shop_id TEXT,
            user_id INTEGER,
            PRIMARY KEY (shop_id, user_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            shop_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            full_name TEXT,
            username TEXT,
            PRIMARY KEY (shop_id, user_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            ts INTEGER NOT NULL DEFAULT (unixepoch())
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS shops (
            shop_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            bot_token TEXT NOT NULL UNIQUE,
            manager_chat_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (unixepoch())
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (unixepoch())
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_sessions (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (unixepoch())
        )
        """
    )
    db.commit()


def clear_shop(shop_id: str) -> None:
    db = _db()
    db.execute("DELETE FROM catalog WHERE shop_id = ?", (shop_id,))
    db.commit()


def get_all_items(shop_id: str) -> list[tuple[int, str]]:
    """Return [(rowid, content), ...] for all catalog items of a shop."""
    db = _db()
    rows = db.execute(
        "SELECT rowid, content FROM catalog WHERE shop_id = ?", (shop_id,)
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def delete_items(shop_id: str, rowids: list[int]) -> None:
    """Delete specific catalog rows by rowid."""
    db = _db()
    for rowid in rowids:
        db.execute("DELETE FROM catalog WHERE rowid = ?", (rowid,))
    db.commit()


def add_items(shop_id: str, contents: list[str]) -> int:
    db = _db()
    vecs = embeddings.embed_passages(contents)
    for content, vec in zip(contents, vecs):
        db.execute(
            "INSERT INTO catalog (shop_id, content, embedding) VALUES (?, ?, ?)",
            (shop_id, content, sqlite_vec.serialize_float32(vec)),
        )
    db.commit()
    return len(contents)


def search(shop_id: str, query_vec: list[float], k: int) -> list[tuple[str, float]]:
    db = _db()
    rows = db.execute(
        """
        SELECT content, distance
        FROM catalog
        WHERE shop_id = ? AND embedding MATCH ? AND k = ?
        ORDER BY distance
        """,
        (shop_id, sqlite_vec.serialize_float32(query_vec), k),
    ).fetchall()
    return [(content, float(dist)) for content, dist in rows]


# --- escalation state -------------------------------------------------------

def set_escalated(shop_id: str, user_id: int) -> None:
    db = _db()
    db.execute(
        "INSERT OR IGNORE INTO escalations (shop_id, user_id) VALUES (?, ?)",
        (shop_id, user_id),
    )
    db.commit()


def clear_escalated(shop_id: str, user_id: int) -> None:
    db = _db()
    db.execute(
        "DELETE FROM escalations WHERE shop_id = ? AND user_id = ?",
        (shop_id, user_id),
    )
    db.commit()


def is_escalated(shop_id: str, user_id: int) -> bool:
    db = _db()
    row = db.execute(
        "SELECT 1 FROM escalations WHERE shop_id = ? AND user_id = ?",
        (shop_id, user_id),
    ).fetchone()
    return row is not None


# --- contacts & message history ---------------------------------------------

def upsert_contact(shop_id: str, user_id: int, full_name: str, username: str | None) -> None:
    db = _db()
    db.execute(
        "INSERT OR REPLACE INTO contacts (shop_id, user_id, full_name, username) VALUES (?, ?, ?, ?)",
        (shop_id, user_id, full_name, username),
    )
    db.commit()


def log_message(shop_id: str, user_id: int, role: str, text: str) -> None:
    """role: 'customer' | 'bot' | 'manager'"""
    db = _db()
    db.execute(
        "INSERT INTO messages (shop_id, user_id, role, text) VALUES (?, ?, ?, ?)",
        (shop_id, user_id, role, text),
    )
    db.commit()


def get_history(shop_id: str, user_id: int, limit: int = 20) -> list[tuple[str, str]]:
    """Returns [(role, text), ...] oldest-first."""
    db = _db()
    rows = db.execute(
        """
        SELECT role, text FROM messages
        WHERE shop_id = ? AND user_id = ?
        ORDER BY ts DESC LIMIT ?
        """,
        (shop_id, user_id, limit),
    ).fetchall()
    return list(reversed(rows))


def get_escalated_contacts(shop_id: str) -> list[dict]:
    """Escalated users with contact info and their most recent customer message."""
    db = _db()
    rows = db.execute(
        """
        SELECT e.user_id, c.full_name, c.username,
               (SELECT text FROM messages
                WHERE shop_id = e.shop_id AND user_id = e.user_id AND role = 'customer'
                ORDER BY ts DESC LIMIT 1) AS last_msg
        FROM escalations e
        LEFT JOIN contacts c ON c.shop_id = e.shop_id AND c.user_id = e.user_id
        WHERE e.shop_id = ?
        """,
        (shop_id,),
    ).fetchall()
    return [
        {"user_id": r[0], "full_name": r[1] or str(r[0]), "username": r[2], "last_msg": r[3]}
        for r in rows
    ]


# --- shop registry ----------------------------------------------------------

def upsert_shop(shop_id: str, name: str, bot_token: str, manager_chat_id: int) -> None:
    db = _db()
    db.execute(
        """
        INSERT INTO shops (shop_id, name, bot_token, manager_chat_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(shop_id) DO UPDATE SET
            name=excluded.name,
            bot_token=excluded.bot_token,
            manager_chat_id=excluded.manager_chat_id
        """,
        (shop_id, name, bot_token, manager_chat_id),
    )
    db.commit()


def get_shop(shop_id: str) -> dict | None:
    db = _db()
    row = db.execute(
        "SELECT shop_id, name, bot_token, manager_chat_id FROM shops WHERE shop_id = ?",
        (shop_id,),
    ).fetchone()
    if not row:
        return None
    return {"shop_id": row[0], "name": row[1], "bot_token": row[2], "manager_chat_id": row[3]}


def get_all_shops() -> list[dict]:
    db = _db()
    rows = db.execute(
        "SELECT shop_id, name, bot_token, manager_chat_id FROM shops ORDER BY created_at"
    ).fetchall()
    return [{"shop_id": r[0], "name": r[1], "bot_token": r[2], "manager_chat_id": r[3]} for r in rows]


def delete_shop(shop_id: str) -> None:
    db = _db()
    db.execute("DELETE FROM shops WHERE shop_id = ?", (shop_id,))
    db.execute("DELETE FROM catalog WHERE shop_id = ?", (shop_id,))
    db.execute("DELETE FROM escalations WHERE shop_id = ?", (shop_id,))
    db.execute("DELETE FROM messages WHERE shop_id = ?", (shop_id,))
    db.execute("DELETE FROM contacts WHERE shop_id = ?", (shop_id,))
    db.commit()


def catalog_count(shop_id: str) -> int:
    try:
        db = _db()
        row = db.execute(
            "SELECT count(*) FROM catalog WHERE shop_id = ?", (shop_id,)
        ).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


# --- admin accounts & sessions ----------------------------------------------

def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return salt.hex() + ":" + key.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(":")
        salt = bytes.fromhex(salt_hex)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def admin_count() -> int:
    return _db().execute("SELECT count(*) FROM admin_users").fetchone()[0]


def create_admin(username: str, password: str) -> bool:
    """Returns False if username already exists."""
    try:
        _db().execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            (username, _hash_password(password)),
        )
        _db().commit()
        return True
    except sqlite3.IntegrityError:
        return False


def verify_admin(username: str, password: str) -> bool:
    row = _db().execute(
        "SELECT password_hash FROM admin_users WHERE username = ?", (username,)
    ).fetchone()
    return bool(row and _verify_password(password, row[0]))


def create_session(username: str) -> str:
    token = secrets.token_hex(32)
    _db().execute(
        "INSERT INTO admin_sessions (token, username) VALUES (?, ?)", (token, username)
    )
    _db().commit()
    return token


def get_session(token: str) -> str | None:
    """Returns username if session exists and is less than 30 days old."""
    row = _db().execute(
        "SELECT username FROM admin_sessions WHERE token = ? AND created_at > unixepoch() - 2592000",
        (token,),
    ).fetchone()
    return row[0] if row else None


def delete_session(token: str) -> None:
    _db().execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
    _db().commit()
