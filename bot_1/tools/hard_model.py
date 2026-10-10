"""Модель встроенного бота HARD: карта плотности, из равных клеток берётся первая по порядку.

Сверено с журналами: все выстрелы HARD во всех партиях — первая клетка из `best_cells`
(`PYTHONPATH=. uv run python bot_1/tools/analyze_matches.py bot_1/matches`).
Счёт плотности берётся из strategies/density.py без правок в нём; если его изменят, модель
разойдётся с журналами и тест начала партии это покажет.
"""

from random import Random

from seabattle.model import Cell, Fleet, Rules
from seabattle.placement import random_fleet
from seabattle.state import ShotView
from seabattle.strategy import legal_cells
from strategies.board import remaining_lengths, ship_groups
from strategies.density import _search_scores, _target_scores


def best_cells(view: ShotView) -> list[Cell]:
    """Клетки с наибольшей плотностью по возрастанию (строка, столбец)."""
    groups = ship_groups(view)
    remaining = remaining_lengths(view, groups)
    scores = _target_scores(view, groups, remaining) or _search_scores(view, groups, remaining)
    if not scores:
        return []
    top = max(scores.values())
    return sorted(cell for cell, score in scores.items() if score == top)


class HardModel:
    def place(self, rules: Rules, rng: Random) -> Fleet:
        return random_fleet(rules, rng)

    def choose_shot(self, view: ShotView, rng: Random, deadline: float) -> Cell:  # noqa: ARG002
        best = best_cells(view)
        if best:
            return best[0]
        free = legal_cells(view)
        return free[0] if free else Cell(0, 0)
