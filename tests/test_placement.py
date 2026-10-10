from random import Random

import pytest

from seabattle.model import Cell, Ship
from seabattle.placement import PlacementError, random_fleet, validate_fleet
from tests.fake_platform import rules_of


def ship(*cells: tuple[int, int]) -> Ship:
    return Ship(tuple(Cell(r, c) for r, c in cells))


@pytest.mark.parametrize("overrides", [{}, {"rows": 8, "columns": 8}, {"rows": 12, "columns": 14}])
def test_random_fleets_are_always_valid(overrides: dict[str, int]) -> None:
    rules = rules_of(**overrides)
    for seed in range(300):
        validate_fleet(rules, random_fleet(rules, Random(seed)))


def test_forbidden_cells_are_avoided() -> None:
    rules = rules_of()
    forbidden = {Cell(0, c) for c in range(10)}
    fleet = random_fleet(rules, Random(1), forbidden)
    assert all(cell not in forbidden for s in fleet for cell in s.cells)


def test_impossible_fleet_raises() -> None:
    rules = rules_of(rows=3, columns=3)
    with pytest.raises(PlacementError):
        random_fleet(rules, Random(0))


def valid_base() -> list[Ship]:
    return list(random_fleet(rules_of(), Random(5)))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda fleet: fleet[:-1],
        lambda fleet: [*fleet[:-1], ship((0, 0), (1, 1))],
        lambda fleet: [*fleet[:-1], ship((0, 0), (0, 2))],
        lambda fleet: [*fleet[:-1], ship((0, 9), (0, 10))],
        lambda fleet: [*fleet[:-1], fleet[0]],
    ],
)
def test_invalid_fleets_are_rejected(mutate) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(PlacementError):
        validate_fleet(rules_of(), tuple(mutate(valid_base())))


def test_diagonal_touch_is_rejected() -> None:
    rules = rules_of(fleet=[{"length": 1, "count": 2}])
    with pytest.raises(PlacementError, match="касаются"):
        validate_fleet(rules, (ship((0, 0)), ship((1, 1))))
    validate_fleet(rules, (ship((0, 0)), ship((2, 2))))
