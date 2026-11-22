"""配置合并。

优先级：内置默认 < 仓库根的 `.chronicle.toml` < 环境变量 < 命令行参数。
最后一条最重要 —— 显式传进来的参数永远该赢。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Settings", "LlmSettings", "load_settings", "CONFIG_FILENAME"]

CONFIG_FILENAME = ".chronicle.toml"

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
# 按顺序找，先找到的先用
_API_KEY_ENVS = ("CHRONICLE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")


@dataclass(slots=True)
class LlmSettings:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    timeout: float = 90.0
    temperature: float = 0.7
    max_tokens: int = 2000
    enabled: bool = True
    retries: int = 1

    def ready(self) -> bool:
        return self.enabled and bool(self.api_key)


@dataclass(slots=True)
class Settings:
    lang: str = "zh"
    tone: str = "tech"
    ignore: list[str] = field(default_factory=list)
    author_aliases: dict[str, list[str]] = field(default_factory=dict)
    llm: LlmSettings = field(default_factory=LlmSettings)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover - 3.10 只损失配置文件能力
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, ValueError):
        return {}


def load_settings(
    repo_root: Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    settings = Settings()
    overrides = overrides or {}

    if repo_root:
        data = _read_toml(repo_root / CONFIG_FILENAME)
        report_cfg = data.get("report") or {}
        llm_cfg = data.get("llm") or {}

        if isinstance(report_cfg.get("lang"), str):
            settings.lang = report_cfg["lang"]
        if isinstance(report_cfg.get("tone"), str):
            settings.tone = report_cfg["tone"]
        ignore = report_cfg.get("ignore")
        if isinstance(ignore, list):
            settings.ignore = [str(item) for item in ignore]

        if isinstance(llm_cfg.get("base_url"), str):
            settings.llm.base_url = llm_cfg["base_url"]
        if isinstance(llm_cfg.get("model"), str):
            settings.llm.model = llm_cfg["model"]
        if isinstance(llm_cfg.get("timeout"), (int, float)):
            settings.llm.timeout = float(llm_cfg["timeout"])
        if isinstance(llm_cfg.get("temperature"), (int, float)):
            settings.llm.temperature = float(llm_cfg["temperature"])
        if isinstance(llm_cfg.get("enabled"), bool):
            settings.llm.enabled = llm_cfg["enabled"]

        author_cfg = data.get("author") or {}
        alias_cfg = author_cfg.get("aliases") or {}
        if isinstance(alias_cfg, dict):
            settings.author_aliases = {
                str(name): [str(email) for email in emails]
                for name, emails in alias_cfg.items()
                if isinstance(emails, list)
            }

        key_env = llm_cfg.get("api_key_env")
        if isinstance(key_env, str) and os.environ.get(key_env):
            settings.llm.api_key = os.environ[key_env]

    if not settings.llm.api_key:
        for name in _API_KEY_ENVS:
            value = os.environ.get(name)
            if value:
                settings.llm.api_key = value
                break

    # 命令行覆盖
    for name in ("lang", "tone"):
        if overrides.get(name):
            setattr(settings, name, overrides[name])
    if overrides.get("ignore"):
        settings.ignore = list(overrides["ignore"])

    llm_overrides = {
        "base_url": overrides.get("llm_base_url"),
        "model": overrides.get("model"),
        "api_key": overrides.get("api_key"),
        "timeout": overrides.get("llm_timeout"),
        "temperature": overrides.get("temperature"),
    }
    for key, value in llm_overrides.items():
        if value not in (None, ""):
            setattr(settings.llm, key, value)
    if overrides.get("no_ai"):
        settings.llm.enabled = False

    return settings
