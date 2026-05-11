import os
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class ModelPreset(BaseModel):
    name: str
    display_name: str = ""
    base_url: str = ""
    api_key_env: str = ""
    needs_proxy: bool = False


MODEL_PRESETS: dict[str, ModelPreset] = {
    "glm-4.7": ModelPreset(
        name="glm-4.7",
        display_name="GLM-4.7 (智谱旗舰)",
        base_url="https://open.bigmodel.cn/api/anthropic",
        api_key_env="ZHIPU_API_KEY",
    ),
    "glm-5.1": ModelPreset(
        name="glm-5.1",
        display_name="GLM-5.1 (智谱)",
        base_url="https://open.bigmodel.cn/api/anthropic",
        api_key_env="ZHIPU_API_KEY",
    ),
    "deepseek-v4-flash": ModelPreset(
        name="deepseek-v4-flash",
        display_name="DeepSeek V4 Flash (快速)",
        base_url="https://api.deepseek.com/anthropic",
        api_key_env="DEEPSEEK_API_KEY",
    ),
    "deepseek-v4-pro": ModelPreset(
        name="deepseek-v4-pro",
        display_name="DeepSeek V4 Pro (强力)",
        base_url="https://api.deepseek.com/anthropic",
        api_key_env="DEEPSEEK_API_KEY",
    ),
    "claude-opus-4.7": ModelPreset(
        name="anthropic/claude-opus-4.7",
        display_name="Claude Opus 4.7 (OpenRouter)",
        base_url="http://127.0.0.1:9198",
        api_key_env="OPENROUTER_API_KEY",
        needs_proxy=True,
    ),
    "claude-sonnet-4.6": ModelPreset(
        name="anthropic/claude-sonnet-4.6",
        display_name="Claude Sonnet 4.6 (OpenRouter)",
        base_url="http://127.0.0.1:9198",
        api_key_env="OPENROUTER_API_KEY",
        needs_proxy=True,
    ),
}


class ModelConfig(BaseModel):
    name: str = "claude-sonnet-4.6"
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
    zhipu_api_key: str = ""
    deepseek_api_key: str = ""
    openrouter_api_key: str = ""
    serper_api_key: str = ""
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
_active_model: str | None = None


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


def get_active_model() -> str:
    global _active_model
    if _active_model is None:
        _active_model = get_config().model.name
    return _active_model


def set_active_model(model_name: str):
    global _active_model
    _active_model = model_name


def get_model_env(model_name: str) -> tuple[str, str, bool]:
    """Returns (base_url, api_key, needs_proxy) for a given model name."""
    preset = MODEL_PRESETS.get(model_name)
    if not preset:
        preset = MODEL_PRESETS.get(get_config().model.name)
    env = get_env()
    base_url = preset.base_url
    api_key = getattr(env, preset.api_key_env.lower(), "") if preset.api_key_env else ""
    return base_url, api_key, preset.needs_proxy


def init_config(config_path: str | None = None):
    global _config, _env, _active_model
    _env = EnvSettings()
    _config = load_config(config_path)
    _active_model = _config.model.name

    base_url, api_key, _ = get_model_env(_active_model)
    os.environ["ANTHROPIC_BASE_URL"] = base_url
    os.environ["ANTHROPIC_API_KEY"] = api_key
