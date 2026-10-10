"""Доменные типы партии. Значения здесь уже прошли разбор на границе протокола."""

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Self


class TolerantEnum(StrEnum):
    """Платформа только расширяет перечисления: неизвестное значение превращается в UNKNOWN."""

    @classmethod
    def _missing_(cls, value: object) -> Self:  # noqa: ARG003
        return cls("UNKNOWN")


class ShotResult(TolerantEnum):
    MISS = "MISS"
    HIT = "HIT"
    SUNK = "SUNK"
    DEFENDED = "DEFENDED"
    MINE = "MINE"
    UNKNOWN = "UNKNOWN"


class RuleModifier(TolerantEnum):
    DUEL = "DUEL"
    THREE_MUSKETEERS = "THREE_MUSKETEERS"
    SCHRODINGER_SHIP = "SCHRODINGER_SHIP"
    HUNGER_GAMES = "HUNGER_GAMES"
    SPEEDRUN = "SPEEDRUN"
    BIRD_BOX = "BIRD_BOX"
    OLD_SCHOOL = "OLD_SCHOOL"
    CASINO_ROYALE = "CASINO_ROYALE"
    UNKNOWN = "UNKNOWN"


class TouchRule(TolerantEnum):
    NO_TOUCHING_EVEN_DIAGONALLY = "NO_TOUCHING_EVEN_DIAGONALLY"
    UNKNOWN = "UNKNOWN"


class TurnAfterHitRule(TolerantEnum):
    SHOOTER_KEEPS_TURN = "SHOOTER_KEEPS_TURN"
    HIT_SPENDS_SHOT = "HIT_SPENDS_SHOT"
    UNKNOWN = "UNKNOWN"


class BotMode(StrEnum):
    RANKING = "RANKING"
    TRAINING = "TRAINING"


class BuiltInBot(StrEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


@dataclass(frozen=True, slots=True, order=True)
class Cell:
    row: int
    column: int

    def neighbors8(self) -> Iterator["Cell"]:
        for d_row in (-1, 0, 1):
            for d_column in (-1, 0, 1):
                if d_row or d_column:
                    yield Cell(self.row + d_row, self.column + d_column)

    def neighbors4(self) -> Iterator["Cell"]:
        yield Cell(self.row - 1, self.column)
        yield Cell(self.row + 1, self.column)
        yield Cell(self.row, self.column - 1)
        yield Cell(self.row, self.column + 1)


@dataclass(frozen=True, slots=True)
class Ship:
    cells: tuple[Cell, ...]

    def __post_init__(self) -> None:
        if not self.cells:
            raise ValueError("корабль без клеток")

    @property
    def length(self) -> int:
        return len(self.cells)


Fleet = tuple[Ship, ...]

MAX_SIDE = 100
MAX_SHIPS = 100


@dataclass(frozen=True, slots=True)
class Rules:
    rows: int
    columns: int
    ship_lengths: tuple[int, ...]
    touch_rule: TouchRule
    turn_after_hit_rule: TurnAfterHitRule
    match_duration_seconds: int
    shot_limit_per_player: int
    move_timeout_seconds: int
    placement_window_seconds: int
    modifier: RuleModifier | None
    shots_per_turn: int

    def __post_init__(self) -> None:
        if not (0 < self.rows <= MAX_SIDE and 0 < self.columns <= MAX_SIDE):
            raise ValueError("размер поля вне допустимых границ")
        if len(self.ship_lengths) > MAX_SHIPS:
            raise ValueError("слишком много кораблей")
        if self.move_timeout_seconds <= 0:
            raise ValueError("время на ход должно быть положительным")
        if any(not 0 < length <= MAX_SIDE for length in self.ship_lengths):
            raise ValueError("длина корабля вне допустимых границ")

    def contains(self, cell: Cell) -> bool:
        return 0 <= cell.row < self.rows and 0 <= cell.column < self.columns

    def cells(self) -> Iterator[Cell]:
        for row in range(self.rows):
            for column in range(self.columns):
                yield Cell(row, column)
