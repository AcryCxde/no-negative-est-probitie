"""Настройки процесса из окружения и `.env`. Токен — секрет: нигде не печатается."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from seabattle.model import BotMode, BuiltInBot

DEFAULT_URL = "wss://hackathon.no-offence.ru/api/v1/bots/connection"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigError(Exception):
    """Настройки отсутствуют или некорректны."""


@dataclass(frozen=True, slots=True)
class Secret:
    value: str

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class BotConfig:
    token: Secret
    url: str
    mode: BotMode
    opponent: BuiltInBot | None
    log_level: str
    max_matches: int | None = None


def read_env_file(path: Path) -> dict[str, str]:
    """Читает `KEY=VALUE`; пустые строки и комментарии `#` пропускает."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator:
            raise ConfigError(f"{path.name}: строка без '='")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_config(env_file: Path | None, environ: Mapping[str, str] | None = None) -> BotConfig:
    """Переменные процесса главнее файла."""
    merged: dict[str, str] = read_env_file(env_file) if env_file and env_file.is_file() else {}
    merged.update(os.environ if environ is None else environ)
    token = merged.get("TOKEN", "")
    if not token:
        raise ConfigError("TOKEN не задан: укажите токен бота в .env или в окружении")
    url = merged.get("BOT_URL", DEFAULT_URL)
    _require_secure_url(url)
    try:
        mode = BotMode(merged.get("BOT_MODE", BotMode.TRAINING))
        opponent = BuiltInBot(merged.get("BOT_OPPONENT", BuiltInBot.EASY))
    except ValueError as error:
        raise ConfigError(f"недопустимое значение настройки: {error}") from error
    return BotConfig(
        Secret(token),
        url,
        mode,
        opponent,
        merged.get("LOG_LEVEL", "INFO").upper(),
        _parse_max_matches(merged.get("BOT_MATCHES", "")),
    )


def _parse_max_matches(raw: str) -> int | None:
    """Пусто — играть до остановки вручную; число — остановиться после стольких партий."""
    if not raw:
        return None
    if not raw.isdecimal() or int(raw) == 0:
        raise ConfigError(f"BOT_MATCHES должен быть положительным целым, получено {raw!r}")
    return int(raw)


def _require_secure_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme == "wss" and parts.hostname:
        return
    if parts.scheme == "ws" and parts.hostname in LOOPBACK_HOSTS:
        return
    raise ConfigError("BOT_URL должен быть wss://, а ws:// допустим только для localhost")
