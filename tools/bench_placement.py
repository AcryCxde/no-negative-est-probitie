"""Какие расстановки сложнее найти: среднее число выстрелов стрелка до победы (больше — лучше нам).

Запуск: PYTHONPATH=. uv run python tools/bench_placement.py [партий]
«HARD model» — модель встроенного бота (tools/hard_model.py): детерминирована, сид не влияет.
"""

import statistics
import sys
from collections.abc import Callable
from random import Random

from seabattle.model import Cell, Fleet, Rules
from seabattle.placement import random_fleet
from seabattle.strategy import Strategy
from strategies.baseline import BaselineStrategy
from strategies.compact import compact_fleet
from strategies.density import DensityStrategy
from strategies.hard_fleets import hard_fleet
from tests.fake_platform import rules_of
from tools.bench_strategy import shots_to_win
from tools.hard_model import HardModel

Family = Callable[[Rules, Random], Fleet]


def block(rows: range, columns: range) -> set[Cell]:
    return {Cell(r, c) for r in rows for c in columns}


def uniform(rules: Rules, rng: Random) -> Fleet:
    return random_fleet(rules, rng)


def no_border(rules: Rules, rng: Random) -> Fleet:
    border = {c for c in rules.cells() if c.row in (0, 9) or c.column in (0, 9)}
    return random_fleet(rules, rng, border)


def no_center(rules: Rules, rng: Random) -> Fleet:
    return random_fleet(rules, rng, block(range(3, 7), range(3, 7)))


def no_corners(rules: Rules, rng: Random) -> Fleet:
    corners = block(range(0, 2), range(0, 2)) | block(range(0, 2), range(8, 10))
    corners |= block(range(8, 10), range(0, 2)) | block(range(8, 10), range(8, 10))
    return random_fleet(rules, rng, corners)


def half_board(rules: Rules, rng: Random) -> Fleet:
    return random_fleet(rules, rng, block(range(0, 10), range(0, 3)))


def columns_free(width: int) -> Family:
    def family(rules: Rules, rng: Random) -> Fleet:
        return random_fleet(rules, rng, block(range(0, 10), range(0, width)))

    return family


def quadrant_free(size: int) -> Family:
    def family(rules: Rules, rng: Random) -> Fleet:
        return random_fleet(rules, rng, block(range(0, size), range(0, size)))

    return family


def diagonal_half(rules: Rules, rng: Random) -> Fleet:
    return random_fleet(rules, rng, {c for c in rules.cells() if c.row + c.column < 8})


FAMILIES: dict[str, Family] = {
    "uniform": uniform,
    "half board": compact_fleet,
    "tuned vs HARD": lambda _rules, rng: hard_fleet(rng),
    "free cols 0-4": columns_free(5),
}


def main() -> None:
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    rules = rules_of()
    shooters: dict[str, Callable[[], Strategy]] = {
        "baseline": BaselineStrategy,
        "density": DensityStrategy,
        "HARD model": HardModel,
    }
    for family, make in FAMILIES.items():
        fleets = [make(rules, Random(50_000 + i)) for i in range(games)]
        line = f"{family:18}"
        for shooter, new in shooters.items():
            counts = [shots_to_win(new(), fleet, i)[0] for i, fleet in enumerate(fleets)]
            line += f" {shooter}={statistics.mean(counts):.1f}"
        print(line)  # noqa: T201


if __name__ == "__main__":
    main()
