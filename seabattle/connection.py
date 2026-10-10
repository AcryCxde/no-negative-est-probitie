"""Жизненный цикл соединения: подключение, обработка закрытий, переподключение с задержкой.

Знает только про сокет и политику повторов. Ход партии целиком в `BotRunner`.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from seabattle.config import BotConfig
from seabattle.runner import BotRunner, TransportClosedError

log = logging.getLogger(__name__)

BASE_DELAY: Final = 1.0
MAX_DELAY: Final = 30.0
CONNECT_TIMEOUT: Final = 10.0
PING_INTERVAL: Final = 20.0
PING_TIMEOUT: Final = 20.0
MAX_FRAME_BYTES: Final = 1 << 20

# TOKEN_REISSUED, CONNECTION_REPLACED, BOT_BLOCKED, BOT_DELETED: повтор не поможет или устроит дуэль
FATAL_CLOSE_CODES: Final = frozenset({4001, 4002, 4004, 4005})
FATAL_HTTP_STATUSES: Final = frozenset({401, 403})


class Verdict(Enum):
    RETRY = auto()
    STOP = auto()


class FatalConnectionError(Exception):
    """Подключаться снова бессмысленно: токен недействителен, бот заблокирован или вытеснен."""


def close_verdict(code: int | None) -> Verdict:
    return Verdict.STOP if code in FATAL_CLOSE_CODES else Verdict.RETRY


def retry_delay(attempt: int, rng: random.Random) -> float:
    """Экспоненциальная задержка с полным джиттером: от BASE_DELAY до MAX_DELAY."""
    ceiling = min(MAX_DELAY, BASE_DELAY * 2 ** max(attempt, 0))
    return rng.uniform(BASE_DELAY, max(BASE_DELAY, ceiling))


@dataclass(slots=True)
class WebSocketTransport:
    socket: ClientConnection
    received: int = 0

    async def recv(self) -> str:
        try:
            frame = await self.socket.recv()
        except ConnectionClosed as closed:
            raise TransportClosedError(closed.rcvd.code if closed.rcvd else None) from closed
        self.received += 1
        return frame if isinstance(frame, str) else frame.decode("utf-8", errors="replace")

    async def send(self, text: str) -> None:
        try:
            await self.socket.send(text)
        except ConnectionClosed as closed:
            raise TransportClosedError(closed.rcvd.code if closed.rcvd else None) from closed


async def run_forever(
    config: BotConfig,
    runner: BotRunner,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> None:
    """Держит бота на связи до фатального отказа (`FatalConnectionError`) или мягкой остановки."""
    jitter = rng or random.Random()
    attempt = 0
    while True:
        productive = await _session(config, runner)
        if runner.stopping:
            return
        attempt = 0 if productive else attempt + 1
        delay = retry_delay(attempt, jitter)
        log.info("Переподключение", extra={"delay": round(delay, 1), "attempt": attempt})
        await sleep(delay)


async def _session(config: BotConfig, runner: BotRunner) -> bool:
    """Одно подключение; True, если платформа успела что-то прислать."""
    try:
        async with connect(
            config.url,
            additional_headers={"Authorization": f"Bearer {config.token.value}"},
            open_timeout=CONNECT_TIMEOUT,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_TIMEOUT,
            max_size=MAX_FRAME_BYTES,
            compression=None,
        ) as socket:
            log.info("Соединение открыто")
            transport = WebSocketTransport(socket)
            try:
                await runner.serve(transport)
            except TransportClosedError as closed:
                log.warning("Соединение закрыто", extra={"code": closed.code})
                if close_verdict(closed.code) is Verdict.STOP:
                    raise FatalConnectionError(f"код закрытия {closed.code}") from closed
            return transport.received > 0
    except InvalidStatus as refused:
        status = refused.response.status_code
        if status in FATAL_HTTP_STATUSES:
            raise FatalConnectionError(
                f"платформа отказала в подключении: HTTP {status}"
            ) from refused
        log.warning("Подключение отклонено", extra={"status": status})
    except (InvalidHandshake, OSError, TimeoutError):
        log.warning("Подключиться не удалось", exc_info=True)
    return False
