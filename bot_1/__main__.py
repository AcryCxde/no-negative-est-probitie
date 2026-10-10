"""Запуск: `uv run python -m bot_1` из корня репозитория. Токен — в `bot_1/.env`."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

from seabattle.config import ConfigError, load_config
from seabattle.connection import FatalConnectionError, run_forever
from seabattle.logs import setup_logging
from seabattle.runner import BotRunner, ModeRequest
from strategies.baseline import BaselineStrategy

log = logging.getLogger("bot_1")


async def main() -> int:
    try:
        config = load_config(Path(__file__).with_name(".env"))
    except ConfigError as error:
        print(f"Ошибка настроек: {error}", file=sys.stderr)  # noqa: T201
        return 2
    setup_logging(config.log_level)
    runner = BotRunner(BaselineStrategy(), ModeRequest(config.mode, config.opponent))
    task = asyncio.create_task(run_forever(config, runner))
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(stop, task.cancel)
    log.info("Бот запущен", extra={"mode": config.mode, "opponent": config.opponent})
    try:
        await task
    except FatalConnectionError as error:
        log.error("Подключение невозможно, остановка", extra={"reason": str(error)})
        return 1
    except asyncio.CancelledError:
        log.info("Остановка по сигналу")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
