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
    "Create a scheduled task that runs on a cron schedule. "
    "The task will execute a Python script and send its stdout as a QQ message. "
    "cron_expr uses 5-field cron format: minute hour day month weekday. "
    "Examples: '*/1 * * * *' = every minute, '*/30 * * * *' = every 30 min, "
    "'0 9 * * *' = daily 9am. Minimum interval is 1 minute. "
    "By default, messages are sent back to the same chat (group or private) "
    "where the user made the request. Only set target_override if the user "
    "explicitly asks to send to a different place.",
    {
        "name": str,
        "cron_expr": str,
        "script_content": str,
        "target_override": str,
    },
)
async def create_scheduled_task(args: dict[str, Any]) -> dict[str, Any]:
    if not _current_user_id:
        return _error("User context not available")

    import os
    script_dir = f"/app/data/tasks/{_current_user_id}"
    os.makedirs(script_dir, exist_ok=True)

    import uuid
    script_filename = f"{uuid.uuid4().hex[:8]}.py"
    script_path = os.path.join(script_dir, script_filename)

    with open(script_path, "w") as f:
        f.write(args["script_content"])

    target_override = args.get("target_override", "").strip()
    if target_override:
        target_id = target_override
    elif _current_session_key:
        parts = _current_session_key.split(":", 2)
        if len(parts) == 3 and parts[1] == "group":
            target_id = f"group:{parts[2]}"
        else:
            target_id = _current_user_qq
    else:
        target_id = _current_user_qq

    result = _api_call("/task/add", {
        "owner_id": _current_user_id,
        "name": args["name"],
        "cron_expr": args["cron_expr"],
        "target_channel": "qq",
        "target_id": target_id,
        "task_type": "script",
        "params": {"script_filename": script_filename},
        "script_path": script_path,
    })

    if "error" in result:
        return _error(result["error"])

    target_desc = "当前群聊" if target_id.startswith("group:") else f"私聊 {target_id}"
    return _ok(
        f"Task created successfully!\n"
        f"  ID: {result['task_id']}\n"
        f"  Name: {args['name']}\n"
        f"  Schedule: {args['cron_expr']}\n"
        f"  Target: {target_desc}"
    )


@tool(
    "list_my_tasks",
    "List all scheduled tasks owned by the current user.",
    {},
)
async def list_my_tasks(args: dict[str, Any]) -> dict[str, Any]:
    if not _current_user_id:
        return _error("User context not available")

    result = _api_call("/task/list", {"owner_id": _current_user_id})
    if "error" in result:
        return _error(result["error"])

    tasks = result.get("tasks", [])
    if not tasks:
        return _ok("No scheduled tasks found.")

    lines = []
    for t in tasks:
        lines.append(
            f"- [{t['id']}] {t['name']}\n"
            f"  Schedule: {t['cron_expr']} | Status: {t['status']}\n"
            f"  Target: {t['target_channel']}:{t['target_id']}"
        )
    return _ok(f"Found {len(tasks)} task(s):\n\n" + "\n\n".join(lines))


@tool(
    "delete_scheduled_task",
    "Delete a scheduled task by its ID. Only tasks owned by the current user can be deleted.",
    {"task_id": str},
)
async def delete_scheduled_task(args: dict[str, Any]) -> dict[str, Any]:
    if not _current_user_id:
        return _error("User context not available")

    result = _api_call("/task/delete", {
        "task_id": args["task_id"],
        "owner_id": _current_user_id,
    })
    if "error" in result:
        return _error(result["error"])
    return _ok(f"Task {args['task_id']} deleted successfully.")


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
    "Search the web using Google (via Serper API). Returns top results with titles, URLs and snippets. "
    "Use this for weather, news, real-time info, or anything you need to look up.",
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
    "Fetch the text content of a webpage by URL. Useful for reading articles, documentation, etc.",
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
    "Send a file to the current chat (or a specified target). "
    "file_path must be an absolute path on the server. "
    "If target_session is empty, the file is sent to the current conversation. "
    "Use this after generating/processing a file that the user wants to receive.",
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
