"""Модель встроенного бота HARD: карта плотности, из равных клеток берётся первая по порядку.

Сверено с журналами: все выстрелы HARD во всех партиях — первая клетка из `best_cells`
(`PYTHONPATH=. uv run python tools/analyze_matches.py bot_1/matches`).
"""

from random import Random

from seabattle.model import Cell, Fleet, Rules
from seabattle.placement import random_fleet
from seabattle.state import ShotView
from seabattle.strategy import legal_cells
from strategies.density import best_cells


class HardModel:
    def place(self, rules: Rules, rng: Random) -> Fleet:
        return random_fleet(rules, rng)

    def choose_shot(self, view: ShotView, rng: Random, deadline: float) -> Cell:  # noqa: ARG002
        best = best_cells(view)
        if best:
            return best[0]
        free = legal_cells(view)
        return free[0] if free else Cell(0, 0)
