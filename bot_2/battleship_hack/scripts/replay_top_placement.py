"""Chronological offline comparison of v2 placement with a bot+mode heat map.

Only target IDs currently in the public top ten are eligible for a new map.
No runtime policy changes, server matches, or simulated opponent targeting.
The metric is first-contact exposure to recorded hunt shots, not final hits.
Usage: .venv/bin/python scripts/replay_top_placement.py --tail 60 --seeds 2
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb

from battleship_hack.game import FleetMCHungerV2, parse_cell, validate_placement
from battleship_hack.heat import HeatStore, hunt_shots

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/bot2"


def cells(ships: list[dict]) -> set[tuple[int, int]]:
    return {parse_cell(c) for ship in ships for c in ship["cells"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tail", type=int, default=60)
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--policy", choices=("bot-mode", "safe-explore"), default="bot-mode")
    args = parser.parse_args()
    snapshot = json.loads((DATA / "leaderboard-snapshot.json").read_text())
    battles = json.loads((DATA / "top-opponents-battles.json").read_text())
    target_ids = {r["botId"] for r in snapshot["ranking"] if r["place"] <= 10}
    selected = {b["battle_id"] for b in [b for b in battles if b["outcome"] is not None
                and b["opponent_id"] in target_ids and b["modifier"] == "HUNGER_GAMES"
                and b["shot_limit"] == 20 and len(b["opponent_shots"]) >= 10][-args.tail:]}
    team_store, bot_store = HeatStore(), HeatStore()
    measurements, timings = [], []
    for index, b in enumerate(battles):
        shots = [(tuple(c), r) for c, r in b["opponent_shots"]]
        opponent_id, team = b["opponent_id"], b["team"]
        same_mode = b["modifier"] == "HUNGER_GAMES" and b["shot_limit"] == 20
        eligible = opponent_id in target_ids and bot_store.matches_for(opponent_id) >= 5
        if b["battle_id"] in selected:
            hunt = hunt_shots(shots, 20)
            for seed in range(args.seeds):
                fixed_seed = 970000 + 100 * index + seed
                baseline = FleetMCHungerV2(b["rules"], fixed_seed)
                baseline.opponent_team = team
                baseline.heat = team_store.heat_for(team)
                rng_state = baseline.rng.getstate()
                exploring = baseline.heat is not None and baseline.rng.random() < baseline.explore
                baseline.rng.setstate(rng_state)
                started = time.monotonic()
                baseline_ships = baseline.place_fleet()
                baseline_ms = 1000 * (time.monotonic() - started)
                validate_placement(b["rules"], baseline_ships)
                base_cells = cells(baseline_ships)
                change_map = args.policy == "bot-mode" and eligible
                change_explore = args.policy == "safe-explore" and opponent_id in target_ids and exploring
                if change_map or change_explore:
                    candidate = FleetMCHungerV2(b["rules"], fixed_seed)
                    candidate.opponent_team = team
                    candidate.heat = (bot_store.heat_for(opponent_id) if change_map else baseline.heat)
                    if change_explore:
                        # Diversify over the least-exposed 20% of sampled fleets,
                        # rather than over all uniformly generated fleets.
                        candidate.explore = 0.0
                        candidate.top_share = 0.2
                    started = time.monotonic()
                    candidate_ships = candidate.place_fleet()
                    candidate_ms = 1000 * (time.monotonic() - started)
                    validate_placement(b["rules"], candidate_ships)
                    candidate_cells = cells(candidate_ships)
                else:
                    candidate_cells, candidate_ms = base_cells, 0.0
                measurements.append({"battle_id": b["battle_id"], "opponent_id": opponent_id,
                    "team": team, "strategy_in_log": b["strategy"], "seed": fixed_seed,
                    "eligible": eligible if args.policy == "bot-mode" else change_explore,
                    "explore_branch": exploring,
                    "prior_bot_mode_matches": bot_store.matches_for(opponent_id),
                    "baseline": sum(c in base_cells for c in hunt),
                    "candidate": sum(c in candidate_cells for c in hunt),
                    "actual": sum(c in {tuple(c) for ship in b["our_ships"] for c in ship} for c in hunt)})
                timings.extend((baseline_ms, candidate_ms))
            if len(measurements) % (args.seeds * 10) == 0:
                print(f"Replayed {len(measurements) // args.seeds}/{len(selected)} matches", flush=True)
        team_store.add(team, shots, b["modifier"])
        if same_mode:
            bot_store.add(opponent_id, shots, b["modifier"])
    # Average seeds within a match before computing uncertainty: the match is
    # the observational unit, not a different random layout for the same game.
    conn = duckdb.connect()
    conn.execute("CREATE TABLE replay (battle_id VARCHAR, opponent_id VARCHAR, team VARCHAR, "
                 "strategy VARCHAR, eligible BOOLEAN, baseline DOUBLE, candidate DOUBLE, actual DOUBLE)")
    conn.executemany("INSERT INTO replay VALUES (?,?,?,?,?,?,?,?)", [
        (r["battle_id"], r["opponent_id"], r["team"], r["strategy_in_log"], r["eligible"],
         r["baseline"], r["candidate"], r["actual"]) for r in measurements])
    conn.execute("CREATE VIEW per_match AS SELECT battle_id, opponent_id, team, strategy, bool_or(eligible) eligible, "
                 "avg(baseline) baseline, avg(candidate) candidate, avg(actual) actual "
                 "FROM replay GROUP BY battle_id, opponent_id, team, strategy")

    def summary(group: str = "", where: str = "") -> list[dict]:
        grouping = f"{group}, " if group else ""
        query = f"SELECT {grouping} count(*) n, avg(baseline) baseline, avg(candidate) candidate, "
        query += "avg(candidate-baseline) delta, stddev_samp(candidate-baseline)/sqrt(count(*)) se, "
        query += "count_if(candidate<baseline) better, count_if(candidate>baseline) worse, "
        query += "count_if(candidate=baseline) equal FROM per_match " + where
        if group:
            query += " GROUP BY ALL ORDER BY n DESC"
        result = conn.execute(query)
        cols = [r[0] for r in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]

    output = {"snapshot_cutoff": snapshot["fetched_at"], "seeds_per_match": args.seeds,
        "tail_matches": args.tail, "policy": args.policy,
        "eligibility": ("top10 bot ID, HUNGER_GAMES/20, >=5 prior same-mode matches" if args.policy == "bot-mode"
                        else "top10 bot ID, HUNGER_GAMES/20, existing map and exploration branch"),
        "metric": "Recorded early hunt shots hitting a replacement fleet; lower is better. "
                  "Counterfactual targeting, adaptation, outcome, and Elo are not modeled.",
        "total": summary(), "eligible_only": summary(where="WHERE eligible"),
        "by_bot": summary("opponent_id, team"), "by_strategy": summary("strategy"),
        "placement_p95_ms": sorted(timings)[min(len(timings)-1, int(.95 * len(timings)))],
        "raw": measurements}
    output_path = DATA / ("top-placement-replay.json" if args.policy == "bot-mode"
                          else "top-safe-explore-replay.json")
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in output.items() if k != "raw"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
