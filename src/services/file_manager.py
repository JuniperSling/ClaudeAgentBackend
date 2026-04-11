import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class FileManager:
    """Shared file storage service. Each user gets an isolated directory."""

    def __init__(self, base_dir: str = "data"):
        self.base_dir = Path(base_dir)

    def get_user_dir(self, user_id: str) -> Path:
        user_dir = self.base_dir / "user_data" / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def get_task_dir(self, user_id: str) -> Path:
        task_dir = self.base_dir / "tasks" / user_id
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    def get_shared_dir(self) -> Path:
        shared = self.base_dir / "shared"
        shared.mkdir(parents=True, exist_ok=True)
        return shared
