"""Which own-placement families make a hunter need the most shots (Schrodinger)?

Shooters (proxies for opponents): `neutral` = plain MC, `inland` = MC with interior bias
(how most opponents opened against us). Higher mean shots = better placement for us.
Usage: uv run python scripts/sim_placement.py [boards_per_family] [family ...]
"""
from __future__ import annotations

import collections
import os
import random
import statistics
import sys
from multiprocessing import Pool

from battleship_hack.game import FleetMCSchrodinger, cell_json, place, validate_placement
from sim_real_boards import RULES, CELLS, Neutral, load_boards  # noqa: E402

SHOOTERS = {"neutral": Neutral, "inland": FleetMCSchrodinger}


def rim_share(share: float):
    def gen(seed):
        rng = random.Random(seed)
        if rng.random() < share:
            return place(RULES, rng, perimeter_multi=True)
        return place(RULES, rng)
    return gen


def corner_singles(seed):
    """Multi-cell ships on the rim, singles prefer corners/edges when possible (random otherwise)."""
    return place(RULES, random.Random(seed), perimeter_multi=True)


FAMILIES = {
    "uniform": lambda s: place(RULES, random.Random(s)),
    "interior": lambda s: place(RULES, random.Random(s), prefer_interior=True),
    "rim80": rim_share(0.8),
    "rim100": rim_share(1.0),
}


def cells_of(ships):
    return frozenset((c["row"], c["column"]) for ship in ships for c in ship["cells"])


def play(job):
    family, shooter, index, cells = job
    bot = SHOOTERS[shooter](RULES, 900 + index)
    alive, shots = set(cells), 0
    while alive and shots < 100:
        cell, _ = bot.choose()
        shots += 1
        hit = cell in alive
        alive.discard(cell)
        bot.observe({"cell": cell_json(cell), "result": "HIT" if hit else "MISS"})
    return family, shooter, index, shots


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 120
    names = [a for a in sys.argv[1:] if not a.isdigit()] or list(FAMILIES) + ["real"]
    boards = {}
    for name in names:
        if name == "real":
            real = [cells for _, cells in load_boards()]
            boards[name] = real[::max(1, len(real) // n)][:n]
        else:
            made = []
            for seed in range(n):
                ships = FAMILIES[name](31000 + seed)
                validate_placement(RULES, ships)
                made.append(cells_of(ships))
            boards[name] = made
    jobs = [(f, s, i, c) for f, bs in boards.items() for s in SHOOTERS for i, c in enumerate(bs)]
    results = collections.defaultdict(list)
    with Pool(os.cpu_count() or 4) as pool:
        for family, shooter, _, shots in pool.imap_unordered(play, jobs, chunksize=2):
            results[family, shooter].append(shots)
    print(f"{'family':10} " + " ".join(f"{s:>16}" for s in SHOOTERS))
    for f in boards:
        print(f"{f:10} " + " ".join(
            f"{statistics.mean(results[f, s]):8.2f} ±{statistics.stdev(results[f, s]) / len(results[f, s]) ** 0.5:4.2f}"
            for s in SHOOTERS))


if __name__ == "__main__":
    main()
