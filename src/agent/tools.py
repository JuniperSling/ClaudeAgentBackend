"""Custom MCP tools for Claude Agent — task scheduling, web search, etc.
Tools run in a subprocess, so they communicate with the main process via internal HTTP API.
"""

import json
import logging
import os
import urllib.request
import urllib.error
from typing import Any

from claude_agent_sdk import tool

logger = logging.getLogger(__name__)

INTERNAL_API = "http://127.0.0.1:9199"

_current_user_id = ""
_current_user_qq = ""
_current_session_key = ""


def set_context(user: dict | None, session_key: str | None):
    global _current_user_id, _current_user_qq, _current_session_key
    _current_user_id = (user or {}).get("id", "")
    _current_user_qq = (user or {}).get("qq_id", "")
    _current_session_key = session_key or ""


def _api_call(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{INTERNAL_API}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        try:
            return json.loads(err_body)
        except Exception:
            return {"error": err_body}
    except Exception as e:
        return {"error": str(e)}


@tool(
    "create_scheduled_task",
    "Schedule a recurring or one-shot prompt. At each fire time, the prompt is "
    "enqueued as a new user message and the agent's reply is sent back to the chat "
    "where this task was created. Returns a job ID you can pass to delete_scheduled_task. "
    "cron_expr: standard 5-field cron expression in local time \"M H DoM Mon DoW\" "
    "(e.g. \"*/5 * * * *\" = every 5 minutes, \"30 14 28 2 *\" = Feb 28 at 2:30pm). "
    "prompt: the prompt to enqueue at each fire time. "
    "recurring: true = fire on every cron match until deleted, false = fire once and auto-delete.",
    {"cron_expr": str, "prompt": str, "recurring": bool, "name": str},
)
async def create_scheduled_task(args: dict[str, Any]) -> dict[str, Any]:
    if not _current_user_id:
        return _error("User context not available")

    result = _api_call("/cron/create", {
        "owner_id": _current_user_id,
        "qq_id": _current_user_qq,
        "session_key": _current_session_key,
        "cron": args["cron_expr"],
        "recurring": args.get("recurring", True),
        "prompt": args["prompt"],
        "name": args.get("name") or args["prompt"][:30],
    })
    if "error" in result:
        return _error(result["error"])

    target = "当前群聊" if _current_session_key.startswith("qq:group:") else "私聊"
    schedule = "一次性" if not args.get("recurring", True) else "周期性"
    return _ok(
        f"已创建{schedule}定时任务\n"
        f"  ID: {result['task_id']}\n"
        f"  Cron: {args['cron_expr']}\n"
        f"  发送目标: {target}"
    )


@tool(
    "list_my_tasks",
    "List all active scheduled tasks.",
    {},
)
async def list_my_tasks(args: dict[str, Any]) -> dict[str, Any]:
    if not _current_user_id:
        return _error("User context not available")

    result = _api_call("/cron/list", {"owner_id": _current_user_id})
    if "error" in result:
        return _error(result["error"])

    tasks = result.get("tasks", [])
    if not tasks:
        return _ok("当前没有定时任务")

    lines = []
    for t in tasks:
        schedule = "一次性" if not t.get("recurring", True) else "周期"
        target_id = t.get("target_id", "")
        target_desc = f"群{target_id[6:]}" if target_id.startswith("group:") else f"私聊"
        lines.append(f"[{t['id']}] {schedule} | {t['cron']} | {target_desc} | {t['prompt'][:50]}")
    return _ok("当前定时任务:\n" + "\n".join(lines))


@tool(
    "delete_scheduled_task",
    "Cancel a scheduled task previously created with create_scheduled_task.",
    {"task_id": str},
)
async def delete_scheduled_task(args: dict[str, Any]) -> dict[str, Any]:
    if not _current_user_id:
        return _error("User context not available")

    result = _api_call("/cron/delete", {
        "owner_id": _current_user_id,
        "task_id": args["task_id"],
    })
    if "error" in result:
        return _error(result["error"])
    return _ok(f"已删除任务 {args['task_id']}")


@tool(
    "get_current_user_info",
    "Get information about the current user (QQ ID, nickname, role).",
    {},
)
async def get_current_user_info(args: dict[str, Any]) -> dict[str, Any]:
    if not _current_user_qq:
        return _error("User context not available")

    return _ok(
        f"QQ ID: {_current_user_qq}\n"
        f"User ID: {_current_user_id}\n"
        f"Session: {_current_session_key or 'unknown'}"
    )


@tool(
    "web_search",
    "Search the web using Google. Returns top results with titles, URLs and snippets.",
    {"query": str, "max_results": int},
)
async def web_search(args: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.dumps({"q": args["query"], "num": min(args.get("max_results", 5), 10)}).encode()
        req = urllib.request.Request(
            "https://google.serper.dev/search",
            data=data,
            headers={
                "X-API-KEY": os.environ.get("SERPER_API_KEY", ""),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())

        lines = []
        if result.get("answerBox"):
            ab = result["answerBox"]
            lines.append(f"**Answer: {ab.get('answer') or ab.get('snippet', '')}**")
            lines.append("")

        if result.get("knowledgeGraph"):
            kg = result["knowledgeGraph"]
            lines.append(f"**{kg.get('title', '')}** - {kg.get('description', '')}")
            lines.append("")

        for r in result.get("organic", [])[:args.get("max_results", 5)]:
            lines.append(f"**{r.get('title', '')}**")
            lines.append(f"URL: {r.get('link', '')}")
            lines.append(f"{r.get('snippet', '')}")
            lines.append("")

        if not lines:
            return _ok("No search results found.")
        return _ok("\n".join(lines))
    except Exception as e:
        return _error(f"Search failed: {e}")


@tool(
    "web_fetch",
    "Fetch the text content of a webpage by URL.",
    {"url": str},
)
async def web_fetch(args: dict[str, Any]) -> dict[str, Any]:
    try:
        req = urllib.request.Request(args["url"], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode(errors="replace")
        if len(text) > 8000:
            text = text[:8000] + "\n\n...(truncated)"
        return _ok(text)
    except Exception as e:
        return _error(f"Fetch failed: {e}")


@tool(
    "send_file_to_chat",
    "Send a file to the chat. file_path must be an absolute path on the server. "
    "Leave target_session empty to send to the current conversation.",
    {"file_path": str, "file_name": str, "target_session": str},
)
async def send_file_to_chat(args: dict[str, Any]) -> dict[str, Any]:
    session = args.get("target_session", "").strip() or _current_session_key
    if not session:
        return _error("No target session available")

    file_path = args["file_path"]
    import os
    if not os.path.exists(file_path):
        return _error(f"File not found: {file_path}")

    result = _api_call("/file/send", {
        "session_key": session,
        "file_path": file_path,
        "file_name": args.get("file_name", os.path.basename(file_path)),
    })

    if "error" in result:
        return _error(result["error"])
    return _ok(f"File sent: {args.get('file_name', file_path)}")


ALL_TOOLS = [
    create_scheduled_task,
    list_my_tasks,
    delete_scheduled_task,
    get_current_user_info,
    web_search,
    web_fetch,
    send_file_to_chat,
]

MCP_SERVER_NAME = "agent_tools"

TOOL_NAMES = [f"mcp__{MCP_SERVER_NAME}__{t.name}" for t in ALL_TOOLS]


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"Error: {text}"}], "is_error": True}
