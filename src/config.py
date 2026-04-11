import os
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class ModelConfig(BaseModel):
    name: str = "glm-4.5-flash"
    max_turns: int = 30


class NapCatConfig(BaseModel):
    ws_url: str = "ws://napcat:3001"
    http_url: str = "http://napcat:3000"


class SessionConfig(BaseModel):
    ttl_hours: int = 24
    max_history: int = 50


class SchedulerConfig(BaseModel):
    task_timeout_seconds: int = 300
    max_tasks_per_user: int = 10


class EnvSettings(BaseSettings):
    anthropic_base_url: str = "https://open.bigmodel.cn/api/anthropic"
    anthropic_api_key: str = ""
    admin_qq_id: str = ""
    admin_password: str = "changeme"

    model_config = {"env_file": ".env", "extra": "ignore"}


class AppConfig(BaseModel):
    model: ModelConfig = ModelConfig()
    napcat: NapCatConfig = NapCatConfig()
    session: SessionConfig = SessionConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    database_path: str = "data/claude_agent.db"
    data_dir: str = "data"


def load_config(config_path: str | None = None) -> AppConfig:
    if config_path is None:
        candidates = ["config/config.yaml", "config/config.example.yaml"]
        for c in candidates:
            if Path(c).exists():
                config_path = c
                break

    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        if "database" in raw and "path" in raw["database"]:
            raw["database_path"] = raw["database"]["path"]
            del raw["database"]
        return AppConfig(**raw)

    return AppConfig()


_env: EnvSettings | None = None
_config: AppConfig | None = None


def get_env() -> EnvSettings:
    global _env
    if _env is None:
        _env = EnvSettings()
    return _env


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def init_config(config_path: str | None = None):
    global _config, _env
    _env = EnvSettings()
    _config = load_config(config_path)

    os.environ.setdefault("ANTHROPIC_BASE_URL", _env.anthropic_base_url)
    os.environ.setdefault("ANTHROPIC_API_KEY", _env.anthropic_api_key)
