import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_db_path: str = "data/claude_agent.db"
_db: aiosqlite.Connection | None = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    qq_id TEXT UNIQUE NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    max_tasks INTEGER NOT NULL DEFAULT 10,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'qq',
    channel_session_id TEXT NOT NULL,
    history TEXT NOT NULL DEFAULT '[]',
    last_active TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_channel
    ON sessions(channel, channel_session_id);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'custom',
    cron_expr TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    target_channel TEXT NOT NULL DEFAULT 'qq',
    target_id TEXT NOT NULL,
    script_path TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (owner_id) REFERENCES users(id)
);
"""


async def init_db(db_path: str | None = None):
    global _db, _db_path
    if db_path:
        _db_path = db_path

    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(_db_path)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA_SQL)
    await _db.commit()
    logger.info("Database initialized: %s", _db_path)


async def get_db() -> aiosqlite.Connection:
    if _db is None:
        await init_db()
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None
