import asyncio
import json
import logging
import os
import re
import time
from typing import Callable, Awaitable

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    create_sdk_mcp_server,
)

from src.config import get_config, get_env, get_active_model, get_model_env, MODEL_PRESETS
from src.agent.tools import ALL_TOOLS, MCP_SERVER_NAME, TOOL_NAMES, set_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个部署在QQ上的AI助手，用中文回复，保持简洁友好。

输出格式：
- 不要使用 Markdown 语法（如 **加粗**、# 标题、- 列表、```代码块``` 等）
- 用纯文本排版，换行和空行分隔段落
- 用数字编号（1. 2. 3.）代替列表符号，用「」或引号代替加粗强调

行为准则：
- 文件读写在当前工作目录（cwd）下进行，使用相对路径
"""

ProgressCallback = Callable[[str, str], Awaitable[None]]


def _strip_markdown(text: str) -> str:
    """Convert markdown formatting to plain text for QQ display."""
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"「\1」", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"「\1」", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"```[\w]*\n?", "", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"^\s*[-*]\s+", "· ", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\(([^\)]+)\)", r"\1 (\2)", text)
    return text.strip()


class AgentRunner:
    def __init__(self):
        self.config = get_config()

        self._mcp_server = create_sdk_mcp_server(
            name=MCP_SERVER_NAME,
            version="1.0.0",
            tools=ALL_TOOLS,
        )

    async def run(
        self,
        user_message: str,
        system_prompt: str | None = None,
        on_progress: ProgressCallback | None = None,
        scheduler=None,
        user: dict | None = None,
        session_key: str | None = None,
        workspace_id: str | None = None,
        resume_session_id: str | None = None,
    ) -> tuple[str, str | None]:
        """Run agent. Returns (reply_text, agent_session_id)."""
        set_context(user, session_key)

        wid = workspace_id or (user["id"] if user else "default")
        user_workspace = f"/app/data/workspace/{wid}"
        os.makedirs(user_workspace, exist_ok=True)

        active_model = get_active_model()
        base_url, api_key, needs_proxy = get_model_env(active_model)
        os.environ["ANTHROPIC_BASE_URL"] = base_url
        os.environ["ANTHROPIC_API_KEY"] = api_key
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("HTTP_PROXY", None)

        preset = MODEL_PRESETS.get(active_model)
        sdk_model_name = preset.name if preset else active_model

        sys_prompt = system_prompt or SYSTEM_PROMPT
        logger.info("Using model: %s (%s), resume=%s", active_model, sdk_model_name, resume_session_id[:8] if resume_session_id else None)

        options_kwargs = dict(
            model=sdk_model_name,
            max_turns=self.config.model.max_turns,
            permission_mode="bypassPermissions",
            cwd=user_workspace,
            mcp_servers={MCP_SERVER_NAME: self._mcp_server},
            system_prompt={"type": "preset", "preset": "claude_code", "append": sys_prompt},
            disallowed_tools=["CronCreate", "CronList", "CronDelete"],
        )
        if resume_session_id:
            options_kwargs["resume"] = resume_session_id

        options = ClaudeAgentOptions(**options_kwargs)

        result_text = ""
        new_session_id = None
        turn_count = 0
        last_tool_use = None
        last_event_time = time.monotonic()
        heartbeat_task = None
        last_thinking = ""

        async def _heartbeat():
            nonlocal last_event_time, last_thinking
            while True:
                await asyncio.sleep(60)
                if on_progress and time.monotonic() - last_event_time >= 55:
                    elapsed = int(time.monotonic() - last_event_time)
                    if last_thinking:
                        summary = last_thinking[:200].replace("\n", " ")
                        await on_progress("heartbeat", f"💭 {summary}... ({elapsed}s)")
                    else:
                        await on_progress("heartbeat", f"⏳ 处理中... ({elapsed}s)")

        async def _run_client(opts):
            nonlocal result_text, new_session_id, turn_count, last_tool_use
            nonlocal last_event_time, heartbeat_task, last_thinking
            async with ClaudeSDKClient(options=opts) as client:
                await client.query(user_message)
                heartbeat_task = asyncio.create_task(_heartbeat())

                async for message in client.receive_response():
                    last_event_time = time.monotonic()
                    msg_type = type(message).__name__

                    if msg_type == "AssistantMessage":
                        for block in message.content:
                            block_type = type(block).__name__

                            if block_type == "ThinkingBlock" and on_progress:
                                thinking = getattr(block, "thinking", "")
                                if thinking and len(thinking) > 20:
                                    last_thinking = thinking
                                    summary = thinking[:200].replace("\n", " ")
                                    await on_progress("thinking", f"💭 {summary}...")

                            elif block_type == "TextBlock":
                                text = getattr(block, "text", "")
                                if text:
                                    result_text = text
                                    last_thinking = ""

                            elif block_type == "ToolUseBlock":
                                tool_name = getattr(block, "name", "unknown")
                                tool_input = getattr(block, "input", {})
                                last_thinking = ""
                                logger.info("Tool call: %s | %s", tool_name, json.dumps(tool_input, ensure_ascii=False)[:500])

                                if not on_progress:
                                    last_tool_use = tool_name
                                    continue

                                mcp_labels = {
                                    f"mcp__{MCP_SERVER_NAME}__web_search": "🔍 搜索网页",
                                    f"mcp__{MCP_SERVER_NAME}__web_fetch": "🌐 获取网页",
                                    f"mcp__{MCP_SERVER_NAME}__create_scheduled_task": "📋 创建定时任务",
                                    f"mcp__{MCP_SERVER_NAME}__list_my_tasks": "📋 查询任务",
                                    f"mcp__{MCP_SERVER_NAME}__delete_scheduled_task": "🗑️ 删除任务",
                                    f"mcp__{MCP_SERVER_NAME}__send_file_to_chat": "📤 发送文件",
                                }
                                builtin_labels = {
                                    "Bash": "⚙️ 执行命令",
                                    "Read": "📄 读取文件",
                                    "Write": "✏️ 写入文件",
                                    "Edit": "✏️ 编辑文件",
                                    "Glob": "🔍 搜索文件",
                                    "Grep": "🔍 搜索内容",
                                    "Skill": "🎯 使用技能",
                                    "Task": "🤖 子任务",
                                    "WebSearch": "🔍 搜索网页",
                                    "WebFetch": "🌐 获取网页",
                                    "TodoWrite": "📝 任务清单",
                                    "NotebookEdit": "📓 编辑笔记本",
                                }
                                label = mcp_labels.get(tool_name) or builtin_labels.get(tool_name) or f"🔧 {tool_name}"

                                param_str = ""
                                if tool_input:
                                    if isinstance(tool_input, dict):
                                        key_params = {k: v for k, v in tool_input.items()
                                                      if k not in ("script_content",) and v}
                                        if key_params:
                                            parts = []
                                            for k, v in list(key_params.items())[:3]:
                                                vs = str(v)
                                                if len(vs) > 60:
                                                    vs = vs[:60] + "..."
                                                parts.append(f"{k}={vs}")
                                            param_str = " | " + ", ".join(parts)

                                await on_progress("tool", f"{label}{param_str}")
                                last_tool_use = tool_name

                    elif msg_type == "ToolResultMessage" and on_progress:
                        turn_count += 1
                        if turn_count > 1:
                            await on_progress("progress", f"⏳ 处理中 (第{turn_count}轮)")

                    elif msg_type == "SystemMessage":
                        sid = getattr(message, "data", {}).get("session_id") if hasattr(message, "data") else None
                        if sid:
                            new_session_id = sid

                    elif msg_type == "ResultMessage":
                        if not result_text and hasattr(message, "result"):
                            result_text = message.result or ""
                        sid = getattr(message, "session_id", None)
                        if sid:
                            new_session_id = sid
                        logger.info(
                            "Agent completed: turns=%s, cost=$%.4f, session=%s",
                            getattr(message, "num_turns", "?"),
                            getattr(message, "total_cost_usd", 0),
                            (new_session_id or "")[:12],
                        )

        try:
            await _run_client(options)
        except Exception as e:
            if resume_session_id and "conversation" in str(e).lower():
                logger.warning("Resume failed (session lost), retrying without resume: %s", e)
                options_kwargs.pop("resume", None)
                fallback_options = ClaudeAgentOptions(**options_kwargs)
                try:
                    await _run_client(fallback_options)
                except Exception as e2:
                    logger.error("Agent error (retry): %s", e2, exc_info=True)
                    result_text = f"抱歉，处理消息时出错了: {type(e2).__name__}"
            else:
                logger.error("Agent error: %s", e, exc_info=True)
                result_text = f"抱歉，处理消息时出错了: {type(e).__name__}"
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
            set_context(None, None)

        reply = _strip_markdown(result_text) if result_text else "（无回复）"
        return reply, new_session_id

