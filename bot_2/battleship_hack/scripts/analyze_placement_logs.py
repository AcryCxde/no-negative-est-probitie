"""Placement lessons from logs only (no game simulation; single core, seconds).

1) Our ships: when does the opponent first hit / finish each ship, by length and position.
2) Opponent boards (full 20-hit fleets from our wins) vs random valid boards: shape features.
Usage: uv run python scripts/analyze_placement_logs.py [SCHRODINGER_SHIP|ALL]
"""
from __future__ import annotations

import collections
import glob
import json
import os
import random
import statistics
import sys

from battleship_hack.game import neighbors, place

MOD = sys.argv[1] if len(sys.argv) > 1 else "SCHRODINGER_SHIP"
RULES = {"rows": 10, "columns": 10, "touchRule": "NO_TOUCHING_EVEN_DIAGONALLY", "placementWindowSeconds": 10,
         "fleet": [{"length": 4, "count": 1}, {"length": 3, "count": 2}, {"length": 2, "count": 3},
                   {"length": 1, "count": 4}]}


def depth(cell):
    r, c = cell
    return min(r, 9 - r, c, 9 - c)


def position(ship):
    if len(ship) == 1:
        r, c = ship[0]
        return "corner" if r in (0, 9) and c in (0, 9) else "edge" if depth(ship[0]) == 0 else f"depth{min(depth(ship[0]), 2)}"
    return "rim" if all(depth(x) == 0 for x in ship) else "touches-rim" if any(depth(x) == 0 for x in ship) else "inner"


def components(cells):
    left, out = set(cells), []
    while left:
        comp, stack = set(), [left.pop()]
        while stack:
            x = stack.pop()
            comp.add(x)
            nxt = neighbors(x) & left
            left -= nxt
            stack.extend(nxt)
        out.append(sorted(comp))
    return out


def features(cells):
    ships = components(cells)
    covered = set(cells).union(*(neighbors(x, True) for x in cells))
    singles = [s for s in ships if len(s) == 1]
    multi = [s for s in ships if len(s) > 1]
    quad = collections.Counter((r >= 5, c >= 5) for r, c in cells)
    return {
        "rim_cells": sum(depth(x) == 0 for x in cells),
        "multi_on_rim": sum(position(s) == "rim" for s in multi),
        "singles_rim": sum(depth(s[0]) == 0 for s in singles),
        "singles_corner": sum(position(s) == "corner" for s in singles),
        "covered(ship+halo)": len(covered),
        "horizontal_multi": sum(s[0][0] == s[-1][0] for s in multi),
        "max_quadrant_cells": max(quad.values()),
        "free_rows": sum(all((r, c) not in cells for c in range(10)) for r in range(10)),
        "free_cols": sum(all((r, c) not in cells for r in range(10)) for c in range(10)),
    }


def load():
    battles = []
    for path in sorted(glob.glob("data/bot2/logs/*.jsonl"), key=os.path.getmtime):
        cur = {}
        for line in open(path):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t, p, b = r["type"], r["payload"], r["battle_id"]
            if t == "BATTLE_STARTED":
                cur[b] = dict(team=p["opponent"].get("teamName"), mod=p["rules"].get("modifier"),
                              strategy=p.get("strategy"), ships=None, opp=[], ours=[], out=None)
            elif b not in cur:
                continue
            elif t == "PLACE":
                cur[b]["ships"] = [[(c["row"], c["column"]) for c in s["cells"]] for s in p["ships"]]
            elif t == "OPPONENT_SHOT":
                cur[b]["opp"].append(((p["cell"]["row"], p["cell"]["column"]), p["result"]))
            elif t == "SHOT_RESOLVED":
                cur[b]["ours"].append(((p["cell"]["row"], p["cell"]["column"]), p["result"]))
            elif t == "MATCH_FINISHED":
                cur[b]["out"] = p["outcome"]
        battles += [d for d in cur.values() if d["out"] and d["team"] and (MOD == "ALL" or d["mod"] == MOD)]
    return battles


def our_ships(battles):
    first, done, alive = collections.defaultdict(list), collections.defaultdict(list), collections.Counter()
    total = collections.Counter()
    for d in battles:
        if not d["ships"] or not d["opp"]:
            continue
        index = {}
        for n, (cell, res) in enumerate(d["opp"], 1):
            if res in ("HIT", "SUNK"):
                index[cell] = n
        n_shots = len(d["opp"])
        for ship in d["ships"]:
            key = (len(ship), position(ship))
            total[key] += 1
            hits = [index[x] for x in ship if x in index]
            # Shot index normalised by this opponent's total shots in the match (0..1).
            first.setdefault(key, [])
            if hits:
                first[key].append(min(hits) / n_shots)
            if len(hits) == len(ship):
                done[key].append(max(hits) / n_shots)
            else:
                alive[key] += 1
    print(f"Our ships ({MOD}, {len(battles)} battles): when the opponent first hits / finishes them "
          f"(fraction of opponent's shots in the match; higher = survives longer)")
    print(f"  {'len pos':18} {'n':>4} {'first hit':>9} {'finished':>9} {'survived':>9}")
    for key in sorted(total):
        f, dn = first[key], done[key]
        print(f"  {str(key):18} {total[key]:4} {statistics.mean(f) if f else float('nan'):9.2f} "
              f"{statistics.mean(dn) if dn else float('nan'):9.2f} {alive[key] / total[key]:9.0%}")


def opp_boards(battles):
    real = collections.defaultdict(list)
    for d in battles:
        hits = {c for c, r in d["ours"] if r in ("HIT", "SUNK")}
        if len(hits) == 20:
            real[d["team"]].append(hits)
    rng_boards = []
    for seed in range(300):
        rng_boards.append({(c["row"], c["column"]) for s in place(RULES, random.Random(50000 + seed)) for c in s["cells"]})
    base = [features(b) for b in rng_boards]
    allreal = [features(b) for bs in real.values() for b in bs]
    keys = list(base[0])
    print(f"\nOpponent boards: random valid placement (300) vs real full boards ({len(allreal)}), means")
    print(f"  {'feature':22} {'random':>7} {'real':>7}  " + "  ".join(t[:10] for t in real if len(real[t]) >= 5))
    for k in keys:
        per_team = "  ".join(f"{statistics.mean(features(b)[k] for b in real[t]):10.1f}" for t in real if len(real[t]) >= 5)
        print(f"  {k:22} {statistics.mean(f[k] for f in base):7.2f} {statistics.mean(f[k] for f in allreal):7.2f}  {per_team}")
    print("  team sample sizes:", {t[:14]: len(v) for t, v in real.items()})


if __name__ == "__main__":
    data = load()
    our_ships(data)
    # Opponent placement should not depend on the modifier: use every battle for sample size.
    MOD = "ALL"
    opp_boards(load())
