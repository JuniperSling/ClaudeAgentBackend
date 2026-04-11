import logging
import uuid

import bcrypt

from src.services.database import get_db

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


class UserManager:
    async def get_by_qq_id(self, qq_id: str) -> dict | None:
        db = await get_db()
        row = await db.execute_fetchall(
            "SELECT * FROM users WHERE qq_id = ? AND is_active = 1", (str(qq_id),)
        )
        if row:
            return dict(row[0])
        return None

    async def get_by_id(self, user_id: str) -> dict | None:
        db = await get_db()
        row = await db.execute_fetchall(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        if row:
            return dict(row[0])
        return None

    async def create_user(
        self, qq_id: str, password: str, nickname: str = "", role: str = "user"
    ) -> dict:
        db = await get_db()
        user_id = str(uuid.uuid4())[:8]
        pw_hash = hash_password(password)
        nickname = nickname or f"user_{qq_id[-4:]}"

        await db.execute(
            "INSERT INTO users (id, qq_id, nickname, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (user_id, str(qq_id), nickname, pw_hash, role),
        )
        await db.commit()
        logger.info("Created user: qq_id=%s, role=%s, id=%s", qq_id, role, user_id)
        return await self.get_by_qq_id(qq_id)

    async def authenticate(self, qq_id: str, password: str) -> dict | None:
        user = await self.get_by_qq_id(qq_id)
        if user and verify_password(password, user["password_hash"]):
            return user
        return None

    async def ensure_admin(self, qq_id: str, password: str):
        existing = await self.get_by_qq_id(qq_id)
        if not existing:
            await self.create_user(qq_id, password, nickname="Admin", role="admin")
            logger.info("Admin user created: %s", qq_id)
        else:
            logger.info("Admin user already exists: %s", qq_id)

    async def list_users(self) -> list[dict]:
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT id, qq_id, nickname, role, is_active, created_at FROM users"
        )
        return [dict(r) for r in rows]
