"""Офлайн-сравнение стратегий: сколько выстрелов нужно, чтобы потопить случайный флот.

Запуск: uv run python tools/bench_strategy.py [партий]
"""

import statistics
import sys
import time
from collections.abc import Callable
from random import Random
from types import MappingProxyType

from seabattle.model import Cell, Fleet, ShotResult
from seabattle.placement import random_fleet
from seabattle.state import ShotView
from seabattle.strategy import Strategy
from strategies.baseline import BaselineStrategy
from strategies.density import DensityStrategy
from tests.fake_platform import rules_of

MAX_SHOTS = 100


def shots_to_win(strategy: Strategy, fleet: Fleet, seed: int) -> tuple[int, float]:
    rules = rules_of()
    rng = Random(seed)
    shots: dict[Cell, ShotResult] = {}
    cells = {c: i for i, ship in enumerate(fleet) for c in ship.cells}
    left = {i: ship.length for i, ship in enumerate(fleet)}
    slowest = 0.0
    while any(left.values()) and len(shots) < MAX_SHOTS:
        view = ShotView(rules, MappingProxyType(dict(shots)), MappingProxyType({}), MAX_SHOTS, 1)
        started = time.perf_counter()
        cell = strategy.choose_shot(view, rng, 0.0)
        slowest = max(slowest, time.perf_counter() - started)
        if cell in shots or cell not in cells:
            shots[cell] = ShotResult.MISS
            continue
        ship = cells[cell]
        left[ship] -= 1
        shots[cell] = ShotResult.SUNK if left[ship] == 0 else ShotResult.HIT
    return len(shots), slowest


def main() -> None:
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rules = rules_of()
    fleets = [random_fleet(rules, Random(10_000 + i)) for i in range(games)]
    contenders: dict[str, Callable[[], Strategy]] = {
        "baseline": BaselineStrategy,
        "density": DensityStrategy,
    }
    for name, make in contenders.items():
        results = [shots_to_win(make(), fleet, i) for i, fleet in enumerate(fleets)]
        counts = sorted(r[0] for r in results)
        print(  # noqa: T201
            f"{name:9} mean={statistics.mean(counts):.1f} median={statistics.median(counts)} "
            f"p90={counts[int(games * 0.9)]} worst={counts[-1]} "
            f"slowest_move={max(r[1] for r in results) * 1000:.0f}ms"
        )


if __name__ == "__main__":
    main()
