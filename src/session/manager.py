import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from src.services.database import get_db

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, ttl_hours: int = 24, max_history: int = 50):
        self.ttl_hours = ttl_hours
        self.max_history = max_history

    async def get_or_create(
        self, user_id: str, channel: str, channel_session_id: str
    ) -> dict:
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT * FROM sessions WHERE channel = ? AND channel_session_id = ?",
            (channel, channel_session_id),
        )

        if rows:
            session = dict(rows[0])
            last_active = datetime.fromisoformat(session["last_active"])
            if datetime.now(timezone.utc) - last_active.replace(
                tzinfo=timezone.utc
            ) > timedelta(hours=self.ttl_hours):
                await self._reset_history(session["id"])
                session["history"] = "[]"
                logger.info("Session expired, reset: %s", session["id"])
            return session

        session_id = str(uuid.uuid4())[:12]
        await db.execute(
            "INSERT INTO sessions (id, user_id, channel, channel_session_id) VALUES (?, ?, ?, ?)",
            (session_id, user_id, channel, channel_session_id),
        )
        await db.commit()
        logger.info(
            "Created session: %s for user %s on %s",
            session_id, user_id, channel,
        )
        return {
            "id": session_id,
            "user_id": user_id,
            "channel": channel,
            "channel_session_id": channel_session_id,
            "history": "[]",
        }

    async def append_message(self, session_id: str, role: str, content: str):
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT history FROM sessions WHERE id = ?", (session_id,)
        )
        if not rows:
            return

        history = json.loads(rows[0]["history"])
        history.append({"role": role, "content": content})

        if len(history) > self.max_history:
            history = history[-self.max_history :]

        await db.execute(
            "UPDATE sessions SET history = ?, last_active = datetime('now') WHERE id = ?",
            (json.dumps(history, ensure_ascii=False), session_id),
        )
        await db.commit()

    async def get_history(self, session_id: str) -> list[dict]:
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT history FROM sessions WHERE id = ?", (session_id,)
        )
        if rows:
            return json.loads(rows[0]["history"])
        return []

    async def _reset_history(self, session_id: str):
        db = await get_db()
        await db.execute(
            "UPDATE sessions SET history = '[]', last_active = datetime('now') WHERE id = ?",
            (session_id,),
        )
        await db.commit()

    async def clear_session(self, session_id: str):
        await self._reset_history(session_id)
        logger.info("Session cleared: %s", session_id)
