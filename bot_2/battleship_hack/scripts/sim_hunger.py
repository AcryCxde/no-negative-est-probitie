"""HUNGER_GAMES shooting: hits within the shot limit on real and uniform boards.

Real boards are opponents' full fleets from our logs (skewed toward our wins);
compare variants with each other. SUNK is announced (no Schrodinger).
Usage: uv run python scripts/sim_hunger.py [boards] [limit] [variant ...]
"""
from __future__ import annotations

import collections
import os
import random
import statistics
import sys
from multiprocessing import Pool

from battleship_hack.game import FleetMCHunger, FleetMCHungerV2, FleetMCSchrodingerRimV2, cell_json, place
from sim_real_boards import RULES as BASE_RULES, load_boards
from analyze_placement_logs import components


class Greedy(FleetMCHunger):
    """Same code path with every ship worth one hit: plain P(hit) hunt."""
    def ship_value(self, length: int) -> float:
        return 1.0


VARIANTS = {"rim-v2": FleetMCSchrodingerRimV2, "greedy": Greedy, "hunger": FleetMCHunger,
            "hunger-v2": FleetMCHungerV2}


def play(job):
    name, source, index, cells, limit = job
    rules = {**BASE_RULES, "modifier": "HUNGER_GAMES", "shotLimitPerPlayer": limit}
    bot = VARIANTS[name](rules, 700 + index)
    ships = [set(ship) for ship in components(cells)]
    hits = 0
    for _ in range(limit):
        cell, _ = bot.choose()
        ship = next((s for s in ships if cell in s), None)
        result = "MISS"
        if ship is not None:
            hits += 1
            ship.discard(cell)
            result = "HIT" if ship else "SUNK"
        bot.observe({"cell": cell_json(cell), "result": result})
    return name, source, index, hits


def main():
    numbers = [int(a) for a in sys.argv[1:] if a.isdigit()]
    n, limit = (numbers + [120, 20])[:2] if numbers else (120, 20)
    names = [a for a in sys.argv[1:] if not a.isdigit()] or list(VARIANTS)
    real = [cells for _, cells in load_boards()]
    boards = {"real": real[::max(1, len(real) // n)][:n],
              "uniform": [frozenset((c["row"], c["column"]) for s in place(BASE_RULES, random.Random(81000 + i))
                                    for c in s["cells"]) for i in range(n)]}
    jobs = [(name, src, i, cells, limit) for name in names for src, bs in boards.items() for i, cells in enumerate(bs)]
    results = collections.defaultdict(dict)
    # Oversubscribing the per-move wall-clock budget changes the MC sample count.
    with Pool(min(4, os.cpu_count() or 1)) as pool:
        for name, src, i, hits in pool.imap_unordered(play, jobs, chunksize=2):
            results[name, src][i] = hits
    print(f"limit={limit}; boards real={len(boards['real'])} uniform={len(boards['uniform'])}")
    for src in boards:
        base = results[names[0], src]
        for name in names:
            r = results[name, src]
            diffs = [r[i] - base[i] for i in base]
            se = statistics.stdev(diffs) / len(diffs) ** 0.5 if name != names[0] else 0
            print(f"{src:8} {name:8} mean_hits={statistics.mean(r.values()):5.2f}  vs {names[0]}: "
                  f"{statistics.mean(diffs):+.2f} ± {se:.2f}  better/worse={sum(d > 0 for d in diffs)}/{sum(d < 0 for d in diffs)}")


if __name__ == "__main__":
    main()
