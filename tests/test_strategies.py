from random import Random
from types import MappingProxyType

import pytest

from seabattle.model import Cell, ShotResult
from seabattle.state import ShotView
from seabattle.strategy import Strategy
from strategies.baseline import BaselineStrategy
from strategies.density import DensityStrategy
from tests.fake_platform import rules_of

STRATEGIES = [BaselineStrategy, DensityStrategy]
every_strategy = pytest.mark.parametrize("make", STRATEGIES)


def view_of(shots: dict[Cell, ShotResult]) -> ShotView:
    return ShotView(rules_of(), MappingProxyType(shots), MappingProxyType({}), 100, 1)


def picks(make: type[Strategy], shots: dict[Cell, ShotResult], seeds: int = 60) -> set[Cell]:
    return {make().choose_shot(view_of(shots), Random(seed), 0.0) for seed in range(seeds)}


@every_strategy
def test_after_a_hit_it_shoots_orthogonal_neighbours(make: type[Strategy]) -> None:
    assert picks(make, {Cell(5, 5): ShotResult.HIT}) == set(Cell(5, 5).neighbors4())


@every_strategy
def test_two_hits_in_a_line_are_extended_along_the_line(make: type[Strategy]) -> None:
    shots = {Cell(5, 5): ShotResult.HIT, Cell(5, 6): ShotResult.HIT}
    assert picks(make, shots) == {Cell(5, 4), Cell(5, 7)}


@every_strategy
def test_sunk_ship_halo_is_never_shot(make: type[Strategy]) -> None:
    sunk = {Cell(0, 0): ShotResult.HIT, Cell(0, 1): ShotResult.SUNK}
    halo = {Cell(1, 0), Cell(1, 1), Cell(1, 2), Cell(0, 2)}
    assert picks(make, sunk, 300).isdisjoint(halo | set(sunk))


def test_density_does_not_pursue_or_shoot_around_a_group_as_long_as_the_longest_ship() -> None:
    # SCHRODINGER_SHIP: потопление не объявлено, но четвёрка в линии — уже целый корабль
    line = {Cell(5, c): ShotResult.HIT for c in range(2, 6)}
    halo = {near for cell in line for near in cell.neighbors8()} - set(line)
    assert picks(DensityStrategy, line, 200).isdisjoint(halo | set(line))


def test_search_prefers_checkerboard_while_big_ships_remain() -> None:
    assert all((cell.row + cell.column) % 2 == 0 for cell in picks(BaselineStrategy, {}, 300))


def test_density_gives_up_on_a_wounded_group_that_cannot_grow() -> None:
    # Все клетки вокруг одиночного попадания — промахи: этот корабль закончен, ищем другие
    wall = dict.fromkeys(Cell(5, 5).neighbors8(), ShotResult.MISS)
    shots = {Cell(5, 5): ShotResult.HIT, **wall}
    assert picks(DensityStrategy, shots, 100).isdisjoint({Cell(5, 5), *wall})


@every_strategy
def test_never_repeats_a_shot_when_cells_remain(make: type[Strategy]) -> None:
    rules = rules_of()
    shots = {cell: ShotResult.MISS for cell in rules.cells() if cell != Cell(9, 9)}
    assert picks(make, shots, 5) == {Cell(9, 9)}


@every_strategy
def test_full_board_still_returns_a_cell(make: type[Strategy]) -> None:
    shots = dict.fromkeys(rules_of().cells(), ShotResult.MISS)
    assert picks(make, shots, 3)
