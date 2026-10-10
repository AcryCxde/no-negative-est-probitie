"""Что известно о поле соперника: группы подбитых клеток, заведомо пустые клетки, остаток флота."""

from collections.abc import Iterable
from dataclasses import dataclass

from seabattle.model import Cell, ShotResult
from seabattle.state import ShotView


@dataclass(frozen=True, slots=True)
class Group:
    """Подбитые клетки одного корабля: корабли не касаются даже углом, поэтому группа связна."""

    cells: frozenset[Cell]
    sunk: bool


def ship_groups(view: ShotView) -> list[Group]:
    hits = {c for c, r in view.shots.items() if r in (ShotResult.HIT, ShotResult.SUNK)}
    sunk = {c for c, r in view.shots.items() if r is ShotResult.SUNK}
    groups: list[Group] = []
    seen: set[Cell] = set()
    for start in sorted(hits):
        if start in seen:
            continue
        cells = _flood(start, hits)
        seen |= cells
        groups.append(Group(frozenset(cells), not cells.isdisjoint(sunk)))
    return groups


def _flood(start: Cell, hits: set[Cell]) -> set[Cell]:
    group, frontier = {start}, [start]
    while frontier:
        for near in frontier.pop().neighbors8():
            if near in hits and near not in group:
                group.add(near)
                frontier.append(near)
    return group


def known_empty(view: ShotView, groups: Iterable[Group]) -> set[Cell]:
    """Клетки, где корабля быть не может: ореол убитых и диагонали от подбитых клеток."""
    empty: set[Cell] = set()
    for group in groups:
        for cell in group.cells:
            if group.sunk:
                empty.update(cell.neighbors8())
            else:
                empty.update(
                    n for n in cell.neighbors8() if n.row != cell.row and n.column != cell.column
                )
    return {cell for cell in empty if view.rules.contains(cell)}


def remaining_lengths(view: ShotView, groups: Iterable[Group]) -> list[int]:
    """Длины кораблей, которые ещё не потоплены (в «Шрёдингере» потопления не видно — весь флот)."""
    remaining = list(view.rules.ship_lengths)
    for group in groups:
        if group.sunk and len(group.cells) in remaining:
            remaining.remove(len(group.cells))
    return remaining
