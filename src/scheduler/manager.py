import asyncio
import json
import logging
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import get_config
from src.services.database import get_db

logger = logging.getLogger(__name__)


class TaskScheduler:
    def __init__(self, send_func=None):
        self._scheduler = AsyncIOScheduler()
        self._send_func = send_func

    async def start(self):
        await self._load_tasks_from_db()
        self._scheduler.start()
        logger.info("TaskScheduler started")

    async def stop(self):
        self._scheduler.shutdown(wait=False)
        logger.info("TaskScheduler stopped")

    def set_send_func(self, send_func):
        """Set the function used to send messages: async def send(channel, target_id, text)"""
        self._send_func = send_func

    async def add_task(
        self,
        owner_id: str,
        name: str,
        cron_expr: str,
        target_channel: str,
        target_id: str,
        task_type: str = "custom",
        params: dict | None = None,
        script_path: str | None = None,
    ) -> str:
        config = get_config()
        db = await get_db()

        owner_tasks = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM tasks WHERE owner_id = ? AND status = 'active'",
            (owner_id,),
        )
        if owner_tasks and dict(owner_tasks[0])["cnt"] >= config.scheduler.max_tasks_per_user:
            raise ValueError(f"Task limit reached ({config.scheduler.max_tasks_per_user})")

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        await db.execute(
            """INSERT INTO tasks (id, owner_id, name, task_type, cron_expr, params, target_channel, target_id, script_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, owner_id, name, task_type, cron_expr,
                json.dumps(params or {}), target_channel, target_id, script_path,
            ),
        )
        await db.commit()

        self._register_job(task_id, cron_expr, target_channel, target_id, script_path, params or {})
        logger.info("Task added: %s (%s) by %s", task_id, name, owner_id)
        return task_id

    async def remove_task(self, task_id: str, owner_id: str | None = None):
        db = await get_db()

        rows = await db.execute_fetchall(
            "SELECT script_path FROM tasks WHERE id = ?", (task_id,)
        )
        script_path = dict(rows[0]).get("script_path") if rows else None

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

        if script_path:
            import os
            try:
                os.unlink(script_path)
            except OSError:
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
                task.get("script_path"), json.loads(task.get("params", "{}")),
            )
        logger.info("Loaded %d tasks from database", len(rows))

    def _register_job(
        self, task_id: str, cron_expr: str,
        target_channel: str, target_id: str,
        script_path: str | None, params: dict,
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

        self._scheduler.add_job(
            self._execute_task,
            trigger=trigger,
            id=task_id,
            replace_existing=True,
            kwargs={
                "task_id": task_id,
                "target_channel": target_channel,
                "target_id": target_id,
                "script_path": script_path,
                "params": params,
            },
        )

    async def _execute_task(
        self, task_id: str, target_channel: str, target_id: str,
        script_path: str | None, params: dict,
    ):
        logger.info("Executing task: %s", task_id)
        config = get_config()

        try:
            if script_path:
                result = await asyncio.wait_for(
                    self._run_script(script_path, params, config.data_dir),
                    timeout=config.scheduler.task_timeout_seconds,
                )
            else:
                result = f"Task {task_id} executed (no script configured)"

            if result and self._send_func:
                await self._send_func(target_channel, target_id, result)

        except asyncio.TimeoutError:
            logger.error("Task %s timed out after %ds", task_id, config.scheduler.task_timeout_seconds)
        except Exception:
            logger.exception("Task %s failed", task_id)

    async def _run_script(self, script_path: str, params: dict, data_dir: str) -> str:
        """Run a task script in a subprocess with timeout."""
        import tempfile

        params_json = json.dumps(params, ensure_ascii=False)
        wrapper = f"""
import sys, json
sys.path.insert(0, '.')
params = json.loads('''{params_json}''')

exec(open('{script_path}').read())
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(wrapper)
            tmp_path = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", tmp_path,
                cwd=data_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error("Task script error: %s", stderr.decode()[-500:])
                return f"任务执行失败: {stderr.decode()[-200:]}"
            return stdout.decode().strip()
        finally:
            import os
            os.unlink(tmp_path)
