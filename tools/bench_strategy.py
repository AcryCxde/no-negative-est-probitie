"""Офлайн-сравнение стратегий: сколько выстрелов нужно, чтобы потопить случайный флот.

Запуск: PYTHONPATH=. uv run python tools/bench_strategy.py [партий] [стратегии через запятую]
Стратегии: baseline, density. Партии идут параллельно по всем ядрам.
"""

import statistics
import sys
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
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
THINK_SECONDS = 0.4  # как рабочий deadline бота: 0.4 от секунды на ход
STRATEGIES: dict[str, Callable[[], Strategy]] = {
    "baseline": BaselineStrategy,
    "density": DensityStrategy,
}


def shots_to_win(strategy: Strategy, fleet: Fleet, seed: int) -> tuple[int, float]:
    shots, slowest = play_out(strategy, fleet, seed)
    return len(shots), slowest


def play_out(strategy: Strategy, fleet: Fleet, seed: int) -> tuple[dict[Cell, ShotResult], float]:
    """Все выстрелы партии в порядке стрельбы и самый долгий ход."""
    rules = rules_of()
    rng = Random(seed)
    shots: dict[Cell, ShotResult] = {}
    cells = {c: i for i, ship in enumerate(fleet) for c in ship.cells}
    left = {i: ship.length for i, ship in enumerate(fleet)}
    slowest = 0.0
    while any(left.values()) and len(shots) < MAX_SHOTS:
        view = ShotView(rules, MappingProxyType(dict(shots)), MappingProxyType({}), MAX_SHOTS, 1)
        started = time.perf_counter()
        cell = strategy.choose_shot(view, rng, time.monotonic() + THINK_SECONDS)
        slowest = max(slowest, time.perf_counter() - started)
        if cell in shots or cell not in cells:
            shots[cell] = ShotResult.MISS
            continue
        ship = cells[cell]
        left[ship] -= 1
        shots[cell] = ShotResult.SUNK if left[ship] == 0 else ShotResult.HIT
    return shots, slowest


def play(job: tuple[str, int]) -> tuple[int, float]:
    name, index = job
    fleet = random_fleet(rules_of(), Random(10_000 + index))
    return shots_to_win(STRATEGIES[name](), fleet, index)


def main() -> None:
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else list(STRATEGIES)
    with ProcessPoolExecutor() as pool:
        for name in names:
            results = list(pool.map(play, [(name, i) for i in range(games)], chunksize=4))
            counts = sorted(r[0] for r in results)
            print(  # noqa: T201
                f"{name:10} mean={statistics.mean(counts):.1f} median={statistics.median(counts)} "
                f"p90={counts[int(games * 0.9)]} worst={counts[-1]} "
                f"slowest_move={max(r[1] for r in results) * 1000:.0f}ms"
            )


if __name__ == "__main__":
    main()
