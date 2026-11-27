"""Tiny DB layer: SQLite by default, Postgres when DATABASE_URL is set.

SQL is written with '?' placeholders; they are converted to '%s' for Postgres.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS channels (
        id {PK},
        platform TEXT NOT NULL,
        name TEXT NOT NULL,
        handle TEXT,
        external_id TEXT,
        remote_name TEXT,
        avatar_url TEXT,
        token_enc TEXT,
        refresh_enc TEXT,
        token_expires_at TEXT,
        status TEXT NOT NULL DEFAULT 'disconnected',
        status_detail TEXT,
        auth TEXT,
        sort INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS discovered (
        id {PK},
        platform TEXT NOT NULL,
        external_id TEXT NOT NULL,
        name TEXT,
        handle TEXT,
        avatar_url TEXT,
        token_enc TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(platform, external_id)
    )""",
    """CREATE TABLE IF NOT EXISTS media (
        id {PK},
        token TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        filename TEXT NOT NULL,
        path TEXT NOT NULL,
        jpeg_path TEXT,
        mime TEXT,
        size BIGINT NOT NULL DEFAULT 0,
        received BIGINT NOT NULL DEFAULT 0,
        width INTEGER,
        height INTEGER,
        duration REAL,
        status TEXT NOT NULL DEFAULT 'uploading',
        created_at TEXT NOT NULL,
        done_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS posts (
        id {PK},
        post_type TEXT NOT NULL,
        media_ids TEXT NOT NULL DEFAULT '[]',
        override TEXT,
        label TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS destinations (
        id {PK},
        post_id INTEGER NOT NULL,
        channel_id INTEGER,
        channel_name TEXT,
        platform TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT,
        body TEXT,
        link TEXT,
        privacy TEXT,
        options TEXT,
        schedule_at TEXT,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        detail TEXT,
        progress INTEGER,
        remote_id TEXT,
        remote_url TEXT,
        container_id TEXT,
        container_at TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        locked_until TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)",
    "CREATE INDEX IF NOT EXISTS ix_dest_status ON destinations(status)",
    "CREATE INDEX IF NOT EXISTS ix_dest_post ON destinations(post_id)",
]

SEED = [
    ("youtube", "Venu Kalyan Business Growth Strategist", None),
    ("youtube", "Telugu Business Talks with Venu Kalyan", None),
    ("youtube", "Venu Kalyan Clips", None),
    ("youtube", "Soujanya Parenting Coach", None),
    ("instagram", "venukalyan01", "venukalyan01"),
    ("instagram", "venukalyanpodcastshorts", "venukalyanpodcastshorts"),
    ("instagram", "ubmbyvenukalayan", "ubmbyvenukalayan"),
    ("instagram", "msbyvenukalyan", "msbyvenukalyan"),
    ("instagram", "soujanyaparentingcoach", "soujanyaparentingcoach"),
    ("facebook", "Venu Kalyan", "venukalyan01"),
    ("facebook", "Soujanya Parenting Coach", "Soujanyaparentingcoach"),
]


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Database:
    def __init__(self, url=None, sqlite_path=None):
        self.url = url or ""
        self.is_pg = self.url.startswith(("postgres://", "postgresql://"))
        self._lock = threading.RLock()
        if self.is_pg:
            from psycopg_pool import ConnectionPool
            url = self.url.replace("postgres://", "postgresql://", 1)
            self._pool = ConnectionPool(url, min_size=1, max_size=4, open=True,
                                        kwargs={"autocommit": False})
        else:
            self.sqlite_path = sqlite_path
            os.makedirs(os.path.dirname(os.path.abspath(sqlite_path)), exist_ok=True)

    # ---- connections -------------------------------------------------
    @contextmanager
    def _conn(self):
        if self.is_pg:
            with self._pool.connection() as c:
                yield c
        else:
            with self._lock:
                c = sqlite3.connect(self.sqlite_path, timeout=30)
                c.row_factory = sqlite3.Row
                try:
                    c.execute("PRAGMA journal_mode=WAL")
                    yield c
                    c.commit()
                except Exception:
                    c.rollback()
                    raise
                finally:
                    c.close()

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.is_pg else sql

    def _rows(self, cur):
        if cur.description is None:
            return []
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def query(self, sql, params=()):
        with self._conn() as c:
            cur = c.cursor() if self.is_pg else c
            cur = cur.execute(self._sql(sql), tuple(params))
            rows = self._rows(cur)
            if self.is_pg:
                c.commit()
            return rows

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        """Run a write; returns affected row count."""
        with self._conn() as c:
            cur = c.cursor() if self.is_pg else c
            cur = cur.execute(self._sql(sql), tuple(params))
            n = cur.rowcount
            if self.is_pg:
                c.commit()
            return n

    def insert(self, table, data):
        cols = list(data.keys())
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) RETURNING id"
        rows = self.query(sql, [data[k] for k in cols])
        return rows[0]["id"]

    def update(self, table, row_id, data):
        if not data:
            return 0
        sets = ",".join(f"{k}=?" for k in data)
        return self.execute(f"UPDATE {table} SET {sets} WHERE id=?", list(data.values()) + [row_id])

    # ---- schema ------------------------------------------------------
    def init(self, seed=True):
        pk = "BIGSERIAL PRIMARY KEY" if self.is_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
        for stmt in SCHEMA:
            s = stmt.replace("{PK}", pk)
            if not self.is_pg:
                s = s.replace("BIGINT", "INTEGER")
            self.execute(s)
        if seed:
            if not self.one("SELECT v FROM meta WHERE k='seeded'"):
                self.execute("INSERT INTO meta (k, v) VALUES ('seeded', '1')")
                now = iso(utcnow())
                for i, (p, name, handle) in enumerate(SEED):
                    self.insert("channels", {"platform": p, "name": name, "handle": handle,
                                             "status": "disconnected", "sort": i,
                                             "created_at": now, "updated_at": now})
