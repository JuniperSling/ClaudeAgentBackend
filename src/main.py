import asyncio
import logging
import os
import signal
import sys

from src.config import init_config, get_config, get_env, get_active_model, set_active_model, MODEL_PRESETS
from src.services.database import init_db, close_db
from src.users.manager import UserManager
from src.session.manager import SessionManager
from src.agent.runner import AgentRunner
from src.channels.base import IncomingMessage
from src.channels.qq.bot import QQBot
from src.scheduler.manager import TaskScheduler
from src.services.internal_api import start_internal_api, stop_internal_api
from src.services.openrouter_proxy import start_openrouter_proxy, stop_openrouter_proxy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("claude-agent")


class Application:
    def __init__(self):
        self.user_mgr = UserManager()
        self.session_mgr: SessionManager | None = None
        self.agent: AgentRunner | None = None
        self.qq_bot: QQBot | None = None
        self.scheduler: TaskScheduler | None = None
        self._processing = set()

    async def start(self):
        init_config()
        config = get_config()
        env = get_env()

        await init_db(config.database_path)

        await self.user_mgr.ensure_admin(env.admin_qq_id, env.admin_password)

        self.session_mgr = SessionManager(
            ttl_hours=config.session.ttl_hours,
            max_history=config.session.max_history,
        )
        self.agent = AgentRunner()

        self.qq_bot = QQBot(
            ws_url=config.napcat.ws_url,
            http_url=config.napcat.http_url,
        )

        self.scheduler = TaskScheduler()
        self.scheduler.set_llm_runner(self._scheduler_run_llm)

        await self.qq_bot.start(on_message=self.handle_message)
        await self.scheduler.start()

        start_internal_api(self)
        start_openrouter_proxy()

        logger.info("Application started successfully")

    async def stop(self):
        logger.info("Shutting down...")
        stop_internal_api()
        stop_openrouter_proxy()
        if self.qq_bot:
            await self.qq_bot.stop()
        if self.scheduler:
            await self.scheduler.stop()
        await close_db()
        logger.info("Application stopped")

    async def handle_message(self, msg: IncomingMessage):
        task = asyncio.create_task(self._process_message(msg))
        self._processing.add(task)
        task.add_done_callback(self._processing.discard)

    async def _process_message(self, msg: IncomingMessage):
        user = await self.user_mgr.get_by_qq_id(msg.user_id)
        if not user:
            import uuid
            auto_pw = uuid.uuid4().hex[:12]
            user = await self.user_mgr.create_user(
                qq_id=msg.user_id, password=auto_pw, nickname=f"QQ_{msg.user_id[-4:]}"
            )
            logger.info("Auto-registered QQ user: %s", msg.user_id)

        if msg.content.startswith("/"):
            handled = await self._handle_command(msg, user)
            if handled:
                return

        session = await self.session_mgr.get_or_create(
            user_id=user["id"],
            channel=msg.channel,
            channel_session_id=msg.session_key,
        )

        agent_session_id = await self.session_mgr.get_agent_session_id(session["id"])
        await self.session_mgr.append_message(session["id"], "user", msg.content)

        logger.info(
            "Processing: user=%s(%s), session=%s, agent_sid=%s, msg=%s",
            user["nickname"], msg.user_id, session["id"],
            (agent_session_id or "")[:8] if agent_session_id else "new",
            msg.content[:50],
        )

        await self.qq_bot.send_text(msg.session_key, "正在思考中...", reply_to=msg.message_id)

        import time
        last_progress_time = 0.0

        async def on_progress(kind: str, text: str):
            nonlocal last_progress_time
            now = time.monotonic()
            if now - last_progress_time < 8:
                return
            last_progress_time = now
            await self.qq_bot.send_text(msg.session_key, text, reply_to=msg.message_id)

        reply, new_agent_sid = await self.agent.run(
            user_message=msg.content,
            on_progress=on_progress,
            scheduler=self.scheduler,
            user=user,
            session_key=msg.session_key,
            workspace_id=msg.workspace_id,
            resume_session_id=agent_session_id,
        )

        if new_agent_sid and new_agent_sid != agent_session_id:
            await self.session_mgr.set_agent_session_id(session["id"], new_agent_sid)

        await self.session_mgr.append_message(session["id"], "assistant", reply)
        await self.qq_bot.send_text(msg.session_key, reply, reply_to=msg.message_id)

    async def _handle_command(self, msg: IncomingMessage, user: dict) -> bool:
        parts = msg.content.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            help_text = (
                "可用命令:\n"
                "/help - 显示帮助\n"
                "/clear - 清空当前会话历史\n"
                "/new - 新会话（清历史 + 清工作区文件）\n"
                "/files - 查看当前工作区文件\n"
                "/tasks - 查看我的定时任务\n"
                "/model - 查看/切换模型 (如 /model deepseek-v4-flash)"
            )
            if user["role"] == "admin":
                help_text += (
                    "\n\n管理员命令:\n"
                    "/adduser <QQ号> <密码> [昵称] - 添加用户\n"
                    "/users - 查看所有用户"
                )
            await self.qq_bot.send_text(msg.session_key, help_text)
            return True

        if cmd in ("/clear", "/new"):
            session = await self.session_mgr.get_or_create(
                user["id"], msg.channel, msg.session_key
            )
            await self.session_mgr.clear_session(session["id"])
            result = "会话已清空 ✓"

            if cmd == "/new":
                import shutil
                workspace_id = f"group_{msg.group_id}" if msg.is_group else msg.user_id
                workspace_dir = f"/app/data/workspace/{workspace_id}"
                if os.path.isdir(workspace_dir):
                    shutil.rmtree(workspace_dir)
                    os.makedirs(workspace_dir, exist_ok=True)
                result += "\n工作区文件已清空 ✓"

            await self.qq_bot.send_text(msg.session_key, result)
            return True

        if cmd == "/files":
            workspace_id = f"group_{msg.group_id}" if msg.is_group else msg.user_id
            workspace_dir = f"/app/data/workspace/{workspace_id}"
            if os.path.isdir(workspace_dir):
                files = []
                for name in sorted(os.listdir(workspace_dir)):
                    path = os.path.join(workspace_dir, name)
                    if os.path.isfile(path):
                        size = os.path.getsize(path)
                        if size > 1024 * 1024:
                            s = f"{size / 1024 / 1024:.1f}MB"
                        elif size > 1024:
                            s = f"{size / 1024:.1f}KB"
                        else:
                            s = f"{size}B"
                        files.append(f"  {name} ({s})")
                if files:
                    await self.qq_bot.send_text(msg.session_key, f"📁 工作区文件 ({workspace_id}):\n" + "\n".join(files))
                else:
                    await self.qq_bot.send_text(msg.session_key, "工作区为空")
            else:
                await self.qq_bot.send_text(msg.session_key, "工作区为空")
            return True

        if cmd == "/tasks":
            import json as _json
            tasks = await self.scheduler.list_tasks(owner_id=user["id"])
            if not tasks:
                await self.qq_bot.send_text(msg.session_key, "暂无定时任务")
            else:
                lines = ["📋 我的任务:"]
                for t in tasks:
                    params = _json.loads(t.get("params") or "{}") if isinstance(t.get("params"), str) else (t.get("params") or {})
                    schedule = "一次性" if not params.get("recurring", True) else "周期"
                    target = t["target_id"]
                    target_desc = f"群{target[6:]}" if target.startswith("group:") else f"私聊"
                    lines.append(f"[{t['id']}] {schedule} | {t['cron_expr']} | {target_desc} | {t['name']}")
                await self.qq_bot.send_text(msg.session_key, "\n".join(lines))
            return True

        if cmd == "/model":
            if not args.strip():
                active = get_active_model()
                lines = [f"当前模型: {active}", "", "可用模型:"]
                for key, preset in MODEL_PRESETS.items():
                    marker = " ← 当前" if key == active else ""
                    lines.append(f"  {key} - {preset.display_name}{marker}")
                lines.append("")
                lines.append("切换: /model <模型名>")
                await self.qq_bot.send_text(msg.session_key, "\n".join(lines))
            else:
                model_name = args.strip()
                if model_name not in MODEL_PRESETS:
                    await self.qq_bot.send_text(
                        msg.session_key,
                        f"未知模型: {model_name}\n可用: {', '.join(MODEL_PRESETS.keys())}",
                    )
                else:
                    set_active_model(model_name)
                    preset = MODEL_PRESETS[model_name]
                    await self.qq_bot.send_text(
                        msg.session_key,
                        f"模型已切换: {preset.display_name}",
                    )
                    logger.info("Model switched to: %s", model_name)
            return True

        if cmd == "/adduser" and user["role"] == "admin":
            return await self._cmd_adduser(msg, args)

        if cmd == "/users" and user["role"] == "admin":
            users = await self.user_mgr.list_users()
            lines = ["👥 用户列表:"]
            for u in users:
                status = "✓" if u["is_active"] else "✗"
                lines.append(f"  {status} {u['qq_id']} ({u['nickname']}) [{u['role']}]")
            await self.qq_bot.send_text(msg.session_key, "\n".join(lines))
            return True

        return False

    async def _cmd_adduser(self, msg: IncomingMessage, args: str) -> bool:
        parts = args.split()
        if len(parts) < 2:
            await self.qq_bot.send_text(
                msg.session_key, "用法: /adduser <QQ号> <密码> [昵称]"
            )
            return True

        qq_id, password = parts[0], parts[1]
        nickname = parts[2] if len(parts) > 2 else ""

        existing = await self.user_mgr.get_by_qq_id(qq_id)
        if existing:
            await self.qq_bot.send_text(msg.session_key, f"用户 {qq_id} 已存在")
            return True

        await self.user_mgr.create_user(qq_id, password, nickname)
        await self.qq_bot.send_text(msg.session_key, f"用户已添加: {qq_id} ✓")
        return True

    async def _scheduler_send(self, channel: str, target_id: str, text: str):
        if channel == "qq" and self.qq_bot:
            if target_id.startswith("group:"):
                await self.qq_bot.send_group_text(target_id[6:], text)
            else:
                await self.qq_bot.send_private_text(target_id, text)

    async def _scheduler_run_llm(self, task: dict):
        """Trigger an LLM task: run Agent with the cron's prompt and send result."""
        owner_id = task.get("owner_id", "")
        target_channel = task.get("target_channel", "qq")
        target_id = task.get("target_id", "")
        params = task.get("params", {})
        prompt = params.get("prompt", "")
        session_key = params.get("session_key", "")
        workspace_id = params.get("workspace_id", "")

        if not prompt:
            logger.warning("LLM task %s has empty prompt", task.get("task_id"))
            return

        user = None
        if owner_id:
            user_row = await self.user_mgr.get_by_id(owner_id)
            if user_row:
                user = user_row

        logger.info("Running LLM task: target=%s, prompt=%s", target_id, prompt[:60])

        try:
            reply, _ = await self.agent.run(
                user_message=prompt,
                user=user,
                session_key=session_key,
                workspace_id=workspace_id,
            )
            if reply:
                await self._scheduler_send(target_channel, target_id, reply)
        except Exception:
            logger.exception("LLM task %s failed", task.get("task_id"))


async def main():
    app = Application()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(app.stop()))

    try:
        await app.start()
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
