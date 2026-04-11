import asyncio
import json
import logging
import os

import httpx
import websockets

from src.channels.base import BaseChannel, IncomingMessage, MessageHandler

logger = logging.getLogger(__name__)

MAX_QQ_MSG_LENGTH = 3000


class QQBot(BaseChannel):
    """QQ channel adapter via NapCatQQ OneBot v11 WebSocket + HTTP."""

    def __init__(self, ws_url: str, http_url: str):
        self.ws_url = ws_url
        self.http_url = http_url
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._on_message: MessageHandler | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._http = httpx.AsyncClient(base_url=http_url, timeout=30)

    async def start(self, on_message: MessageHandler):
        self._on_message = on_message
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("QQBot starting, ws=%s", self.ws_url)

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
        await self._http.aclose()
        logger.info("QQBot stopped")

    async def _run_loop(self):
        """Reconnect loop for WebSocket."""
        retry_delay = 2
        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    retry_delay = 2
                    logger.info("QQBot WebSocket connected")
                    await self._listen(ws)
            except (
                websockets.ConnectionClosed,
                ConnectionRefusedError,
                OSError,
            ) as e:
                logger.warning("QQBot WS disconnected: %s, retry in %ds", e, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("QQBot unexpected error")
                await asyncio.sleep(retry_delay)

    async def _listen(self, ws: websockets.WebSocketClientProtocol):
        async for raw in ws:
            try:
                event = json.loads(raw)
                await self._handle_event(event)
            except Exception:
                logger.exception("Error handling QQ event")

    async def _handle_event(self, event: dict):
        post_type = event.get("post_type")

        if post_type != "message":
            return

        msg_type = event.get("message_type")
        user_id = str(event.get("user_id", ""))
        raw_message = event.get("raw_message", "").strip()
        group_id = str(event.get("group_id", "")) if event.get("group_id") else None

        if not raw_message or not user_id:
            return

        if "[CQ:file" in raw_message or "[CQ:image" in raw_message:
            logger.info("File event raw message segments: %s", json.dumps(event.get("message", []), ensure_ascii=False)[:1000])

        import re
        raw_message = re.sub(r"\[CQ:reply,id=[^\]]*\]\s*", "", raw_message).strip()

        is_group = msg_type == "group"
        session_key = f"qq:group:{group_id}" if is_group else f"qq:c2c:{user_id}"
        message_id = str(event.get("message_id", ""))

        from src.services.file_handler import parse_cq_files, download_file, extract_text
        cq_files = parse_cq_files(raw_message)
        has_files = bool(cq_files)

        is_slash_cmd = raw_message.lstrip().startswith("/")

        if is_group and not has_files and not is_slash_cmd:
            self_id = str(event.get("self_id", ""))
            if not re.search(rf"\[CQ:at,qq={self_id}\]", raw_message):
                return
            raw_message = re.sub(r"\[CQ:at,qq=\d+\]\s*", "", raw_message).strip()
            if not raw_message:
                return
        file_context = ""
        workspace_id = f"group_{group_id}" if is_group else user_id
        workspace_dir = f"/app/data/workspace/{workspace_id}"

        if cq_files:
            downloaded_names = []
            for cqf in cq_files:
                raw_message = raw_message.replace(cqf["raw"], "").strip()
                file_id = cqf["params"].get("file_id", "")
                file_name = cqf["params"].get("file", cqf["params"].get("file_name", "unknown"))
                if not file_id:
                    continue

                file_info = await download_file(self._http, file_id, workspace_dir)
                if file_info:
                    local_path = file_info.get("local_path", "")
                    downloaded_names.append(file_name)
                    if raw_message:
                        extracted = extract_text(local_path) if local_path else None
                        if extracted:
                            file_context += f"\n\n[文件: {file_name}]\n{extracted}"
                        else:
                            file_context += f"\n\n[文件: {file_name}] (已保存到工作区)"
                else:
                    file_context += f"\n\n[文件: {file_name}] (下载失败)"

            if not raw_message and downloaded_names:
                names_str = ", ".join(downloaded_names)
                try:
                    await self._http.post(
                        "/send_private_msg" if not is_group else "/send_group_msg",
                        json={
                            ("user_id" if not is_group else "group_id"): int(group_id if is_group else user_id),
                            "message": [
                                {"type": "reply", "data": {"id": str(event.get("message_id", ""))}},
                                {"type": "text", "data": {"text": f"📎 已收到文件: {names_str}\n文件已保存到工作区，你可以告诉我需要做什么处理。"}},
                            ],
                        },
                    )
                except Exception:
                    logger.exception("Failed to send file receipt")
                return

        if not raw_message and not file_context:
            return

        content = raw_message
        if file_context:
            content = (content + file_context).strip()

        incoming = IncomingMessage(
            channel="qq",
            user_id=user_id,
            content=content,
            session_key=session_key,
            is_group=is_group,
            group_id=group_id,
            message_id=message_id,
            workspace_id=workspace_id,
            raw=event,
        )

        if self._on_message:
            await self._on_message(incoming)

    async def send_text(self, session_key: str, text: str, reply_to: str | None = None, **kwargs):
        parts = session_key.split(":")
        if len(parts) < 3:
            logger.error("Invalid session_key: %s", session_key)
            return

        channel_type = parts[1]
        target_id = parts[2]

        chunks = self._split_message(text)
        for i, chunk in enumerate(chunks):
            message = []
            if reply_to and i == 0:
                message.append({"type": "reply", "data": {"id": reply_to}})
            message.append({"type": "text", "data": {"text": chunk}})
            try:
                if channel_type == "group":
                    await self._http.post(
                        "/send_group_msg",
                        json={"group_id": int(target_id), "message": message},
                    )
                else:
                    await self._http.post(
                        "/send_private_msg",
                        json={"user_id": int(target_id), "message": message},
                    )
            except Exception:
                logger.exception("Failed to send QQ message to %s", session_key)

    async def send_group_text(self, group_id: str | int, text: str):
        """Direct group message send for scheduled tasks etc."""
        chunks = self._split_message(text)
        for chunk in chunks:
            message = [{"type": "text", "data": {"text": chunk}}]
            await self._http.post(
                "/send_group_msg",
                json={"group_id": int(group_id), "message": message},
            )

    async def send_private_text(self, user_id: str | int, text: str):
        """Direct private message send."""
        chunks = self._split_message(text)
        for chunk in chunks:
            message = [{"type": "text", "data": {"text": chunk}}]
            await self._http.post(
                "/send_private_msg",
                json={"user_id": int(user_id), "message": message},
            )

    async def send_file(self, session_key: str, file_path: str, file_name: str | None = None):
        """Send a file via base64 encoding (works across containers)."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._send_file_sync, session_key, file_path, file_name
        )

    def _send_file_sync(self, session_key: str, file_path: str, file_name: str | None = None):
        """Synchronous file send (safe to call from any event loop context)."""
        import base64
        parts = session_key.split(":")
        if len(parts) < 3:
            return

        channel_type = parts[1]
        target_id = parts[2]
        if not file_name:
            file_name = os.path.basename(file_path)

        try:
            with open(file_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            file_uri = f"base64://{b64}"

            with httpx.Client(base_url=self.http_url, timeout=60) as client:
                if channel_type == "group":
                    client.post(
                        "/upload_group_file",
                        json={"group_id": int(target_id), "file": file_uri, "name": file_name},
                    )
                else:
                    client.post(
                        "/upload_private_file",
                        json={"user_id": int(target_id), "file": file_uri, "name": file_name},
                    )
            logger.info("File sent: %s -> %s", file_name, session_key)
        except Exception:
            logger.exception("Failed to send file %s to %s", file_name, session_key)

    def _split_message(self, text: str) -> list[str]:
        if len(text) <= MAX_QQ_MSG_LENGTH:
            return [text]

        chunks = []
        while text:
            if len(text) <= MAX_QQ_MSG_LENGTH:
                chunks.append(text)
                break
            split_pos = text.rfind("\n", 0, MAX_QQ_MSG_LENGTH)
            if split_pos == -1:
                split_pos = MAX_QQ_MSG_LENGTH
            chunks.append(text[:split_pos])
            text = text[split_pos:].lstrip("\n")
        return chunks
