"""Offline misses on «Низкие ожидания» boards: fleet-mc-lowexp vs current ranking policies.

Boards: fully sunk fleets plus one completion of each partial board (remaining
ships sampled uniformly, consistent with misses/halos). The lowexp prior is
re-estimated without the evaluated match (leave-one-out). Bonuses go through
the real BonusBag with 3 DISTANCE uses; metric is misses until the fleet sinks.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_low_expectations import clusters_from_shots, load, random_fleet, ring  # noqa: E402
from sim_last_ranking import complete_board, reconstruct_ships  # noqa: E402

from battleship_hack.bonuses import BonusBag  # noqa: E402
from battleship_hack.game import STRATEGIES, FleetMCLowExp, cell_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SEEDS = (81001, 81002)
PSEUDO = 4


def bucket(ship) -> int:
    return min(min(ring(*p) for p in ship), 2)


def uniform_buckets(samples: int = 40000) -> dict:
    rng, counts, totals = random.Random(3), Counter(), Counter()
    for _ in range(samples):
        for ship in random_fleet(rng):
            totals[len(ship)] += 1
            counts[(len(ship), bucket(ship))] += 1
    return {n: [counts[(n, i)] / totals[n] for i in range(3)] for n in totals}


def prior_table(battles, skip: int, uniform: dict) -> dict:
    counts, totals = Counter(), Counter()
    for i, battle in enumerate(battles):
        if i == skip:
            continue
        for ship in clusters_from_shots(battle["our_shots"]):
            totals[len(ship)] += 1
            counts[(len(ship), bucket(ship))] += 1
    return {n: tuple(round((counts[(n, i)] + PSEUDO * u[i]) / (totals[n] + PSEUDO) / u[i], 3)
                     for i in range(3)) for n, u in uniform.items()}


def variant(name: str, prior: dict):
    if name == "lowexp":
        return type("LowExpLOO", (FleetMCLowExp,), {"PRIOR": prior})
    if name == "lowexp-early-distance":
        return type("LowExpEarly", (FleetMCLowExp,), {"PRIOR": prior, "distance_policy": None})
    if name == "uniform-endgame-distance":
        return type("UniformEnd", (FleetMCLowExp,), {"PRIOR": {}})
    return STRATEGIES[name]


def play(cls, rules: dict, ships: list, seed: int) -> dict:
    model = cls(rules, seed)
    bag = BonusBag(model, {"bonuses": [{"bonus": "DISTANCE", "uses": 3}]})
    occupied = set().union(*ships)
    sunk: set = set()
    misses = shots = bonus = 0
    limit = rules.get("shotLimitPerPlayer", 100)
    while +model.remaining and shots + bonus < limit:
        action, payload = bag.choose(limit - shots - bonus, 1, shots + bonus + 1)
        if action == "USE_BONUS":
            center = (payload["cell"]["row"], payload["cell"]["column"])
            alive = occupied - sunk
            distance = min(abs(center[0] - r) + abs(center[1] - c) for r, c in alive)
            bag.apply_resolved({"bonus": "DISTANCE", "distance": distance})
            bonus += 1
            continue
        cell = (payload["cell"]["row"], payload["cell"]["column"])
        assert cell not in model.used, (cls.__name__, cell)
        if cell in occupied:
            ship = next(s for s in ships if cell in s)
            result = "SUNK" if ship <= model.used | {cell} else "HIT"
            if result == "SUNK":
                sunk |= ship
        else:
            result = "MISS"
            misses += 1
        model.observe({"cell": cell_json(cell), "result": result})
        shots += 1
    return {"misses": misses, "shots": shots, "bonus": bonus, "finished": not +model.remaining}


def job(args):
    index, board, rules, prior, seed, names = args
    return index, seed, {name: play(variant(name, prior), rules, board, seed) for name in names}


def main() -> None:
    names = sys.argv[1:] or ["fleet-mc-duel-v2", "fleet-mc-interior", "uniform-endgame-distance",
                             "lowexp-early-distance", "lowexp"]
    battles = load()
    uniform = uniform_buckets()
    jobs, meta = [], []
    for index, battle in enumerate(battles):
        shots = [{"cell": cell_json(p), "result": r} for p, r in battle["our_shots"]]
        ships, leftover, misses = reconstruct_ships(shots)
        rules = battle["rules"]
        full = sum(map(len, ships)) == 20 and not leftover
        if full:
            board = ships
        else:
            board = complete_board({"rules": rules, "revealed_ships": [sorted(s) for s in ships],
                                    "leftover_hits": sorted(leftover), "misses": sorted(misses)},
                                   random.Random(index))
        if not board or sum(map(len, board)) != 20:
            continue
        prior = prior_table(battles, index, uniform)
        meta.append({"index": index, "bot": battle["bot"], "modifier": battle["modifier"], "full": full})
        for seed in SEEDS:
            jobs.append((index, [set(s) for s in board], rules, prior, seed, names))
    rows = []
    with ProcessPoolExecutor(max_workers=6) as pool:
        for index, seed, result in pool.map(job, jobs):
            rows.append({"index": index, "seed": seed, "full": meta[[m["index"] for m in meta].index(index)]["full"],
                         "results": result})
    summary = {}
    for subset in ("all", "full"):
        chosen = [r for r in rows if subset == "all" or r["full"]]
        summary[subset] = {name: {
            "games": len(chosen),
            "mean_misses": round(statistics.mean(r["results"][name]["misses"] for r in chosen), 2),
            "median_misses": statistics.median(r["results"][name]["misses"] for r in chosen),
            "mean_distance_uses": round(statistics.mean(r["results"][name]["bonus"] for r in chosen), 2),
            "unfinished": sum(not r["results"][name]["finished"] for r in chosen),
        } for name in names}
        base = names[0]
        for name in names[1:]:
            diffs = [r["results"][name]["misses"] - r["results"][base]["misses"] for r in chosen]
            summary[subset][name]["delta_vs_" + base] = round(statistics.mean(diffs), 2)
            summary[subset][name]["stderr"] = round(statistics.stdev(diffs) / len(diffs) ** 0.5, 2)
            summary[subset][name]["better_worse_equal"] = [sum(d < 0 for d in diffs), sum(d > 0 for d in diffs),
                                                           sum(d == 0 for d in diffs)]
    out = ROOT / "data/bot2/lowexp-sim.json"
    out.write_text(json.dumps({"boards": meta, "summary": summary, "rows": rows}, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
