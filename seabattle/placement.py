"""Расстановка флота: проверка по правилам платформы и равномерно-случайный генератор."""

from collections.abc import Collection, Iterator
from itertools import pairwise
from random import Random

from seabattle.model import Cell, Fleet, Rules, Ship

MAX_PLACEMENT_ATTEMPTS = 1000


class PlacementError(Exception):
    """Расстановка нарушает правила или не найдена."""


def validate_fleet(rules: Rules, fleet: Fleet) -> None:
    """Те же проверки, что делает платформа: форма, границы, состав, пересечения, касания."""
    if sorted(ship.length for ship in fleet) != sorted(rules.ship_lengths):
        raise PlacementError("состав флота не совпадает с правилами")
    occupied: dict[Cell, int] = {}
    for index, ship in enumerate(fleet):
        _validate_line(rules, ship)
        for cell in ship.cells:
            if cell in occupied:
                raise PlacementError(f"клетка {cell} занята двумя кораблями")
            occupied[cell] = index
    for cell, index in occupied.items():
        for near in cell.neighbors8():
            if occupied.get(near, index) != index:
                raise PlacementError(f"корабли касаются в клетках {cell} и {near}")


def _validate_line(rules: Rules, ship: Ship) -> None:
    if not all(rules.contains(cell) for cell in ship.cells):
        raise PlacementError("корабль выходит за поле")
    rows = {cell.row for cell in ship.cells}
    columns = {cell.column for cell in ship.cells}
    if len(rows) > 1 and len(columns) > 1:
        raise PlacementError("корабль не лежит на одной прямой")
    ordered = sorted(ship.cells)
    steps = {(b.row - a.row) + (b.column - a.column) for a, b in pairwise(ordered)}
    if steps - {1}:
        raise PlacementError("клетки корабля повторяются или идут не подряд")


def _line_placements(rules: Rules, length: int) -> Iterator[Ship]:
    for cell in rules.cells():
        horizontal = tuple(Cell(cell.row, cell.column + i) for i in range(length))
        if rules.contains(horizontal[-1]):
            yield Ship(horizontal)
        if length > 1:
            vertical = tuple(Cell(cell.row + i, cell.column) for i in range(length))
            if rules.contains(vertical[-1]):
                yield Ship(vertical)


def _halo(ship: Ship) -> frozenset[Cell]:
    return frozenset(near for cell in ship.cells for near in cell.neighbors8()) | frozenset(
        ship.cells
    )


def random_fleet(rules: Rules, rng: Random, forbidden: Collection[Cell] = ()) -> Fleet:
    """Случайная допустимая расстановка; корабли ставятся от крупных к мелким."""
    lengths = sorted(rules.ship_lengths, reverse=True)
    candidates = {length: tuple(_line_placements(rules, length)) for length in set(lengths)}
    for _ in range(MAX_PLACEMENT_ATTEMPTS):
        blocked: set[Cell] = set(forbidden)
        placed: list[Ship] = []
        for length in lengths:
            free = [ship for ship in candidates[length] if blocked.isdisjoint(ship.cells)]
            if not free:
                break
            ship = rng.choice(free)
            placed.append(ship)
            blocked |= _halo(ship)
        else:
            return tuple(placed)
    raise PlacementError("не удалось расставить флот за отведённое число попыток")
