"""Стрельба по карте плотности, расстановка в половине поля.

Стреляем в клетку, где проходит больше всего допустимых положений кораблей.

Поиск: положения оставшихся кораблей, не задевающие промахи, убитые корабли и их ореол.
Добивание: только положения, целиком накрывающие подбитую группу. Достраивает DataGenetics-подход
под запрет касания; ориентир на будущее — Монте-Карло по целым флотам (docs/research/research.md).
"""

from collections import Counter
from functools import lru_cache
from random import Random

from seabattle.model import Cell, Fleet, Rules
from seabattle.state import ShotView
from seabattle.strategy import legal_cells
from strategies.board import Group, known_empty, remaining_lengths, ship_groups
from strategies.compact import compact_fleet

Placement = tuple[Cell, ...]


@lru_cache(maxsize=64)
def _placements(rows: int, columns: int, length: int) -> tuple[Placement, ...]:
    found: list[Placement] = []
    for row in range(rows):
        for column in range(columns):
            if column + length <= columns:
                found.append(tuple(Cell(row, column + i) for i in range(length)))
            if length > 1 and row + length <= rows:
                found.append(tuple(Cell(row + i, column) for i in range(length)))
    return tuple(found)


class DensityStrategy:
    def place(self, rules: Rules, rng: Random) -> Fleet:
        return compact_fleet(rules, rng)

    def choose_shot(self, view: ShotView, rng: Random, deadline: float) -> Cell:  # noqa: ARG002
        groups = ship_groups(view)
        remaining = remaining_lengths(view, groups)
        scores = _target_scores(view, groups, remaining) or _search_scores(view, groups, remaining)
        if not scores:
            free = legal_cells(view)
            return rng.choice(free) if free else Cell(0, 0)
        best = max(scores.values())
        return rng.choice(sorted(cell for cell, score in scores.items() if score == best))


def _halo(cells: frozenset[Cell]) -> set[Cell]:
    return {near for cell in cells for near in cell.neighbors8()}


def _search_scores(view: ShotView, groups: list[Group], remaining: list[int]) -> Counter[Cell]:
    """Все подбитые группы считаются завершёнными: их клетки и ореол недоступны."""
    blocked = set(view.shots) | known_empty(view, groups)
    for group in groups:
        blocked |= group.cells | _halo(group.cells)
    weights = Counter(remaining)
    scores: Counter[Cell] = Counter()
    for length, weight in weights.items():
        for placement in _placements(view.rules.rows, view.rules.columns, length):
            if blocked.isdisjoint(placement):
                for cell in placement:
                    scores[cell] += weight
    return scores


def _target_scores(view: ShotView, groups: list[Group], remaining: list[int]) -> Counter[Cell]:
    """Положения, целиком накрывающие недобитую группу; пустой счёт — добивать нечего."""
    open_groups = [g for g in groups if not g.sunk]
    if not open_groups:
        return Counter()
    closed = [g for g in groups if g.sunk]
    blocked = {c for c, r in view.shots.items() if c not in {x for g in groups for x in g.cells}}
    blocked |= known_empty(view, groups)
    for group in closed:
        blocked |= group.cells | _halo(group.cells)
    scores: Counter[Cell] = Counter()
    for length in set(remaining):
        for placement in _placements(view.rules.rows, view.rules.columns, length):
            cells = set(placement)
            if not blocked.isdisjoint(cells) or not _consistent(cells, open_groups):
                continue
            for cell in cells:
                if cell not in view.shots:
                    scores[cell] += 1
    return scores


def _consistent(cells: set[Cell], open_groups: list[Group]) -> bool:
    """Положение накрывает хоть одну группу целиком, а остальные не задевает и не касается."""
    covers = False
    for group in open_groups:
        if group.cells <= cells:
            covers = True
        elif not cells.isdisjoint(group.cells | _halo(group.cells)):
            return False
    return covers
