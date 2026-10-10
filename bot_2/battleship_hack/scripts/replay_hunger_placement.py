"""Replay own placement against opponents' logged hunt shots (no look-ahead).

For each logged match in time order the heat map uses only earlier matches.
Metric: how many of the opponent's first-20 hunt shots (not next to an earlier
hit) land on a fleet. Opponent targeting after a hit is not simulated, so this
measures first-contact exposure, not final hits.
Usage: uv run python scripts/replay_hunger_placement.py [HUNGER_GAMES|ALL] [seeds]
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
import time
from pathlib import Path

from battleship_hack.game import FleetMCHunger, FleetMCHungerHeat, parse_cell
from battleship_hack.heat import HeatStore, hunt_shots

RULES = {"rows": 10, "columns": 10, "touchRule": "NO_TOUCHING_EVEN_DIAGONALLY", "modifier": "HUNGER_GAMES",
         "fleet": [{"length": 4, "count": 1}, {"length": 3, "count": 2}, {"length": 2, "count": 3},
                   {"length": 1, "count": 4}],
         "moveTimeoutSeconds": 1, "placementWindowSeconds": 10, "shotsPerTurn": 1,
         "shotLimitPerPlayer": 20, "turnAfterHitRule": "SHOOTER_KEEPS_TURN"}


def battles(logs: Path):
    for path in sorted(logs.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        current = {}
        for line in path.open():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind, payload, battle = record["type"], record["payload"], record.get("battle_id")
            if kind == "BATTLE_STARTED":
                current[battle] = {"team": payload["opponent"].get("teamName"),
                                   "modifier": payload["rules"].get("modifier"), "shots": [], "ships": None}
            elif battle in current and kind == "OPPONENT_SHOT":
                current[battle]["shots"].append((parse_cell(payload["cell"]), payload.get("result")))
            elif battle in current and kind == "PLACE":
                current[battle]["ships"] = {parse_cell(c) for s in payload["ships"] for c in s["cells"]}
        yield from current.values()


def cells(ships):
    return {parse_cell(c) for s in ships for c in s["cells"]}


def main():
    scope = sys.argv[1] if len(sys.argv) > 1 else "HUNGER_GAMES"
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    store = HeatStore()
    exposure = collections.defaultdict(list)
    per_team = collections.defaultdict(lambda: collections.defaultdict(list))
    timings = []
    for n, battle in enumerate(battles(Path("data/bot2/logs"))):
        team, shots = battle["team"], battle["shots"]
        if team and len(shots) >= store.min_shots and (scope == "ALL" or battle["modifier"] == scope):
            hunt = hunt_shots(shots, store.window)
            heat = store.heat_for(team)
            for seed in range(seeds):
                v1 = FleetMCHunger(RULES, 1000 * n + seed)
                v1.opponent_team = team
                fleets = {"hunger": cells(v1.place_fleet())}
                for name, explore in (("heat", FleetMCHungerHeat.explore), ("heat-no-explore", 0.0)):
                    v2 = FleetMCHungerHeat(RULES, 1000 * n + seed)
                    v2.opponent_team, v2.heat, v2.explore = team, heat, explore
                    started = time.monotonic()
                    fleets[name] = cells(v2.place_fleet())
                    timings.append(time.monotonic() - started)
                fallback = FleetMCHungerHeat(RULES, 1000 * n + seed)
                fallback.opponent_team = team
                fleets["no-map rule"] = cells(fallback.place_fleet())
                if battle["ships"]:
                    fleets["actual"] = battle["ships"]
                for name, fleet in fleets.items():
                    value = sum(cell in fleet for cell in hunt)
                    exposure[name].append(value)
                    per_team[team][name].append(value)
        store.add(team, shots, battle["modifier"])
    names = ["actual", "hunger", "no-map rule", "heat", "heat-no-explore"]
    print(f"scope={scope}; matches x seeds={len(exposure['hunger'])}; "
          f"placement p95={sorted(timings)[int(len(timings) * 0.95)] * 1000:.0f} ms" if timings else "no data")
    print("hunt shots landing on our fleet (lower is better)")
    for name in names:
        if exposure[name]:
            print(f"  {name:16} {statistics.mean(exposure[name]):5.2f}")
    print(f"  {'team':22} {'n':>3} " + " ".join(f"{n[:12]:>12}" for n in names))
    for team, rows in sorted(per_team.items(), key=lambda kv: -len(kv[1]["hunger"])):
        print(f"  {team[:22]:22} {len(rows['hunger']) // seeds:3} "
              + " ".join(f"{statistics.mean(rows[n]):12.2f}" if rows[n] else f"{'-':>12}" for n in names))


if __name__ == "__main__":
    main()
