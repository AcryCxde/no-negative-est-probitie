"""Replay SCHRODINGER_SHIP shooting on REAL opponent boards recovered from our logs.

Boards = opponents' full fleets (20 HIT cells) from all our battles. Only HIT/MISS are
shown to the shooter (Schrodinger). Team priors are leave-one-out (the evaluated board is
excluded from its own prior). Boards come from our wins, so they skew toward easy ones;
compare variants against each other, not against live shot counts.

Usage: uv run python scripts/sim_real_boards.py [max_boards] [variant ...]
"""
from __future__ import annotations

import collections
import glob
import json
import os
import random
import statistics
import sys
from multiprocessing import Pool

from battleship_hack.game import FleetMCSchrodinger, FleetMCSchrodingerRim, cell_json, place

RULES = {"rows": 10, "columns": 10, "touchRule": "NO_TOUCHING_EVEN_DIAGONALLY", "modifier": "SCHRODINGER_SHIP",
         "fleet": [{"length": 4, "count": 1}, {"length": 3, "count": 2}, {"length": 2, "count": 3},
                   {"length": 1, "count": 4}],
         "moveTimeoutSeconds": 1, "placementWindowSeconds": 10, "shotsPerTurn": 1,
         "shotLimitPerPlayer": 100, "turnAfterHitRule": "SHOOTER_KEEPS_TURN"}
CELLS = [(r, c) for r in range(10) for c in range(10)]


def load_boards() -> list[tuple[str, frozenset]]:
    boards = []
    for path in sorted(glob.glob("data/bot2/logs/*.jsonl"), key=os.path.getmtime):
        cur = {}
        for line in open(path):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t, p, b = r["type"], r["payload"], r["battle_id"]
            if t == "BATTLE_STARTED":
                cur[b] = (p["opponent"].get("teamName"), set())
            elif b in cur and t == "SHOT_RESOLVED" and p["result"] in ("HIT", "SUNK"):
                cur[b][1].add((p["cell"]["row"], p["cell"]["column"]))
        boards += [(team, frozenset(h)) for team, h in cur.values() if team and len(h) == 20]
    return boards


def uniform_occupancy(samples: int = 1500) -> dict:
    counts = collections.Counter()
    for seed in range(samples):
        for ship in place(RULES, random.Random(70000 + seed)):
            counts.update((c["row"], c["column"]) for c in ship["cells"])
    return {cell: counts[cell] / samples for cell in CELLS}


class Neutral(FleetMCSchrodinger):
    """No interior bias: top MC cell(s), parity tie-break. Own placement irrelevant here."""
    def _pick_hunt(self, scored):
        top = max(scored.values())
        near = [c for c, s in scored.items() if s >= top * 0.97]
        even = [c for c in near if (c[0] + c[1]) % 2 == 0]
        return self.rng.choice(sorted(even or near))


class Heat(Neutral):
    """Neutral + multiplicative per-cell prior ratio (team frequency / uniform frequency)."""
    heat: dict = {}
    power = 1.0

    def _pick_hunt(self, scored):
        return super()._pick_hunt({c: s * self.heat.get(c, 1.0) ** self.power for c, s in scored.items()})


VARIANTS = {"current": FleetMCSchrodinger, "neutral": Neutral, "heat-team": Heat, "heat-pool": Heat}


def make_heat(boards: list, skip: int, team: str, uniform: dict, pooled: bool, strength: float = 8.0) -> dict:
    counts, n = collections.Counter(), 0
    for i, (t, cells) in enumerate(boards):
        if i != skip and (pooled or t == team):
            counts.update(cells)
            n += 1
    return {cell: ((counts[cell] + strength * uniform[cell]) / (n + strength)) / uniform[cell] for cell in CELLS}


def play(job):
    index, name, boards, uniform = job
    team, ships = boards[index]
    cls = VARIANTS[name]
    if name.startswith("heat"):
        cls = type(f"Heat_{index}", (Heat,), {"heat": make_heat(boards, index, team, uniform, name == "heat-pool")})
    bot = cls(RULES, 500 + index)
    alive, shots = set(ships), 0
    while alive and shots < 100:
        cell, _ = bot.choose()
        shots += 1
        hit = cell in alive
        alive.discard(cell)
        bot.observe({"cell": cell_json(cell), "result": "HIT" if hit else "MISS"})
    return index, name, shots


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 10 ** 9
    names = [a for a in sys.argv[1:] if not a.isdigit()] or list(VARIANTS)
    boards = load_boards()
    # Keep order stable but cap the number of evaluated boards (spread across teams).
    evaluated = list(range(len(boards)))[::max(1, len(boards) // limit)][:limit]
    uniform = uniform_occupancy()
    print(f"{len(boards)} real boards, evaluating {len(evaluated)}; teams: "
          f"{dict(collections.Counter(t for t, _ in boards))}", flush=True)
    jobs = [(i, n, boards, uniform) for n in names for i in evaluated]
    results = collections.defaultdict(dict)
    with Pool(os.cpu_count() or 4) as pool:
        for index, name, shots in pool.imap_unordered(play, jobs, chunksize=2):
            results[name][index] = shots
    base = results[names[0]]
    for name in names:
        r = results[name]
        diffs = [r[i] - base[i] for i in evaluated]
        se = statistics.stdev(diffs) / len(diffs) ** 0.5 if len(diffs) > 1 and name != names[0] else 0
        print(f"{name:12} mean_shots={statistics.mean(r.values()):6.2f}  vs {names[0]}: "
              f"{statistics.mean(diffs):+.2f} ± {se:.2f}  better/worse={sum(d < 0 for d in diffs)}/{sum(d > 0 for d in diffs)}")
    teams = sorted({boards[i][0] for i in evaluated})
    print("\nper team (mean shots):", "  ".join(names))
    for team in teams:
        idx = [i for i in evaluated if boards[i][0] == team]
        if len(idx) >= 5:
            print(f"  {team[:22]:22} n={len(idx):3}", "  ".join(f"{statistics.mean(results[n][i] for i in idx):6.2f}" for n in names))


if __name__ == "__main__":
    main()
