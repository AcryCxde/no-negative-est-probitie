from random import Random
from types import MappingProxyType

from seabattle.model import Cell, ShotResult
from seabattle.state import ShotView
from strategies.baseline import BaselineStrategy
from tests.fake_platform import rules_of


def view_of(shots: dict[Cell, ShotResult]) -> ShotView:
    return ShotView(rules_of(), MappingProxyType(shots), MappingProxyType({}), 100, 1)


def pick(shots: dict[Cell, ShotResult], seed: int) -> Cell:
    return BaselineStrategy().choose_shot(view_of(shots), Random(seed), 0.0)


def test_after_a_hit_it_shoots_orthogonal_neighbours() -> None:
    shots = {Cell(5, 5): ShotResult.HIT}
    assert {pick(shots, seed) for seed in range(50)} == set(Cell(5, 5).neighbors4())


def test_two_hits_in_a_line_are_extended_along_the_line() -> None:
    shots = {Cell(5, 5): ShotResult.HIT, Cell(5, 6): ShotResult.HIT}
    assert {pick(shots, seed) for seed in range(50)} == {Cell(5, 4), Cell(5, 7)}


def test_sunk_ship_halo_is_never_shot() -> None:
    sunk = {Cell(0, 0): ShotResult.HIT, Cell(0, 1): ShotResult.SUNK}
    halo = {Cell(1, 0), Cell(1, 1), Cell(1, 2), Cell(0, 2)}
    picked = {pick(sunk, seed) for seed in range(300)}
    assert picked.isdisjoint(halo | set(sunk))


def test_search_uses_checkerboard_while_big_ships_remain() -> None:
    picked = {pick({}, seed) for seed in range(300)}
    assert all((cell.row + cell.column) % 2 == 0 for cell in picked)


def test_never_repeats_a_shot_when_cells_remain() -> None:
    rules = rules_of()
    shots = {cell: ShotResult.MISS for cell in rules.cells() if cell != Cell(9, 9)}
    assert pick(shots, 0) == Cell(9, 9)
