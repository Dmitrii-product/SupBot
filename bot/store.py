"""SQLite + sqlite-vec storage.

One embedded database holds every shop's catalog (multi-tenant via a partition
key) plus escalation state. No separate vector service to run or pay for.
"""
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
    # partition key = shop_id keeps each tenant's vectors isolated and fast.
    # +content is an auxiliary (stored, unindexed) column.
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
    db.commit()


def clear_shop(shop_id: str) -> None:
    db = _db()
    db.execute("DELETE FROM catalog WHERE shop_id = ?", (shop_id,))
    db.commit()


def add_items(shop_id: str, contents: list[str]) -> int:
    """Embed and store catalog/FAQ chunks for a shop."""
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
    """Return [(content, cosine_distance), ...] best-first for one shop."""
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
