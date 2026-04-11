from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TaskContext:
    task_id: str
    owner_id: str
    params: dict = field(default_factory=dict)
    data_dir: str = ""


@dataclass
class TaskResult:
    text: str | None = None
    target_channel: str = "qq"
    target_id: str = ""
    success: bool = True
    error: str | None = None


class BaseTask(ABC):
    name: str = "base"

    @abstractmethod
    async def execute(self, context: TaskContext) -> TaskResult:
        ...
