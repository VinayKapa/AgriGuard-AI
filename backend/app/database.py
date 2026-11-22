import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import settings

DB_PATH = Path(settings.database_path).expanduser() if settings.database_path else Path(__file__).resolve().parent.parent / "agriguard.sqlite3"


class _CompatRow:
    """Allow existing API code to read a Postgres row by either name or index."""
    def __init__(self, row, columns):
        self._row = row
        self._columns = columns

    def __getitem__(self, key):
        return self._row[self._columns.index(key)] if isinstance(key, str) else self._row[key]


class _CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        row = self._cursor.fetchone()
        return _CompatRow(row, [column.name for column in self._cursor.description]) if row is not None else None

    def fetchall(self):
        columns = [column.name for column in self._cursor.description]
        return [_CompatRow(row, columns) for row in self._cursor.fetchall()]


class _PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, query, params=()):
        return _CompatCursor(self._connection.execute(query.replace("?", "%s"), params))

    def executemany(self, query, params):
        return _CompatCursor(self._connection.cursor().executemany(query.replace("?", "%s"), params))

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


@contextmanager
def connect():
    if settings.database_url:
        import psycopg
        raw = psycopg.connect(settings.database_url, sslmode="require", connect_timeout=10, prepare_threshold=None)
        db = _PostgresConnection(raw)
    else:
        db = sqlite3.connect(DB_PATH, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    if settings.database_url:
        statements = [
            "CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash BYTEA NOT NULL, password_salt BYTEA NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS auth_attempts (id BIGSERIAL PRIMARY KEY, bucket TEXT NOT NULL, attempted_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_auth_attempts_bucket_time ON auth_attempts(bucket, attempted_at)",
            "CREATE TABLE IF NOT EXISTS farms (user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, payload TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "DELETE FROM sessions WHERE user_id NOT IN (SELECT id FROM users)",
        ]
        with connect() as db:
            for statement in statements:
                db.execute(statement)
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash BLOB NOT NULL,
            password_salt BLOB NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket TEXT NOT NULL,
            attempted_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_auth_attempts_bucket_time ON auth_attempts(bucket, attempted_at);
        CREATE TABLE IF NOT EXISTS farms (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        DELETE FROM sessions WHERE user_id NOT IN (SELECT id FROM users);
        """)
