"""Минимальная платформа для проверки бота целиком: правила, соперник, сообщения в обе стороны."""

import asyncio
import json
import time
from random import Random
from typing import Any

from seabattle.model import Cell, Fleet, Rules, Ship
from seabattle.placement import PlacementError, random_fleet, validate_fleet
from seabattle.protocol import parse_inbound
from seabattle.runner import TransportClosedError

CLOSE_AFTER_MATCH = 1000


def rules_json(**overrides: Any) -> dict[str, Any]:
    rules: dict[str, Any] = {
        "columns": 10,
        "rows": 10,
        "fleet": [
            {"length": 4, "count": 1},
            {"length": 3, "count": 2},
            {"length": 2, "count": 3},
            {"length": 1, "count": 4},
        ],
        "touchRule": "NO_TOUCHING_EVEN_DIAGONALLY",
        "turnAfterHitRule": "SHOOTER_KEEPS_TURN",
        "matchDurationSeconds": 210,
        "shotLimitPerPlayer": 100,
        "moveTimeoutSeconds": 1,
        "placementWindowSeconds": 10,
        "modifier": None,
        "shotsPerTurn": 1,
    }
    return rules | overrides


def match_started_json(**rule_overrides: Any) -> str:
    return json.dumps(
        {
            "type": "MATCH_STARTED",
            "rules": rules_json(**rule_overrides),
            "bonuses": [],
            "opponent": {
                "kind": "BUILT_IN_BOT",
                "botId": None,
                "teamName": None,
                "builtInBot": "EASY",
                "bonuses": [],
            },
        }
    )


def rules_of(**rule_overrides: Any) -> Rules:
    started = parse_inbound(match_started_json(**rule_overrides))
    assert hasattr(started, "rules")
    return started.rules


class FakePlatform:
    """Соперник стреляет случайно; бот ходит первым или вторым по `bot_first`."""

    def __init__(
        self,
        rule_overrides: dict[str, Any],
        seed: int,
        *,
        bot_first: bool = True,
        duel: bool = False,
    ) -> None:
        self.rule_overrides = {**rule_overrides, **({"modifier": "DUEL"} if duel else {})}
        self.rules = rules_of(**self.rule_overrides)
        self.rng = Random(seed)
        self.bot_first = bot_first
        self.duel = duel
        self.opponent_fleet: Fleet = random_fleet(self.rules, self.rng)
        self.bot_fleet: Fleet | None = None
        self.bot_shots: list[Cell] = []
        self.opponent_shots: list[Cell] = []
        self.sent_kinds: list[str] = []
        self.outcome: str | None = None
        self.move_number = 0

    def opening(self) -> list[str]:
        return [match_started_json(**self.rule_overrides)]

    def on_bot_message(self, text: str) -> list[str]:
        message = json.loads(text)
        kind = message["type"]
        self.sent_kinds.append(kind)
        if kind == "CHANGE_MODE":
            return [json.dumps({"type": "MODE_CHANGED", "mode": message["mode"]})]
        if kind == "PLACE":
            return self._place(message)
        if kind == "SHOOT" and self.outcome is None:
            return self._shoot(Cell(message["cell"]["row"], message["cell"]["column"]))
        return []

    def _place(self, message: dict[str, Any]) -> list[str]:
        fleet = tuple(
            Ship(tuple(Cell(c["row"], c["column"]) for c in ship["cells"]))
            for ship in message["ships"]
        )
        try:
            validate_fleet(self.rules, fleet)
        except PlacementError as error:
            return [json.dumps({"type": "PLACEMENT_REJECTED", "reason": str(error)})]
        self.bot_fleet = fleet
        replies = [json.dumps({"type": "PLACEMENT_ACCEPTED"})]
        return replies + (self._turn() if self.bot_first else self._opponent_turn())

    def _turn(self) -> list[str]:
        self.move_number += 1
        turn = {
            "type": "TURN_REQUESTED",
            "moveNumber": self.move_number,
            "shotsLeft": self.rules.shot_limit_per_player - len(self.bot_shots),
            "shotsLeftInTurn": 1,
        }
        return [json.dumps(turn)]

    def _shoot(self, cell: Cell) -> list[str]:
        self.bot_shots.append(cell)
        result = _resolve(self.opponent_fleet, self.bot_shots[:-1], cell)
        replies = [json.dumps({"type": "SHOT_RESOLVED", "cell": _cell(cell), "result": result})]
        if _all_sunk(self.opponent_fleet, self.bot_shots):
            return replies + self._finish("WIN")
        if result == "MISS" or self.duel:
            return replies + self._opponent_turn()
        return replies + self._turn()

    def _opponent_turn(self) -> list[str]:
        assert self.bot_fleet is not None
        replies: list[str] = []
        while True:
            free = [c for c in self.rules.cells() if c not in self.opponent_shots]
            cell = self.rng.choice(free)
            result = _resolve(self.bot_fleet, self.opponent_shots, cell)
            self.opponent_shots.append(cell)
            replies.append(
                json.dumps({"type": "OPPONENT_SHOT", "cell": _cell(cell), "result": result})
            )
            if _all_sunk(self.bot_fleet, self.opponent_shots):
                return replies + self._finish("LOSS")
            if result == "MISS" or self.duel:
                return replies + self._turn()

    def _finish(self, outcome: str) -> list[str]:
        self.outcome = outcome
        finished = {"type": "MATCH_FINISHED", "outcome": outcome, "endReason": "FLEET_DESTROYED"}
        return [json.dumps(finished)]


def _cell(cell: Cell) -> dict[str, int]:
    return {"row": cell.row, "column": cell.column}


def _resolve(fleet: Fleet, earlier: list[Cell], cell: Cell) -> str:
    if cell in earlier:
        return "MISS"
    for ship in fleet:
        if cell in ship.cells:
            return "SUNK" if all(c in {*earlier, cell} for c in ship.cells) else "HIT"
    return "MISS"


def _all_sunk(fleet: Fleet, shots: list[Cell]) -> bool:
    return all(c in set(shots) for ship in fleet for c in ship.cells)


class PlatformTransport:
    """Транспорт бота, подключённый к `FakePlatform`; после конца партии закрывается."""

    def __init__(self, platform: FakePlatform) -> None:
        self.platform = platform
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        for text in platform.opening():
            self.inbox.put_nowait(text)

    async def recv(self) -> str:
        text = await self.inbox.get()
        if text is None:
            raise TransportClosedError(CLOSE_AFTER_MATCH)
        return text

    async def send(self, text: str) -> None:
        for reply in self.platform.on_bot_message(text):
            self.inbox.put_nowait(reply)
        if self.platform.outcome is not None:
            self.inbox.put_nowait(None)


class ScriptedTransport:
    """Отдаёт заранее заданные кадры, затем закрывается; всё отправленное ботом копится в `sent`."""

    def __init__(self, frames: list[str]) -> None:
        self.frames = list(frames)
        self.sent: list[dict[str, Any]] = []
        self.sent_at: list[float] = []

    async def recv(self) -> str:
        if not self.frames:
            raise TransportClosedError(None)
        return self.frames.pop(0)

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))
        self.sent_at.append(time.monotonic())
