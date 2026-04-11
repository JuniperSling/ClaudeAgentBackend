from abc import ABC, abstractmethod
from typing import Callable, Awaitable


class IncomingMessage:
    """Standardized incoming message from any channel."""

    def __init__(
        self,
        channel: str,
        user_id: str,
        content: str,
        session_key: str,
        is_group: bool = False,
        group_id: str | None = None,
        message_id: str | None = None,
        workspace_id: str | None = None,
        raw: dict | None = None,
    ):
        self.channel = channel
        self.user_id = user_id
        self.content = content
        self.session_key = session_key
        self.is_group = is_group
        self.group_id = group_id
        self.message_id = message_id
        self.workspace_id = workspace_id
        self.raw = raw or {}


MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class BaseChannel(ABC):
    """Minimal channel interface. Implementations retain full native API access."""

    @abstractmethod
    async def start(self, on_message: MessageHandler):
        ...

    @abstractmethod
    async def stop(self):
        ...

    @abstractmethod
    async def send_text(self, session_key: str, text: str, **kwargs):
        ...
