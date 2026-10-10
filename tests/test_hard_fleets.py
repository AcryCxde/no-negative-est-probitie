from random import Random

import pytest

from seabattle.model import Cell, Fleet, ShotResult
from seabattle.placement import validate_fleet
from strategies.density import DensityStrategy
from strategies.hard_fleets import FLEETS, decode
from tests.fake_platform import rules_of
from tools.bench_strategy import play_out
from tools.hard_model import HardModel

# Первая партия из журналов bot_1: расстановка и начало стрельбы настоящего HARD
RECORDED = decode("0,8,4v 9,5,3h 1,1,3v 5,3,2v 4,5,2h 9,1,2h 3,3,1h 8,9,1h 6,0,1h 1,4,1h")
RECORDED_OPENING = [
    (Cell(3, 3), ShotResult.SUNK),
    (Cell(5, 5), ShotResult.MISS),
    (Cell(6, 6), ShotResult.MISS),
    (Cell(3, 7), ShotResult.MISS),
    (Cell(7, 3), ShotResult.MISS),
    (Cell(7, 7), ShotResult.MISS),
    (Cell(1, 5), ShotResult.MISS),
    (Cell(4, 8), ShotResult.MISS),
    (Cell(5, 1), ShotResult.MISS),
    (Cell(8, 4), ShotResult.MISS),
    (Cell(2, 6), ShotResult.MISS),
    (Cell(6, 2), ShotResult.MISS),
    (Cell(1, 1), ShotResult.HIT),
    (Cell(1, 2), ShotResult.MISS),
    (Cell(2, 1), ShotResult.HIT),
    (Cell(3, 1), ShotResult.SUNK),
]


@pytest.mark.parametrize("fleet", FLEETS)
def test_every_fleet_is_valid(fleet: Fleet) -> None:
    validate_fleet(rules_of(), fleet)


def test_fleets_are_distinct() -> None:
    assert len(set(FLEETS)) == len(FLEETS)


def test_hard_model_repeats_recorded_opening() -> None:
    shots, _ = play_out(HardModel(), RECORDED, 0)
    assert list(shots.items())[: len(RECORDED_OPENING)] == RECORDED_OPENING


@pytest.mark.parametrize("fleet", FLEETS)
def test_hard_needs_far_more_shots_than_on_a_random_fleet(fleet: Fleet) -> None:
    shots, _ = play_out(HardModel(), fleet, 0)
    assert len(shots) >= 85


def test_strategy_places_from_the_set() -> None:
    placed = {DensityStrategy().place(rules_of(), Random(seed)) for seed in range(100)}
    assert placed <= set(FLEETS)
    assert len(placed) > 1
