"""Контракт между ядром и стратегией. Менять бота = подставить другую реализацию `Strategy`."""

from random import Random
from typing import Protocol

from seabattle.model import Cell, Fleet, Rules
from seabattle.state import ShotView


class Strategy(Protocol):
    def place(self, rules: Rules, rng: Random) -> Fleet:
        """Расстановка флота; ядро проверит её и при ошибке подставит случайную."""
        ...

    def choose_shot(self, view: ShotView, rng: Random, deadline: float) -> Cell:
        """Следующий выстрел. `deadline` — момент `time.monotonic()`, к которому нужен ответ."""
        ...


def legal_cells(view: ShotView) -> list[Cell]:
    """Клетки поля, в которые ещё не стреляли."""
    return [cell for cell in view.rules.cells() if cell not in view.shots]


def fallback_shot(view: ShotView, rng: Random) -> Cell:
    """Допустимый выстрел на случай сбоя или опоздания стратегии."""
    free = legal_cells(view)
    return rng.choice(free) if free else Cell(0, 0)
