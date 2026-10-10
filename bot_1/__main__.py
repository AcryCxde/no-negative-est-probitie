"""Запуск: `uv run python -m bot_1` из корня репозитория. Токен — в `bot_1/.env`."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

from seabattle.config import ConfigError, load_config
from seabattle.connection import FatalConnectionError, run_forever
from seabattle.journal import DirectoryJournal
from seabattle.logs import setup_logging
from seabattle.runner import BotRunner, ModeRequest

from bot_1.strategy import Bot1Strategy

log = logging.getLogger("bot_1")

DRAIN_SECONDS = 240  # партия длится не дольше 210 с


async def main() -> int:
    try:
        config = load_config(Path(__file__).with_name(".env"))
    except ConfigError as error:
        print(f"Ошибка настроек: {error}", file=sys.stderr)  # noqa: T201
        return 2
    setup_logging(config.log_level)
    journal = DirectoryJournal(Path(__file__).with_name("matches"))
    runner = BotRunner(
        Bot1Strategy(),
        ModeRequest(config.mode, config.opponent),
        journal=journal,
        max_matches=config.max_matches,
    )
    task = asyncio.create_task(run_forever(config, runner))
    loop = asyncio.get_running_loop()

    def on_signal() -> None:
        # Первый сигнал — доиграть идущую партию, повторный или по истечении срока — выйти сразу
        if runner.stopping or not runner.match_active:
            task.cancel()
            return
        runner.request_stop()
        log.info("Остановка после партии", extra={"drain_seconds": DRAIN_SECONDS})
        loop.call_later(DRAIN_SECONDS, task.cancel)

    for stop in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(stop, on_signal)
    log.info("Бот запущен", extra={"mode": config.mode, "opponent": config.opponent})
    try:
        await task
    except FatalConnectionError as error:
        log.error("Подключение невозможно, остановка", extra={"reason": str(error)})
        return 1
    except asyncio.CancelledError:
        log.info("Остановка по сигналу")
        return 0
    log.info("Остановка после партии")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
