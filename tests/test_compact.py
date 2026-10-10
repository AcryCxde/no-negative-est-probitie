from random import Random

from seabattle.model import Cell
from seabattle.placement import validate_fleet
from strategies.compact import compact_fleet
from tests.fake_platform import rules_of


def occupied(fleet: tuple) -> set[Cell]:  # type: ignore[type-arg]
    return {cell for ship in fleet for cell in ship.cells}


def test_fleet_is_valid_and_sits_in_one_half() -> None:
    rules = rules_of()
    for seed in range(200):
        fleet = compact_fleet(rules, Random(seed))
        validate_fleet(rules, fleet)
        cells = occupied(fleet)
        rows = {c.row for c in cells}
        columns = {c.column for c in cells}
        assert max(rows) < 5 or min(rows) >= 5 or max(columns) < 5 or min(columns) >= 5


def test_all_four_sides_are_used() -> None:
    rules = rules_of()
    halves = set()
    for seed in range(200):
        cells = occupied(compact_fleet(rules, Random(seed)))
        rows = {c.row for c in cells}
        columns = {c.column for c in cells}
        halves.add(
            "top"
            if max(rows) < 5
            else "bottom"
            if min(rows) >= 5
            else "left"
            if max(columns) < 5
            else "right"
        )
    assert halves == {"top", "bottom", "left", "right"}


def test_falls_back_to_the_whole_board_when_half_is_too_small() -> None:
    rules = rules_of(
        rows=6, columns=6, fleet=[{"length": 3, "count": 2}, {"length": 1, "count": 3}]
    )
    for seed in range(20):
        validate_fleet(rules, compact_fleet(rules, Random(seed)))
