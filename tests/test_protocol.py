import json

import pytest

from seabattle.model import BotMode, BuiltInBot, Cell, RuleModifier, Ship, ShotResult
from seabattle.protocol import (
    ChangeMode,
    MatchSnapshot,
    MatchStarted,
    Place,
    ProtocolError,
    Shoot,
    ShotRecord,
    TurnRequested,
    Unhandled,
    encode_outbound,
    parse_inbound,
)
from tests.fake_platform import match_started_json, rules_json


def test_match_started_is_parsed_into_rules() -> None:
    message = parse_inbound(match_started_json())
    assert isinstance(message, MatchStarted)
    assert message.rules.ship_lengths == (4, 3, 3, 2, 2, 2, 1, 1, 1, 1)
    assert (message.rules.rows, message.rules.columns) == (10, 10)
    assert message.rules.modifier is None


def test_snapshot_without_opponent_shots_is_accepted() -> None:
    raw = json.dumps(
        {
            "type": "MATCH_SNAPSHOT",
            "rules": rules_json(modifier="BIRD_BOX"),
            "ships": None,
            "shots": [{"cell": {"row": 4, "column": 4}, "result": "MISS"}],
            "shotsLeft": 99,
        }
    )
    message = parse_inbound(raw)
    assert isinstance(message, MatchSnapshot)
    assert message.rules.modifier is RuleModifier.BIRD_BOX
    assert message.shots == (ShotRecord(Cell(4, 4), ShotResult.MISS),)
    assert message.opponent_shots == ()


def test_unknown_fields_values_and_types_are_tolerated() -> None:
    turn = parse_inbound(
        '{"type":"TURN_REQUESTED","moveNumber":7,"shotsLeft":97,"shotsLeftInTurn":1,"x":1}'
    )
    assert turn == TurnRequested(7, 97, 1)
    assert parse_inbound('{"type":"SOMETHING_NEW","a":1}') == Unhandled("SOMETHING_NEW")
    shot = parse_inbound('{"type":"OPPONENT_SHOT","cell":{"row":1,"column":2},"result":"LASER"}')
    assert shot.shot.result is ShotResult.UNKNOWN  # type: ignore[union-attr]
    modifier = parse_inbound(match_started_json(modifier="TIME_WARP"))
    assert modifier.rules.modifier is RuleModifier.UNKNOWN  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"no_type": true}',
        '{"type":"TURN_REQUESTED","moveNumber":"7","shotsLeft":1,"shotsLeftInTurn":1}',
        '{"type":"SHOT_RESOLVED","cell":{"row":true,"column":1},"result":"HIT"}',
        '{"type":"SHOT_RESOLVED","result":"HIT"}',
    ],
)
def test_malformed_frames_raise_protocol_error(raw: str) -> None:
    with pytest.raises(ProtocolError):
        parse_inbound(raw)


@pytest.mark.parametrize(
    "overrides",
    [
        {"columns": 10**9},
        {"rows": 0},
        {"moveTimeoutSeconds": 0},
        {"fleet": [{"length": 1, "count": 10**9}]},
        {"fleet": [{"length": 10**6, "count": 1}]},
    ],
)
def test_hostile_rules_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ProtocolError):
        parse_inbound(match_started_json(**overrides))


def test_outbound_shapes_match_the_documentation() -> None:
    assert json.loads(encode_outbound(Shoot(Cell(2, 1)))) == {
        "type": "SHOOT",
        "cell": {"row": 2, "column": 1},
    }
    place = encode_outbound(Place((Ship((Cell(0, 0), Cell(0, 1))),)))
    assert json.loads(place) == {
        "type": "PLACE",
        "ships": [{"cells": [{"row": 0, "column": 0}, {"row": 0, "column": 1}]}],
    }


def test_opponent_is_sent_only_in_training() -> None:
    training = json.loads(encode_outbound(ChangeMode(BotMode.TRAINING, BuiltInBot.HARD)))
    ranking = json.loads(encode_outbound(ChangeMode(BotMode.RANKING, BuiltInBot.HARD)))
    assert training == {"type": "CHANGE_MODE", "mode": "TRAINING", "opponent": "HARD"}
    assert ranking == {"type": "CHANGE_MODE", "mode": "RANKING"}
