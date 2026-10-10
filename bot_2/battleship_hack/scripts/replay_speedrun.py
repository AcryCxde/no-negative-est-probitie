"""Compare shots to sink on fully revealed HARD/SPEEDRUN boards from JSONL.

No completed or invented loss boards: only fleets with every cell observed.
Usage: uv run python scripts/replay_speedrun.py --boards 10 --seeds 3
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from battleship_hack.game import STRATEGIES, cell_json, neighbors, parse_cell, validate_placement

ROOT = Path(__file__).resolve().parents[1]


def load_battles():
    battles = {}
    for path in sorted((ROOT / "data/bot2").glob("**/logs/*.jsonl")):
        run = {}
        for line in path.open():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # An active journal may end with an incomplete record.
            kind, payload, bid = event["type"], event["payload"], event.get("battle_id")
            if kind == "RUN_STARTED":
                run = payload
            if kind == "BATTLE_STARTED" and (payload["rules"].get("modifier") == "SPEEDRUN"
                    or payload["opponent"].get("builtInBot") == "HARD"):
                battles[bid] = {"battle_id": bid, "run_id": event["run_id"],
                    "at": event["timestamp"], "rules": payload["rules"],
                    "opponent": payload["opponent"], "strategy": payload.get("strategy", run.get("strategy")),
                    "shots": [], "decisions": [], "timeouts": 0, "rejections": 0}
            elif bid in battles:
                battle = battles[bid]
                if kind == "SHOT_RESOLVED":
                    battle["shots"].append(payload)
                elif kind == "DECISION":
                    battle["decisions"].append(payload)
                elif kind == "TURN_TIMED_OUT":
                    battle["timeouts"] += 1
                elif kind in {"SHOT_REJECTED", "COMMAND_REJECTED"}:
                    battle["rejections"] += 1
                elif kind == "BATTLE_FINISHED":
                    battle["end"] = payload
                elif kind == "HTTP_MATCH_LINKED":
                    battle["http"] = payload
    return sorted(battles.values(), key=lambda b: b["at"])


def full_fleet(battle):
    rules = battle["rules"]
    hits = {parse_cell(e["cell"]) for e in battle["shots"] if e["result"] in {"HIT", "SUNK"}}
    if len(hits) != sum(f["length"] * f["count"] for f in rules["fleet"]):
        return None
    ships = []
    while hits:
        ship = {hits.pop()}
        frontier = list(ship)
        while frontier:
            adjacent = neighbors(frontier.pop()) & hits
            hits -= adjacent
            ship |= adjacent
            frontier.extend(adjacent)
        ships.append(ship)
    validate_placement(rules, [{"cells": [cell_json(c) for c in ship]} for ship in ships])
    return [sorted(ship) for ship in ships]


def replay(job):
    board, name, seed = job
    rules = {**board["rules"], "modifier": "SPEEDRUN", "shotLimitPerPlayer": 100,
             "moveTimeoutSeconds": 1, "shotsPerTurn": 1}
    bot = STRATEGIES[name](rules, seed)
    bot.opponent_team = board["opponent"].get("teamName")
    ships = [set(map(tuple, ship)) for ship in board["fleet"]]
    occupied = set().union(*ships)
    durations, reasons = [], []
    for shots in range(100):
        bot.shots_left = 100 - shots
        started = time.perf_counter()
        cell, reason = bot.choose()
        durations.append((time.perf_counter() - started) * 1000)
        reasons.append(reason)
        assert cell not in bot.used | bot.empty, (name, cell, reason)
        if cell in occupied:
            ship = next(ship for ship in ships if cell in ship)
            result = "SUNK" if ship <= bot.used | {cell} else "HIT"
        else:
            result = "MISS"
        bot.observe({"cell": cell_json(cell), "result": result})
        if not +bot.remaining:
            return {"battle_id": board["battle_id"], "name": name, "seed": seed,
                    "shots": shots + 1, "mean_ms": statistics.mean(durations),
                    "max_ms": max(durations), "fallbacks": sum("fallback" in r for r in reasons),
                    "model_disabled": getattr(bot, "model_disabled", None),
                    "quadrants_disabled": getattr(bot, "quadrants_disabled", None)}
    raise RuntimeError(f"{name} did not finish {board['battle_id']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boards", type=int, default=10)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--strategies", nargs="+", default=["fleet-mc-hunger-v2", "fleet-mc-speedrun", "fleet-mc-speedrun-v2"])
    parser.add_argument("--output", type=Path, default=ROOT / "data/bot2/speedrun-replay.json")
    args = parser.parse_args()
    battles = load_battles()
    boards = []
    for battle in battles:
        if battle["rules"].get("modifier") not in {None, "SPEEDRUN"}:
            continue
        fleet = full_fleet(battle)
        if fleet is not None:
            boards.append({**{k: v for k, v in battle.items() if k not in {"shots", "decisions"}}, "fleet": fleet})
    speed = [b for b in boards if b["rules"].get("modifier") == "SPEEDRUN"]
    hard = [b for b in boards if b["rules"].get("modifier") is None]
    # Keep the live speedrun controls and spread the historical HARD sample in time.
    selected = speed[-min(len(speed), max(1, args.boards // 2)):]
    need = min(len(hard), args.boards - len(selected))
    selected += [hard[min(len(hard) - 1, i * len(hard) // need)] for i in range(need)]
    seeds = [182001 + i for i in range(args.seeds)]
    jobs = [(b, n, s) for b in selected for s in seeds for n in args.strategies]
    print(f"Fully revealed boards: {len(boards)}; replay {len(selected)} x {len(seeds)} seeds x {len(args.strategies)} strategies", flush=True)
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(replay, jobs):
            rows.append(result)
            if len(rows) % (len(seeds) * len(args.strategies)) == 0:
                print(f"Completed {len(rows)}/{len(jobs)} replays", flush=True)
    paired = {(r["battle_id"], r["seed"], r["name"]): r["shots"] for r in rows}
    base = args.strategies[0]
    summary = {}
    for name in args.strategies:
        group = [r for r in rows if r["name"] == name]
        diffs = [r["shots"] - paired[(r["battle_id"], r["seed"], base)] for r in group]
        # Cluster SE by board: repeated seeds do not make independent boards.
        means = [statistics.mean(r["shots"] - paired[(r["battle_id"], r["seed"], base)]
                                 for r in group if r["battle_id"] == b["battle_id"]) for b in selected]
        summary[name] = {"n": len(group), "mean_shots": statistics.mean(r["shots"] for r in group),
                         "median_shots": statistics.median(r["shots"] for r in group),
                         "delta_vs_baseline": statistics.mean(diffs),
                         "board_cluster_se": statistics.stdev(means) / math.sqrt(len(means)) if len(means) > 1 else None,
                         "better_equal_worse": [sum(d < 0 for d in diffs), sum(d == 0 for d in diffs), sum(d > 0 for d in diffs)],
                         "max_ms": max(r["max_ms"] for r in group),
                         "fallbacks": sum(r["fallbacks"] for r in group)}
    live = [{"battle_id": b["battle_id"], "run_id": b["run_id"], "at": b["at"],
             "strategy": b["strategy"], "team": b["opponent"].get("teamName"),
             "end": b.get("end"), "shots": len(b["shots"]),
             "counted_shots": len(b["shots"]) + b["timeouts"],
             "hits": sum(e["result"] in {"HIT", "SUNK"} for e in b["shots"]),
             "timeouts": b["timeouts"], "rejections": b["rejections"],
             "fallbacks": sum("fallback" in e["reason"] for e in b["decisions"]),
             "mean_ms": statistics.mean(e["elapsed_ms"] for e in b["decisions"]) if b["decisions"] else None,
             "http": b.get("http")} for b in battles if b["rules"].get("modifier") == "SPEEDRUN"]
    report = {"baseline": base, "seeds": seeds, "boards": selected, "rows": rows,
              "summary": summary, "live": live,
              "limitations": ["Only fully revealed fleets; sample is biased toward boards we finished.",
                              "Different live matches have different boards/opponents; live averages are not paired.",
                              "Live counted_shots includes timeouts; SPEEDRUN bonus use is disabled in these runs.",
                              "Full-fleet MC uses hardware-dependent time budgets.",
                              "HARD perimeter/quadrant placement is an observed prior, not a guaranteed rule."]}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
