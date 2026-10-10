import asyncio
from random import Random

import pytest

from seabattle.model import BotMode, BuiltInBot
from seabattle.runner import BotRunner, ModeRequest, TransportClosedError
from seabattle.strategy import Strategy
from strategies.baseline import BaselineStrategy
from strategies.density import DensityStrategy
from tests.fake_platform import FakePlatform, PlatformTransport

TRAINING = ModeRequest(BotMode.TRAINING, BuiltInBot.EASY)


def play_match(
    seed: int,
    *,
    bot_first: bool = True,
    duel: bool = False,
    strategy: Strategy | None = None,
    **rules: object,
) -> FakePlatform:
    platform = FakePlatform(dict(rules), seed, bot_first=bot_first, duel=duel)
    runner = BotRunner(strategy or BaselineStrategy(), TRAINING, Random(seed))
    with pytest.raises(TransportClosedError):
        asyncio.run(asyncio.wait_for(runner.serve(PlatformTransport(platform)), timeout=30))
    return platform


@pytest.mark.parametrize("bot_first", [True, False])
def test_bot_beats_a_random_shooter(bot_first: bool) -> None:
    results = [play_match(seed, bot_first=bot_first) for seed in range(40)]
    wins = sum(1 for platform in results if platform.outcome == "WIN")
    assert wins >= 38
    for platform in results:
        assert len(platform.bot_shots) == len(set(platform.bot_shots)), "повторный выстрел"
        assert len(platform.bot_shots) <= 100
        assert all(platform.rules.contains(cell) for cell in platform.bot_shots)
        assert "PLACE" in platform.sent_kinds


def test_bot_finishes_fleet_in_a_reasonable_number_of_shots() -> None:
    shots = [len(play_match(seed).bot_shots) for seed in range(40)]
    assert sum(shots) / len(shots) < 70


def test_duel_modifier_game_completes() -> None:
    platform = play_match(3, duel=True)
    assert platform.outcome in {"WIN", "LOSS"}
    assert len(platform.bot_shots) == len(set(platform.bot_shots))


def test_small_board_game_completes() -> None:
    platform = play_match(
        4, rows=8, columns=8, fleet=[{"length": 3, "count": 1}, {"length": 1, "count": 3}]
    )
    assert platform.outcome == "WIN"


def test_density_strategy_beats_a_random_shooter_with_fewer_shots() -> None:
    games = [play_match(seed, strategy=DensityStrategy()) for seed in range(40)]
    assert sum(1 for platform in games if platform.outcome == "WIN") >= 38
    assert sum(len(platform.bot_shots) for platform in games) / len(games) < 65
    for platform in games:
        assert len(platform.bot_shots) == len(set(platform.bot_shots))
