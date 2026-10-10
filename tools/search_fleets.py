"""Подбор расстановок против модели HARD: отжиг по числу выстрелов, которые HARD тратит на флот.

Запуск: PYTHONPATH=. uv run python tools/search_fleets.py [цепочек] [шагов в цепочке]
Печатает «выстрелов HARD» и флот в формате strategies/hard_fleets.py, лучшие первыми.
"""

import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from random import Random

from seabattle.model import Cell, Fleet, Ship
from seabattle.placement import random_fleet
from tests.fake_platform import rules_of
from tools.bench_strategy import shots_to_win
from tools.hard_model import HardModel

START_TEMPERATURE = 3.0
SIZE = 10


def line(row: int, column: int, length: int, vertical: bool) -> Ship:
    step = (1, 0) if vertical else (0, 1)
    return Ship(tuple(Cell(row + step[0] * i, column + step[1] * i) for i in range(length)))


def placements(length: int) -> list[Ship]:
    found = [
        line(r, c, length, vertical)
        for r in range(SIZE)
        for c in range(SIZE)
        for vertical in (False, True)
        if (r + length <= SIZE if vertical else c + length <= SIZE)
    ]
    return list({ship.cells: ship for ship in found}.values())


def score(fleet: Fleet) -> int:
    return shots_to_win(HardModel(), fleet, 0)[0]


def move(fleet: Fleet, rng: Random) -> Fleet | None:
    index = rng.randrange(len(fleet))
    others = fleet[:index] + fleet[index + 1 :]
    blocked = {n for ship in others for c in ship.cells for n in (c, *c.neighbors8())}
    free = [s for s in placements(fleet[index].length) if blocked.isdisjoint(s.cells)]
    if not free:
        return None
    return (*fleet[:index], rng.choice(free), *fleet[index + 1 :])


def anneal(job: tuple[int, int]) -> tuple[int, Fleet]:
    seed, steps = job
    rng = Random(seed)
    current = random_fleet(rules_of(), rng)
    current_score = score(current)
    best, best_score = current, current_score
    for step in range(steps):
        candidate = move(current, rng)
        if candidate is None:
            continue
        candidate_score = score(candidate)
        temperature = START_TEMPERATURE * (1 - step / steps) + 0.05
        if candidate_score >= current_score or rng.random() < math.exp(
            (candidate_score - current_score) / temperature
        ):
            current, current_score = candidate, candidate_score
            if current_score > best_score:
                best, best_score = current, current_score
    return best_score, best


def encoded(fleet: Fleet) -> str:
    """Корабль — «строка,столбец,длина» и h/v; формат читает strategies.hard_fleets."""
    return " ".join(
        f"{ship.cells[0].row},{ship.cells[0].column},{ship.length}"
        f"{'v' if ship.length > 1 and ship.cells[0].column == ship.cells[1].column else 'h'}"
        for ship in fleet
    )


def main() -> None:
    chains = int(sys.argv[1]) if len(sys.argv) > 1 else (os.cpu_count() or 1)
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
    with ProcessPoolExecutor() as pool:
        results = sorted(pool.map(anneal, [(i, steps) for i in range(chains)]), key=lambda r: -r[0])
    for value, fleet in results:
        print(f"{value} {encoded(fleet)}")  # noqa: T201


if __name__ == "__main__":
    main()
