import json
import logging
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import get_config
from src.services.database import get_db

logger = logging.getLogger(__name__)


class TaskScheduler:
    """Schedules LLM-prompt tasks. At each fire time, the configured prompt is
    enqueued as a user message to a fresh ClaudeSDKClient and the agent's
    output is sent back to the originating chat."""

    def __init__(self, llm_runner=None):
        self._scheduler = AsyncIOScheduler()
        self._llm_runner = llm_runner

    async def start(self):
        await self._load_tasks_from_db()
        self._scheduler.start()
        logger.info("TaskScheduler started")

    async def stop(self):
        self._scheduler.shutdown(wait=False)
        logger.info("TaskScheduler stopped")

    def set_llm_runner(self, llm_runner):
        """Set the LLM task runner: async def run(task: dict)"""
        self._llm_runner = llm_runner

    async def add_task(
        self,
        owner_id: str,
        name: str,
        cron_expr: str,
        target_channel: str,
        target_id: str,
        params: dict | None = None,
        task_id: str | None = None,
    ) -> str:
        config = get_config()
        db = await get_db()

        owner_tasks = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM tasks WHERE owner_id = ? AND status = 'active'",
            (owner_id,),
        )
        if owner_tasks and dict(owner_tasks[0])["cnt"] >= config.scheduler.max_tasks_per_user:
            raise ValueError(f"Task limit reached ({config.scheduler.max_tasks_per_user})")

        if not task_id:
            task_id = f"task_{uuid.uuid4().hex[:8]}"

        await db.execute(
            """INSERT INTO tasks (id, owner_id, name, task_type, cron_expr, params, target_channel, target_id)
               VALUES (?, ?, ?, 'llm', ?, ?, ?, ?)""",
            (
                task_id, owner_id, name, cron_expr,
                json.dumps(params or {}), target_channel, target_id,
            ),
        )
        await db.commit()

        self._register_job(task_id, cron_expr, target_channel, target_id, params or {}, owner_id)
        logger.info("Task added: %s (%s) by %s", task_id, name, owner_id)
        return task_id

    async def remove_task(self, task_id: str, owner_id: str | None = None):
        db = await get_db()

        if owner_id:
            await db.execute(
                "UPDATE tasks SET status = 'deleted' WHERE id = ? AND owner_id = ?",
                (task_id, owner_id),
            )
        else:
            await db.execute(
                "UPDATE tasks SET status = 'deleted' WHERE id = ?", (task_id,)
            )
        await db.commit()

        try:
            self._scheduler.remove_job(task_id)
        except Exception:
            pass

        logger.info("Task removed: %s", task_id)

    async def list_tasks(self, owner_id: str | None = None) -> list[dict]:
        db = await get_db()
        if owner_id:
            rows = await db.execute_fetchall(
                "SELECT * FROM tasks WHERE owner_id = ? AND status = 'active'",
                (owner_id,),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM tasks WHERE status = 'active'"
            )
        return [dict(r) for r in rows]

    async def _load_tasks_from_db(self):
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT * FROM tasks WHERE status = 'active'"
        )
        for row in rows:
            task = dict(row)
            self._register_job(
                task["id"], task["cron_expr"],
                task["target_channel"], task["target_id"],
                json.loads(task.get("params", "{}")),
                task.get("owner_id", ""),
            )
        logger.info("Loaded %d tasks from database", len(rows))

    def _register_job(
        self, task_id: str, cron_expr: str,
        target_channel: str, target_id: str,
        params: dict, owner_id: str = "",
    ):
        cron_parts = cron_expr.split()
        if len(cron_parts) == 5:
            trigger = CronTrigger(
                minute=cron_parts[0], hour=cron_parts[1],
                day=cron_parts[2], month=cron_parts[3],
                day_of_week=cron_parts[4],
            )
        else:
            trigger = CronTrigger.from_crontab(cron_expr)

        if trigger.get_next_fire_time(None, __import__("datetime").datetime.now(trigger.timezone)) is None:
            raise ValueError(
                f"Cron expression '{cron_expr}' will never fire. "
                "Check the day/month/weekday combination."
            )

        self._scheduler.add_job(
            self._execute_task,
            trigger=trigger,
            id=task_id,
            replace_existing=True,
            kwargs={
                "task_id": task_id,
                "target_channel": target_channel,
                "target_id": target_id,
                "params": params,
                "owner_id": owner_id,
            },
        )

    async def _execute_task(
        self, task_id: str, target_channel: str, target_id: str,
        params: dict, owner_id: str = "",
    ):
        logger.info("Executing task: %s", task_id)
        try:
            if self._llm_runner:
                await self._llm_runner({
                    "task_id": task_id,
                    "owner_id": owner_id,
                    "target_channel": target_channel,
                    "target_id": target_id,
                    "params": params,
                })

            if not params.get("recurring", True):
                await self.remove_task(task_id)
                logger.info("One-shot task auto-deleted: %s", task_id)

        except Exception:
            logger.exception("Task %s failed", task_id)
