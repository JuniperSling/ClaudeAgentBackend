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

from src.config import get_config, get_env
from src.agent.tools import ALL_TOOLS, MCP_SERVER_NAME, TOOL_NAMES, set_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个部署在QQ上的AI助手，用中文回复，保持简洁。

重要格式规则：
- 不要使用Markdown格式（不要用 **加粗**、# 标题、- 列表、```代码块``` 等）
- 用纯文本排版，用换行和空行来分隔段落
- 用数字编号（1. 2. 3.）代替列表符号
- 用「」或引号代替加粗强调

关键规则：
1. 你可以使用web_search搜索网页。当用户问天气、新闻、价格、实时信息或你不确定的事情时，必须使用web_search搜索，不要说你做不到。
2. 你有定时任务管理工具。当用户要求创建/查看/删除定时任务时，必须调用对应工具，不要只用文字描述。
3. 所有文件操作（创建、读取、写入）必须在当前工作目录下进行，不要使用/tmp或其他路径。用相对路径即可。
4. 需要发送文件给用户时，用send_file_to_chat工具，file_path用当前工作目录下的绝对路径。

工具说明：
- create_scheduled_task：cron_expr是5字段格式（分 时 日 月 周几），最小间隔1分钟。script_content是Python脚本，其stdout会作为消息发送。消息自动发送到当前对话（群聊发群里，私聊发私聊）。
- list_my_tasks：无需参数。
- delete_scheduled_task：需要task_id。
- send_file_to_chat：发送文件给用户。file_path是服务器上的绝对路径，file_name是显示名。留空target_session则发到当前对话。
- web_search：搜索网页，返回标题、链接和摘要。
- web_fetch：抓取网页内容。
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
        env = get_env()
        os.environ["ANTHROPIC_BASE_URL"] = env.anthropic_base_url
        os.environ["ANTHROPIC_API_KEY"] = env.anthropic_api_key

        self._mcp_server = create_sdk_mcp_server(
            name=MCP_SERVER_NAME,
            version="1.0.0",
            tools=ALL_TOOLS,
        )

    async def run(
        self,
        user_message: str,
        history: list[dict] | None = None,
        system_prompt: str | None = None,
        on_progress: ProgressCallback | None = None,
        scheduler=None,
        user: dict | None = None,
        session_key: str | None = None,
        workspace_id: str | None = None,
    ) -> str:
        set_context(user, session_key)

        wid = workspace_id or (user["id"] if user else "default")
        user_workspace = f"/app/data/workspace/{wid}"
        os.makedirs(user_workspace, exist_ok=True)

        workspace_files = self._list_workspace_files(user_workspace)
        prompt = self._build_prompt(user_message, history, system_prompt, workspace_files)
        logger.debug("Agent prompt: %s", prompt[:200])

        options = ClaudeAgentOptions(
            model=self.config.model.name,
            max_turns=self.config.model.max_turns,
            permission_mode="bypassPermissions",
            cwd=user_workspace,
            mcp_servers={MCP_SERVER_NAME: self._mcp_server},
        )

        result_text = ""
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

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
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

                    elif msg_type == "ResultMessage":
                        if not result_text and hasattr(message, "result"):
                            result_text = message.result or ""
                        logger.info(
                            "Agent completed: turns=%s, cost=$%.4f",
                            getattr(message, "num_turns", "?"),
                            getattr(message, "total_cost_usd", 0),
                        )

        except Exception as e:
            logger.error("Agent error: %s", e, exc_info=True)
            result_text = f"抱歉，处理消息时出错了: {type(e).__name__}"
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
            set_context(None, None)

        return _strip_markdown(result_text) if result_text else "（无回复）"

    def _list_workspace_files(self, workspace: str) -> list[str]:
        if not os.path.isdir(workspace):
            return []
        files = []
        for name in sorted(os.listdir(workspace)):
            path = os.path.join(workspace, name)
            if os.path.isfile(path):
                size = os.path.getsize(path)
                if size > 1024 * 1024:
                    size_str = f"{size / 1024 / 1024:.1f}MB"
                elif size > 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size}B"
                files.append(f"  {name} ({size_str})")
        return files

    def _build_prompt(
        self,
        user_message: str,
        history: list[dict] | None,
        system_prompt: str | None,
        workspace_files: list[str] | None = None,
        extra_context: str | None = None,
    ) -> str:
        parts = []
        sys_prompt = system_prompt or SYSTEM_PROMPT
        parts.append(sys_prompt.strip())
        parts.append("")

        if extra_context:
            parts.append(extra_context)
            parts.append("")

        if workspace_files:
            parts.append("当前工作区文件:")
            parts.extend(workspace_files)
            parts.append("")

        if history:
            parts.append("Recent conversation:")
            for msg in history[-10:]:
                role = "User" if msg.get("role") == "user" else "Assistant"
                parts.append(f"{role}: {msg.get('content', '')}")
            parts.append("")

        parts.append(f"User: {user_message}")
        return "\n".join(parts)
