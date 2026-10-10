"""Сколько выстрелов стрелок тратит на флот каждого семейства (больше — лучше нам).

Запуск: PYTHONPATH=. uv run python bot_1/tools/bench_fleets.py [партий]
«HARD model» — модель встроенного бота, детерминирована: сид не влияет.
"""

import statistics
import sys
from collections.abc import Callable
from random import Random

from seabattle.model import Fleet, Rules
from seabattle.placement import random_fleet
from seabattle.strategy import Strategy
from strategies.baseline import BaselineStrategy
from strategies.compact import compact_fleet
from strategies.density import DensityStrategy
from tests.fake_platform import rules_of
from tools.bench_strategy import shots_to_win

from bot_1.hard_fleets import hard_fleet
from bot_1.tools.hard_model import HardModel

Family = Callable[[Rules, Random], Fleet]

FAMILIES: dict[str, Family] = {
    "uniform": random_fleet,
    "half board": compact_fleet,
    "tuned vs HARD": hard_fleet,
}
SHOOTERS: dict[str, Callable[[], Strategy]] = {
    "baseline": BaselineStrategy,
    "density": DensityStrategy,
    "HARD model": HardModel,
}


def main() -> None:
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    rules = rules_of()
    for family, make in FAMILIES.items():
        fleets = [make(rules, Random(50_000 + i)) for i in range(games)]
        line = f"{family:14}"
        for shooter, new in SHOOTERS.items():
            counts = [shots_to_win(new(), fleet, i)[0] for i, fleet in enumerate(fleets)]
            line += f" {shooter}={statistics.mean(counts):.1f}"
        print(line)  # noqa: T201


if __name__ == "__main__":
    main()
