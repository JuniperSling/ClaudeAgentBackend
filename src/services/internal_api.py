"""Lightweight internal HTTP API for MCP tools to call back into the main process."""

import asyncio
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

logger = logging.getLogger(__name__)

_app_ref = None
_server: HTTPServer | None = None
INTERNAL_PORT = 9199


def start_internal_api(app):
    global _app_ref, _server
    _app_ref = app

    _server = HTTPServer(("127.0.0.1", INTERNAL_PORT), _Handler)
    t = threading.Thread(target=_server.serve_forever, daemon=True)
    t.start()
    logger.info("Internal API started on port %d", INTERNAL_PORT)


def stop_internal_api():
    global _server
    if _server:
        _server.shutdown()
        _server = None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/task/add":
            self._handle_task_add(body)
        elif self.path == "/task/list":
            self._handle_task_list(body)
        elif self.path == "/task/delete":
            self._handle_task_delete(body)
        elif self.path == "/user/info":
            self._handle_user_info(body)
        elif self.path == "/file/send":
            self._handle_file_send(body)
        elif self.path == "/msg/send":
            self._handle_msg_send(body)
        else:
            self._reply(404, {"error": "not found"})

    def _handle_task_add(self, body: dict):
        app = _app_ref
        if not app or not app.scheduler:
            self._reply(500, {"error": "scheduler not available"})
            return

        loop = asyncio.new_event_loop()
        try:
            task_id = loop.run_until_complete(app.scheduler.add_task(
                owner_id=body["owner_id"],
                name=body["name"],
                cron_expr=body["cron_expr"],
                target_channel=body.get("target_channel", "qq"),
                target_id=body["target_id"],
                task_type=body.get("task_type", "script"),
                params=body.get("params"),
                script_path=body.get("script_path"),
            ))
            self._reply(200, {"task_id": task_id})
        except Exception as e:
            self._reply(400, {"error": str(e)})
        finally:
            loop.close()

    def _handle_task_list(self, body: dict):
        app = _app_ref
        if not app or not app.scheduler:
            self._reply(500, {"error": "scheduler not available"})
            return

        loop = asyncio.new_event_loop()
        try:
            tasks = loop.run_until_complete(
                app.scheduler.list_tasks(owner_id=body.get("owner_id"))
            )
            self._reply(200, {"tasks": tasks})
        except Exception as e:
            self._reply(400, {"error": str(e)})
        finally:
            loop.close()

    def _handle_task_delete(self, body: dict):
        app = _app_ref
        if not app or not app.scheduler:
            self._reply(500, {"error": "scheduler not available"})
            return

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                app.scheduler.remove_task(body["task_id"], owner_id=body.get("owner_id"))
            )
            self._reply(200, {"ok": True})
        except Exception as e:
            self._reply(400, {"error": str(e)})
        finally:
            loop.close()

    def _handle_user_info(self, body: dict):
        app = _app_ref
        if not app:
            self._reply(500, {"error": "app not available"})
            return

        loop = asyncio.new_event_loop()
        try:
            user = loop.run_until_complete(
                app.user_mgr.get_by_qq_id(body["qq_id"])
            )
            if user:
                self._reply(200, {
                    "qq_id": user["qq_id"],
                    "nickname": user["nickname"],
                    "role": user["role"],
                    "id": user["id"],
                })
            else:
                self._reply(404, {"error": "user not found"})
        except Exception as e:
            self._reply(400, {"error": str(e)})
        finally:
            loop.close()

    def _handle_file_send(self, body: dict):
        app = _app_ref
        if not app or not app.qq_bot:
            self._reply(500, {"error": "qq bot not available"})
            return

        try:
            app.qq_bot._send_file_sync(
                session_key=body["session_key"],
                file_path=body["file_path"],
                file_name=body.get("file_name"),
            )
            self._reply(200, {"ok": True})
        except Exception as e:
            self._reply(400, {"error": str(e)})

    def _handle_msg_send(self, body: dict):
        app = _app_ref
        if not app or not app.qq_bot:
            self._reply(500, {"error": "qq bot not available"})
            return

        import httpx
        session_key = body.get("session_key", "")
        text = body.get("text", "")
        parts = session_key.split(":")
        if len(parts) < 3:
            self._reply(400, {"error": f"invalid session_key: {session_key}"})
            return

        channel_type, target_id = parts[1], parts[2]
        try:
            with httpx.Client(base_url=app.qq_bot.http_url, timeout=15) as client:
                message = [{"type": "text", "data": {"text": text}}]
                if channel_type == "group":
                    client.post("/send_group_msg", json={"group_id": int(target_id), "message": message})
                else:
                    client.post("/send_private_msg", json={"user_id": int(target_id), "message": message})
            self._reply(200, {"ok": True})
        except Exception as e:
            self._reply(400, {"error": str(e)})

    def _reply(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
