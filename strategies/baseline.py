"""Базовая стратегия: случайная расстановка, шахматный поиск и добивание с учётом ореолов.

Достаточна, чтобы не проигрывать слабым ботам; ориентир на будущее — Монте-Карло по флотам
(docs/research/research.md).
"""

from collections.abc import Iterable
from random import Random

from seabattle.model import Cell, Fleet, Rules, ShotResult
from seabattle.placement import random_fleet
from seabattle.state import ShotView
from seabattle.strategy import legal_cells

Component = frozenset[Cell]


class BaselineStrategy:
    def place(self, rules: Rules, rng: Random) -> Fleet:
        return random_fleet(rules, rng)

    def choose_shot(self, view: ShotView, rng: Random, deadline: float) -> Cell:  # noqa: ARG002
        ships = _ship_components(view)
        known_empty = _known_empty(view, ships)
        candidates = [cell for cell in legal_cells(view) if cell not in known_empty]
        if not candidates:
            candidates = legal_cells(view)
        if not candidates:
            return Cell(0, 0)
        targets = _target_cells(view, ships, set(candidates))
        if targets:
            return rng.choice(targets)
        return rng.choice(_search_cells(view, ships, candidates))


def _ship_components(view: ShotView) -> list[tuple[Component, bool]]:
    """Группы подбитых клеток; корабли не касаются даже углом, поэтому группа — один корабль."""
    hits = {c for c, result in view.shots.items() if result in (ShotResult.HIT, ShotResult.SUNK)}
    sunk = {c for c, result in view.shots.items() if result is ShotResult.SUNK}
    components: list[tuple[Component, bool]] = []
    seen: set[Cell] = set()
    for start in sorted(hits):
        if start in seen:
            continue
        group = _flood(start, hits)
        seen |= group
        components.append((frozenset(group), not group.isdisjoint(sunk)))
    return components


def _flood(start: Cell, hits: set[Cell]) -> set[Cell]:
    group, frontier = {start}, [start]
    while frontier:
        for near in frontier.pop().neighbors8():
            if near in hits and near not in group:
                group.add(near)
                frontier.append(near)
    return group


def _known_empty(view: ShotView, ships: Iterable[tuple[Component, bool]]) -> set[Cell]:
    empty: set[Cell] = set()
    for group, is_sunk in ships:
        for cell in group:
            if is_sunk:
                empty.update(cell.neighbors8())
            else:
                # Вторая палуба того же корабля лежит на одной прямой, диагонали пусты
                empty.update(
                    near
                    for near in cell.neighbors8()
                    if near.row != cell.row and near.column != cell.column
                )
    return {cell for cell in empty if view.rules.contains(cell)}


def _target_cells(
    view: ShotView, ships: Iterable[tuple[Component, bool]], allowed: set[Cell]
) -> list[Cell]:
    longest = max(view.rules.ship_lengths, default=1)
    targets: list[Cell] = []
    for group, is_sunk in ships:
        if is_sunk or len(group) >= longest:
            continue
        targets.extend(cell for cell in _extensions(group) if cell in allowed)
    return targets


def _extensions(group: Component) -> list[Cell]:
    cells = sorted(group)
    if len(cells) == 1:
        return list(cells[0].neighbors4())
    first, last = cells[0], cells[-1]
    d_row, d_column = _unit(last.row - first.row), _unit(last.column - first.column)
    return [
        Cell(first.row - d_row, first.column - d_column),
        Cell(last.row + d_row, last.column + d_column),
    ]


def _unit(value: int) -> int:
    return (value > 0) - (value < 0)


def _search_cells(
    view: ShotView, ships: Iterable[tuple[Component, bool]], candidates: list[Cell]
) -> list[Cell]:
    remaining = list(view.rules.ship_lengths)
    for group, is_sunk in ships:
        if is_sunk and len(group) in remaining:
            remaining.remove(len(group))
    if max(remaining, default=1) < 2:
        return candidates
    parity = [cell for cell in candidates if (cell.row + cell.column) % 2 == 0]
    return parity or candidates
