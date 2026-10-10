"""Offline SCHRODINGER_SHIP check: HIT replaces SUNK, count shots to sink the whole fleet.

Boards are random valid fleets (half interior-first, like our own placement).
Usage: uv run python scripts/sim_schrodinger.py [boards] [strategy ...]
"""
from __future__ import annotations

import random
import statistics
import sys
import time

from battleship_hack.game import STRATEGIES, cell_json, parse_cell, place

RULES = {"rows": 10, "columns": 10, "touchRule": "NO_TOUCHING_EVEN_DIAGONALLY", "modifier": "SCHRODINGER_SHIP",
         "fleet": [{"length": 4, "count": 1}, {"length": 3, "count": 2}, {"length": 2, "count": 3},
                   {"length": 1, "count": 4}],
         "moveTimeoutSeconds": 1, "placementWindowSeconds": 10, "shotsPerTurn": 1,
         "shotLimitPerPlayer": 100, "turnAfterHitRule": "SHOOTER_KEEPS_TURN"}


def play(name: str, ships: list[set], seed: int) -> tuple[int, int, list[float]]:
    bot = STRATEGIES[name](RULES, seed)
    alive = {cell for ship in ships for cell in ship}
    shots, timings = 0, []
    while alive and shots < 100:
        started = time.perf_counter()
        cell, _ = bot.choose()
        timings.append((time.perf_counter() - started) * 1000)
        shots += 1
        result = "HIT" if cell in alive else "MISS"
        alive.discard(cell)
        bot.observe({"cell": cell_json(cell), "result": result})
    return shots, 20 - len(alive), timings


def main():
    boards = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    names = sys.argv[2:] or ["fleet-mc-interior", "fleet-mc-schrodinger"]
    layouts = []
    for n in range(boards):
        fleet = place(RULES, random.Random(9000 + n), prefer_interior=n % 2 == 1)
        layouts.append([{parse_cell(c) for c in ship["cells"]} for ship in fleet])
    results = {}
    for name in names:
        shots, hits, timings = [], [], []
        for n, ships in enumerate(layouts):
            s, h, t = play(name, ships, 500 + n)
            shots.append(s)
            hits.append(h)
            timings.extend(t)
        timings.sort()
        results[name] = shots
        print(f"{name:24s} mean_shots={statistics.mean(shots):6.2f} unfinished={sum(h < 20 for h in hits)} "
              f"p95_ms={timings[int(len(timings) * 0.95)]:.1f} max_ms={timings[-1]:.1f}", flush=True)
    if len(names) == 2:
        a, b = results[names[0]], results[names[1]]
        better = sum(y < x for x, y in zip(a, b))
        worse = sum(y > x for x, y in zip(a, b))
        print(f"delta {names[1]} - {names[0]} = {statistics.mean(b) - statistics.mean(a):+.2f}; "
              f"better/worse/tie={better}/{worse}/{len(a) - better - worse}")


if __name__ == "__main__":
    main()
