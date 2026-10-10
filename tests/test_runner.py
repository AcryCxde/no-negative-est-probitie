import asyncio
import json
import threading
import time
from random import Random

import pytest

from seabattle.model import BotMode, BuiltInBot, Cell, Fleet, Rules, Ship
from seabattle.placement import validate_fleet
from seabattle.protocol import parse_inbound
from seabattle.runner import BotRunner, ModeRequest, TransportClosedError
from seabattle.state import ShotView
from strategies.baseline import BaselineStrategy
from tests.fake_platform import ScriptedTransport, match_started_json, rules_of

TURN = '{"type":"TURN_REQUESTED","moveNumber":1,"shotsLeft":100,"shotsLeftInTurn":1}'
TRAINING = ModeRequest(BotMode.TRAINING, BuiltInBot.EASY)


def play(frames: list[str], strategy: object = None, seed: int = 1) -> list[dict[str, object]]:
    return run(ScriptedTransport(frames), strategy, seed).sent


def run(transport: ScriptedTransport, strategy: object = None, seed: int = 1) -> ScriptedTransport:
    runner = BotRunner(strategy or BaselineStrategy(), TRAINING, Random(seed))  # type: ignore[arg-type]
    with pytest.raises(TransportClosedError):
        asyncio.run(runner.serve(transport))
    return transport


def kinds(sent: list[dict[str, object]]) -> list[object]:
    return [message["type"] for message in sent]


def test_mode_is_announced_first_on_every_connection() -> None:
    sent = play([])
    assert sent == [{"type": "CHANGE_MODE", "mode": "TRAINING", "opponent": "EASY"}]


def test_match_start_is_answered_with_a_valid_placement() -> None:
    sent = play([match_started_json()])
    assert kinds(sent) == ["CHANGE_MODE", "PLACE"]
    ships = sent[1]["ships"]
    assert isinstance(ships, list)
    fleet = tuple(parse_ship(ship) for ship in ships)
    validate_fleet(rules_of(), fleet)


def parse_ship(ship: dict[str, list[dict[str, int]]]) -> Ship:
    return Ship(tuple(Cell(c["row"], c["column"]) for c in ship["cells"]))


def test_rejected_placement_is_replaced_by_a_new_one_but_not_forever() -> None:
    rejection = '{"type":"PLACEMENT_REJECTED","reason":"SHIPS_TOUCH"}'
    sent = play([match_started_json(), *[rejection] * 10])
    assert kinds(sent).count("PLACE") == 5


def test_speedrun_never_sends_a_placement() -> None:
    sent = play([match_started_json(modifier="SPEEDRUN"), TURN])
    assert kinds(sent) == ["CHANGE_MODE", "SHOOT"]


def test_turn_is_answered_with_exactly_one_shot() -> None:
    sent = play([match_started_json(), TURN])
    assert kinds(sent) == ["CHANGE_MODE", "PLACE", "SHOOT"]


def test_malformed_and_unknown_frames_do_not_stop_the_bot() -> None:
    sent = play(
        ["garbage", '{"type":"FUTURE"}', '{"type":"TURN_REQUESTED"}', match_started_json(), TURN]
    )
    assert kinds(sent) == ["CHANGE_MODE", "PLACE", "SHOOT"]


def test_turn_outside_known_match_is_ignored() -> None:
    assert kinds(play([TURN])) == ["CHANGE_MODE"]


def test_snapshot_restores_shots_and_skips_placement() -> None:
    fleet = play([match_started_json()])[1]["ships"]
    shots = [{"cell": {"row": 0, "column": c}, "result": "MISS"} for c in range(9)]
    snapshot = json.dumps(
        {
            "type": "MATCH_SNAPSHOT",
            "rules": json.loads(match_started_json())["rules"],
            "ships": fleet,
            "shots": shots,
            "opponentShots": [],
            "shotsLeft": 91,
        }
    )
    sent = play([snapshot, TURN])
    assert kinds(sent) == ["CHANGE_MODE", "SHOOT"]
    assert sent[1]["cell"] not in [shot["cell"] for shot in shots]


class Exploding:
    def place(self, rules: Rules, rng: Random) -> Fleet:
        raise RuntimeError("boom")

    def choose_shot(self, view: ShotView, rng: Random, deadline: float) -> Cell:
        raise RuntimeError("boom")


class Illegal(Exploding):
    def choose_shot(self, view: ShotView, rng: Random, deadline: float) -> Cell:
        return Cell(-1, 99)


def test_failing_strategy_is_replaced_by_legal_fallbacks() -> None:
    sent = play([match_started_json(), TURN], Exploding())
    assert kinds(sent) == ["CHANGE_MODE", "PLACE", "SHOOT"]


def test_illegal_cell_from_strategy_is_replaced() -> None:
    sent = play([match_started_json(), TURN], Illegal())
    cell = sent[2]["cell"]
    assert isinstance(cell, dict)
    assert 0 <= cell["row"] < 10
    assert 0 <= cell["column"] < 10


def test_slow_strategy_does_not_cost_the_turn() -> None:
    class Slow(Exploding):
        def choose_shot(self, view: ShotView, rng: Random, deadline: float) -> Cell:
            threading.Event().wait(1.5)  # дольше доли хода; поток дожидается asyncio.run при выходе
            return Cell(0, 0)

    transport = ScriptedTransport([match_started_json(), TURN])
    started = time.monotonic()
    run(transport, Slow())
    assert kinds(transport.sent) == ["CHANGE_MODE", "PLACE", "SHOOT"]
    assert transport.sent_at[-1] - started < 1.0


def test_parser_roundtrip_of_scripted_frames() -> None:
    assert parse_inbound(TURN)


def test_stop_is_deferred_until_the_running_match_finishes() -> None:
    finished = '{"type":"MATCH_FINISHED","outcome":"WIN","endReason":"FLEET_DESTROYED"}'
    runner = BotRunner(BaselineStrategy(), TRAINING, Random(1))

    class StopDuringMatch(ScriptedTransport):
        async def send(self, text: str) -> None:
            await super().send(text)
            if '"PLACE"' in text:
                runner.request_stop()  # сигнал приходит, когда партия уже идёт

    transport = StopDuringMatch([match_started_json(), TURN, finished, TURN])
    asyncio.run(runner.serve(transport))  # возвращается сам, исключения нет
    # ход в идущей партии сыгран, после её конца бот вышел и не ждёт новых ходов
    assert kinds(transport.sent) == ["CHANGE_MODE", "PLACE", "SHOOT"]
    assert transport.frames == [TURN]


def test_stop_without_a_match_returns_immediately() -> None:
    transport = ScriptedTransport([TURN])
    runner = BotRunner(BaselineStrategy(), TRAINING, Random(1))
    runner.request_stop()
    asyncio.run(runner.serve(transport))
    assert kinds(transport.sent) == ["CHANGE_MODE"]
    assert transport.frames == [TURN]


def test_runner_stops_by_itself_after_the_requested_number_of_matches() -> None:
    finished = '{"type":"MATCH_FINISHED","outcome":"WIN","endReason":"FLEET_DESTROYED"}'
    frames = [match_started_json(), finished, match_started_json(), finished, match_started_json()]
    transport = ScriptedTransport(frames)
    runner = BotRunner(BaselineStrategy(), TRAINING, Random(1), max_matches=2)
    asyncio.run(runner.serve(transport))
    assert runner.stopping
    assert kinds(transport.sent).count("PLACE") == 2
    assert len(transport.frames) == 1
