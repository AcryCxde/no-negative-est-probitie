"""Граница протокола: JSON платформы ⇄ типизированные сообщения.

Входящие разбираются один раз и дальше живут как frozen-датаклассы. Неизвестные поля и типы
сообщений пропускаются (платформа только расширяет протокол), нарушение формы известного
сообщения — `ProtocolError`.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from seabattle.model import (
    MAX_SHIPS,
    BotMode,
    BuiltInBot,
    Cell,
    Fleet,
    RuleModifier,
    Rules,
    Ship,
    ShotResult,
    TouchRule,
    TurnAfterHitRule,
)

MAX_MESSAGE_BYTES: Final = 8 * 1024


class ProtocolError(Exception):
    """Сообщение платформы не соответствует документированной форме."""


@dataclass(frozen=True, slots=True)
class ShotRecord:
    cell: Cell
    result: ShotResult


@dataclass(frozen=True, slots=True)
class MatchStarted:
    rules: Rules
    opponent: str


@dataclass(frozen=True, slots=True)
class MatchSnapshot:
    rules: Rules
    ships: Fleet | None
    shots: tuple[ShotRecord, ...]
    opponent_shots: tuple[ShotRecord, ...]
    shots_left: int


@dataclass(frozen=True, slots=True)
class PlacementAccepted:
    pass


@dataclass(frozen=True, slots=True)
class PlacementRejected:
    reason: str


@dataclass(frozen=True, slots=True)
class TurnRequested:
    move_number: int
    shots_left: int
    shots_left_in_turn: int


@dataclass(frozen=True, slots=True)
class ShotResolved:
    shot: ShotRecord


@dataclass(frozen=True, slots=True)
class ShotRejected:
    cell: Cell
    reason: str


@dataclass(frozen=True, slots=True)
class OpponentShot:
    shot: ShotRecord


@dataclass(frozen=True, slots=True)
class MatchFinished:
    outcome: str
    end_reason: str


@dataclass(frozen=True, slots=True)
class TurnTimedOut:
    move_number: int


@dataclass(frozen=True, slots=True)
class OpponentTurnTimedOut:
    move_number: int


@dataclass(frozen=True, slots=True)
class ModeChanged:
    mode: str


@dataclass(frozen=True, slots=True)
class ModeRejected:
    mode: str
    reason: str


@dataclass(frozen=True, slots=True)
class CommandRejected:
    command: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class Unhandled:
    """Известное платформе сообщение, которое этому каркасу не нужно, или неизвестное нам."""

    kind: str


Inbound = (
    MatchStarted
    | MatchSnapshot
    | PlacementAccepted
    | PlacementRejected
    | TurnRequested
    | ShotResolved
    | ShotRejected
    | OpponentShot
    | MatchFinished
    | TurnTimedOut
    | OpponentTurnTimedOut
    | ModeChanged
    | ModeRejected
    | CommandRejected
    | Unhandled
)


@dataclass(frozen=True, slots=True)
class ChangeMode:
    mode: BotMode
    opponent: BuiltInBot | None


@dataclass(frozen=True, slots=True)
class Place:
    fleet: Fleet


@dataclass(frozen=True, slots=True)
class Shoot:
    cell: Cell


Outbound = ChangeMode | Place | Shoot


def parse_inbound(raw: str | bytes) -> Inbound:
    """Разбирает один кадр платформы; бросает `ProtocolError`, если форма нарушена."""
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ProtocolError("кадр не является JSON") from error
    body = _object(document, "message")
    kind = _string(body, "type")
    try:
        parser = _PARSERS.get(kind)
        return Unhandled(kind) if parser is None else parser(body)
    except ValueError as error:
        # Rules/Ship проверяют инварианты через ValueError
        raise ProtocolError(f"{kind}: {error}") from error


def encode_outbound(message: Outbound) -> str:
    """Сериализует команду бота; результат не длиннее лимита платформы."""
    match message:
        case ChangeMode(mode, opponent):
            payload: dict[str, Any] = {"type": "CHANGE_MODE", "mode": str(mode)}
            if mode is BotMode.TRAINING and opponent is not None:
                payload["opponent"] = str(opponent)
        case Place(fleet):
            payload = {
                "type": "PLACE",
                "ships": [{"cells": [_cell_json(cell) for cell in ship.cells]} for ship in fleet],
            }
        case Shoot(cell):
            payload = {"type": "SHOOT", "cell": _cell_json(cell)}
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(text.encode()) > MAX_MESSAGE_BYTES:
        raise ProtocolError("сообщение длиннее лимита платформы")
    return text


def _cell_json(cell: Cell) -> dict[str, int]:
    return {"row": cell.row, "column": cell.column}


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{name}: ожидался объект")
    return value


def _array(body: Mapping[str, Any], key: str) -> list[Any]:
    value = body.get(key)
    if not isinstance(value, list):
        raise ProtocolError(f"{key}: ожидался массив")
    return value


def _integer(body: Mapping[str, Any], key: str) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{key}: ожидалось целое число")
    return value


def _string(body: Mapping[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise ProtocolError(f"{key}: ожидалась строка")
    return value


def _optional_string(body: Mapping[str, Any], key: str) -> str | None:
    return None if body.get(key) is None else _string(body, key)


def _cell_from(value: object, name: str) -> Cell:
    cell = _object(value, name)
    return Cell(_integer(cell, "row"), _integer(cell, "column"))


def _cell(body: Mapping[str, Any], key: str = "cell") -> Cell:
    return _cell_from(body.get(key), key)


def _shot(body: Mapping[str, Any]) -> ShotRecord:
    return ShotRecord(_cell(body), ShotResult(_string(body, "result")))


def _fleet(body: Mapping[str, Any], key: str) -> Fleet:
    return tuple(
        Ship(tuple(_cell_from(cell, "cells") for cell in _array(_object(ship, key), "cells")))
        for ship in _array(body, key)
    )


def _rules(body: Mapping[str, Any]) -> Rules:
    rules = _object(body.get("rules"), "rules")
    lengths: list[int] = []
    for entry in _array(rules, "fleet"):
        item = _object(entry, "fleet")
        count = _integer(item, "count")
        if not 0 <= count <= MAX_SHIPS:
            raise ProtocolError("fleet.count вне допустимых границ")
        lengths.extend([_integer(item, "length")] * count)
    modifier = _optional_string(rules, "modifier")
    return Rules(
        rows=_integer(rules, "rows"),
        columns=_integer(rules, "columns"),
        ship_lengths=tuple(sorted(lengths, reverse=True)),
        touch_rule=TouchRule(_string(rules, "touchRule")),
        turn_after_hit_rule=TurnAfterHitRule(_string(rules, "turnAfterHitRule")),
        match_duration_seconds=_integer(rules, "matchDurationSeconds"),
        shot_limit_per_player=_integer(rules, "shotLimitPerPlayer"),
        move_timeout_seconds=_integer(rules, "moveTimeoutSeconds"),
        placement_window_seconds=_integer(rules, "placementWindowSeconds"),
        modifier=None if modifier is None else RuleModifier(modifier),
        shots_per_turn=_integer(rules, "shotsPerTurn"),
    )


def _match_started(body: Mapping[str, Any]) -> MatchStarted:
    opponent = _object(body.get("opponent"), "opponent")
    label = _optional_string(opponent, "teamName") or _optional_string(opponent, "builtInBot")
    return MatchStarted(_rules(body), label or "unknown")


def _match_snapshot(body: Mapping[str, Any]) -> MatchSnapshot:
    ships = None if body.get("ships") is None else _fleet(body, "ships")
    # В «Птичьем коробе» opponentShots нет совсем
    opponent_raw = body.get("opponentShots")
    opponent_shots = (
        ()
        if opponent_raw is None
        else tuple(_shot(_object(s, "opponentShots")) for s in _array(body, "opponentShots"))
    )
    return MatchSnapshot(
        rules=_rules(body),
        ships=ships,
        shots=tuple(_shot(_object(s, "shots")) for s in _array(body, "shots")),
        opponent_shots=opponent_shots,
        shots_left=_integer(body, "shotsLeft"),
    )


_PARSERS: Final[Mapping[str, Callable[[Mapping[str, Any]], Inbound]]] = {
    "MATCH_STARTED": _match_started,
    "MATCH_SNAPSHOT": _match_snapshot,
    "PLACEMENT_ACCEPTED": lambda _body: PlacementAccepted(),
    "PLACEMENT_REJECTED": lambda body: PlacementRejected(_string(body, "reason")),
    "TURN_REQUESTED": lambda body: TurnRequested(
        _integer(body, "moveNumber"), _integer(body, "shotsLeft"), _integer(body, "shotsLeftInTurn")
    ),
    "SHOT_RESOLVED": lambda body: ShotResolved(_shot(body)),
    "SHOT_REJECTED": lambda body: ShotRejected(_cell(body), _string(body, "reason")),
    "OPPONENT_SHOT": lambda body: OpponentShot(_shot(body)),
    "MATCH_FINISHED": lambda body: MatchFinished(
        _string(body, "outcome"), _string(body, "endReason")
    ),
    "TURN_TIMED_OUT": lambda body: TurnTimedOut(_integer(body, "moveNumber")),
    "OPPONENT_TURN_TIMED_OUT": lambda body: OpponentTurnTimedOut(_integer(body, "moveNumber")),
    "MODE_CHANGED": lambda body: ModeChanged(_string(body, "mode")),
    "MODE_REJECTED": lambda body: ModeRejected(_string(body, "mode"), _string(body, "reason")),
    "COMMAND_REJECTED": lambda body: CommandRejected(
        _optional_string(body, "command"), _string(body, "reason")
    ),
}
