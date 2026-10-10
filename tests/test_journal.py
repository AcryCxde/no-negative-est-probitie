import asyncio
import json
from pathlib import Path
from random import Random

import pytest

from seabattle.journal import DirectoryJournal
from seabattle.model import BotMode, BuiltInBot
from seabattle.runner import BotRunner, ModeRequest, TransportClosedError
from strategies.density import DensityStrategy
from tests.fake_platform import FakePlatform, PlatformTransport

TRAINING = ModeRequest(BotMode.TRAINING, BuiltInBot.EASY)


def play_with_journal(directory: Path, seed: int) -> FakePlatform:
    platform = FakePlatform({}, seed)
    runner = BotRunner(
        DensityStrategy(), TRAINING, Random(seed), DirectoryJournal(directory, keep=2)
    )
    with pytest.raises(TransportClosedError):
        asyncio.run(asyncio.wait_for(runner.serve(PlatformTransport(platform)), timeout=30))
    return platform


def test_match_is_recorded_in_both_directions(tmp_path: Path) -> None:
    platform = play_with_journal(tmp_path, 1)
    [path] = tmp_path.glob("*.jsonl")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    kinds = {(line["dir"], line["msg"]["type"]) for line in lines}
    assert ("in", "MATCH_STARTED") in kinds
    assert ("out", "PLACE") in kinds
    assert ("out", "SHOOT") in kinds
    assert ("in", "MATCH_FINISHED") in kinds
    assert sum(
        1 for line in lines if line["dir"] == "out" and line["msg"]["type"] == "SHOOT"
    ) == len(platform.bot_shots)


def test_old_journals_are_pruned(tmp_path: Path) -> None:
    for seed in range(4):
        play_with_journal(tmp_path, seed)
    assert len(list(tmp_path.glob("*.jsonl"))) <= 2


def test_unwritable_directory_does_not_break_the_game(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    platform = play_with_journal(blocker / "matches", 2)
    assert platform.outcome in {"WIN", "LOSS"}
