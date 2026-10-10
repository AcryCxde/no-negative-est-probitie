import asyncio
import random

import pytest

from seabattle import connection
from seabattle.config import BotConfig, Secret
from seabattle.connection import (
    BASE_DELAY,
    MAX_DELAY,
    FatalConnectionError,
    Verdict,
    close_verdict,
    retry_delay,
    run_forever,
)
from seabattle.model import BotMode, BuiltInBot
from seabattle.runner import BotRunner, ModeRequest
from strategies.baseline import BaselineStrategy


@pytest.mark.parametrize("code", [4001, 4002, 4004, 4005])
def test_fatal_close_codes_stop_the_bot(code: int) -> None:
    assert close_verdict(code) is Verdict.STOP


@pytest.mark.parametrize("code", [None, 1000, 1006, 1011, 4003, 4006, 4007])
def test_other_close_codes_trigger_reconnect(code: int | None) -> None:
    assert close_verdict(code) is Verdict.RETRY


def test_retry_delay_is_bounded_and_grows() -> None:
    rng = random.Random(0)
    delays = [retry_delay(attempt, rng) for attempt in range(40) for _ in range(20)]
    assert all(BASE_DELAY <= delay <= MAX_DELAY for delay in delays)
    assert max(retry_delay(0, rng) for _ in range(50)) <= BASE_DELAY
    assert max(retry_delay(6, rng) for _ in range(200)) > 10


CONFIG = BotConfig(Secret("t"), "wss://example.test/ws", BotMode.TRAINING, BuiltInBot.EASY, "INFO")


def test_run_forever_reconnects_with_delays_until_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: list[bool | Exception] = [False, False, True, FatalConnectionError("blocked")]

    async def fake_session(config: BotConfig, runner: BotRunner) -> bool:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(connection, "_session", fake_session)
    runner = BotRunner(BaselineStrategy(), ModeRequest(BotMode.TRAINING, BuiltInBot.EASY))
    with pytest.raises(FatalConnectionError):
        asyncio.run(run_forever(CONFIG, runner, fake_sleep, random.Random(1)))
    assert len(delays) == 3
    assert delays[2] == BASE_DELAY  # после «продуктивного» подключения счётчик попыток сброшен
