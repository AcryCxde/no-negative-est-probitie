"""Profile known top opponents using our own logs and public API snapshot.

All numerical summaries use a temporary in-memory DuckDB, never the live DB.
Read-only with respect to the client, configuration, and primary match logs.
Usage: .venv/bin/python scripts/profile_top_opponents.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import duckdb

from battleship_hack.game import neighbors, parse_cell
from battleship_hack.heat import hunt_shots

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/bot2"


def read_battles(cutoff: str) -> tuple[list[dict], dict]:
    grouped = defaultdict(list)
    seen = set()
    malformed = 0
    kinds = {"BATTLE_STARTED", "BATTLE_FINISHED", "PLACE", "SHOT_RESOLVED",
             "OPPONENT_SHOT", "HTTP_MATCH_LINKED", "USE_BONUS", "OPPONENT_BONUS_USED",
             "TURN_TIMED_OUT", "OPPONENT_TURN_TIMED_OUT", "SHOT_REJECTED", "DECISION"}
    for path in (DATA / "logs").glob("*.jsonl"):
        with path.open() as lines:
            for line in lines:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if event["timestamp"] > cutoff or event["event_id"] in seen:
                    continue
                seen.add(event["event_id"])
                if event["type"] in kinds and event.get("battle_id"):
                    grouped[event["battle_id"]].append(event)
    battles = []
    for battle_id, events in grouped.items():
        events.sort(key=lambda e: (e["timestamp"], e["sequence"]))
        start = next((e for e in events if e["type"] == "BATTLE_STARTED"), None)
        if not start or start["payload"]["opponent"].get("kind") != "REMOTE_BOT":
            continue
        end = next((e for e in reversed(events) if e["type"] == "BATTLE_FINISHED"), None)
        p = start["payload"]
        placement = next((e["payload"]["ships"] for e in events if e["type"] == "PLACE"), [])
        battle = {"battle_id": battle_id, "run_id": start["run_id"],
                  "started": start["timestamp"], "opponent_id": p["opponent"].get("botId"),
                  "team": p["opponent"].get("teamName"), "strategy": p["strategy"],
                  "modifier": p["rules"].get("modifier"), "rules": p["rules"],
                  "shot_limit": p["rules"].get("shotLimitPerPlayer"),
                  "outcome": end["payload"]["outcome"] if end else None,
                  "end_reason": end["payload"].get("endReason") if end else None,
                  "our_ships": [[parse_cell(c) for c in s["cells"]] for s in placement],
                  "our_shots": [], "opponent_shots": [], "our_bonuses": [],
                  "opponent_bonuses": [], "timeouts": 0, "opponent_timeouts": 0,
                  "rejections": 0, "rating_change": None, "shield_followups": []}
        for e in events:
            kind, payload = e["type"], e["payload"]
            if kind in {"SHOT_RESOLVED", "OPPONENT_SHOT"} and payload.get("cell"):
                key = "our_shots" if kind == "SHOT_RESOLVED" else "opponent_shots"
                battle[key].append([parse_cell(payload["cell"]), payload.get("result")])
            elif kind == "USE_BONUS":
                battle["our_bonuses"].append(payload["bonus"])
            elif kind == "OPPONENT_BONUS_USED":
                battle["opponent_bonuses"].append(payload["bonus"])
            elif kind == "HTTP_MATCH_LINKED":
                battle["rating_change"] = payload.get("ratingChange")
            elif kind == "TURN_TIMED_OUT":
                battle["timeouts"] += 1
            elif kind == "OPPONENT_TURN_TIMED_OUT":
                battle["opponent_timeouts"] += 1
            elif kind == "SHOT_REJECTED":
                battle["rejections"] += 1
        for key in ("our_shots", "opponent_shots"):
            battle[key] = [(tuple(c), r) for c, r in battle[key]]
        battles.append(battle)
    battles.sort(key=lambda b: b["started"])
    return battles, {"cutoff": cutoff, "distinct_events": len(seen), "malformed_lines": malformed}


def ring(cell: tuple[int, int], rows: int, columns: int) -> int:
    r, c = cell
    return min(r, rows - 1 - r, c, columns - 1 - c)


def sunk_ships(shots: list) -> list[set]:
    hits, ships = set(), []
    for cell, result in shots:
        if result in {"HIT", "SUNK"}:
            hits.add(cell)
        if result == "SUNK":
            ship, todo = {cell}, [cell]
            while todo:
                for n in (neighbors(todo.pop()) & hits) - ship:
                    ship.add(n)
                    todo.append(n)
            ships.append(ship)
            hits -= ship
    return ships


def main() -> None:
    snapshot = json.loads((DATA / "leaderboard-snapshot.json").read_text())
    battles, diagnostics = read_battles(snapshot["fetched_at"])
    conn = duckdb.connect()
    conn.execute("CREATE TABLE battles (battle_id VARCHAR, run_id VARCHAR, opponent_id VARCHAR, "
                 "team VARCHAR, started VARCHAR, strategy VARCHAR, modifier VARCHAR, shot_limit INT, "
                 "outcome VARCHAR, end_reason VARCHAR, our_shots INT, opponent_shots INT, "
                 "our_hits INT, opponent_hits INT, our_shield INT, defended INT, immediate_retry INT, "
                 "timeouts INT, opponent_timeouts INT, rejections INT, rating_change DOUBLE)")
    conn.execute("CREATE TABLE hunt (battle_id VARCHAR, opponent_id VARCHAR, team VARCHAR, "
                 "modifier VARCHAR, started VARCHAR, row INT, col INT, ring INT, shot_window INT)")
    conn.execute("CREATE TABLE revealed (battle_id VARCHAR, opponent_id VARCHAR, team VARCHAR, "
                 "modifier VARCHAR, started VARCHAR, length INT, row INT, col INT, ring INT)")
    rows, hunt_rows, revealed_rows = [], [], []
    for b in battles:
        opp = b["opponent_shots"]
        defended = [i for i, (_, result) in enumerate(opp) if result == "DEFENDED"]
        rows.append((b["battle_id"], b["run_id"], b["opponent_id"], b["team"], b["started"],
                     b["strategy"], b["modifier"], b["shot_limit"], b["outcome"], b["end_reason"],
                     len(b["our_shots"]), len(opp),
                     sum(r in {"HIT", "SUNK"} for _, r in b["our_shots"]),
                     sum(r in {"HIT", "SUNK"} for _, r in opp), b["our_bonuses"].count("SHIELD"),
                     len(defended), sum(i + 1 < len(opp) and opp[i + 1][0] == opp[i][0]
                                        for i in defended), b["timeouts"], b["opponent_timeouts"],
                     b["rejections"], b["rating_change"]))
        r, c = b["rules"]["rows"], b["rules"]["columns"]
        for window in (10, 20):
            for cell in hunt_shots(opp, window):
                hunt_rows.append((b["battle_id"], b["opponent_id"], b["team"], b["modifier"],
                                  b["started"], cell[0], cell[1], ring(cell, r, c), window))
        ships = sunk_ships(b["our_shots"])
        if b["modifier"] == "SCHRODINGER_SHIP":
            hit_cells = {cell for cell, result in b["our_shots"] if result == "HIT"}
            total = sum(f["length"] * f["count"] for f in b["rules"]["fleet"])
            if len(hit_cells) == total:
                ships = []
                while hit_cells:
                    ship, todo = set(), [hit_cells.pop()]
                    while todo:
                        cell = todo.pop()
                        ship.add(cell)
                        more = neighbors(cell) & hit_cells
                        hit_cells -= more
                        todo.extend(more)
                    ships.append(ship)
        for ship in ships:
            for cell in ship:
                revealed_rows.append((b["battle_id"], b["opponent_id"], b["team"], b["modifier"],
                                      b["started"], len(ship), cell[0], cell[1], ring(cell, r, c)))
    conn.executemany("INSERT INTO battles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO hunt VALUES (?,?,?,?,?,?,?,?,?)", hunt_rows)
    conn.executemany("INSERT INTO revealed VALUES (?,?,?,?,?,?,?,?,?)", revealed_rows)

    def query(sql: str) -> list[dict]:
        result = conn.execute(sql)
        cols = [col[0] for col in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]

    summary = """count(*) n, count_if(outcome='WIN') wins, count_if(outcome='DRAW') draws,
        count_if(outcome='LOSS') losses, avg(our_hits) our_hits, avg(opponent_hits) opponent_hits,
        avg(our_hits-opponent_hits) hit_margin, sum(timeouts) timeouts,
        sum(opponent_timeouts) opponent_timeouts, sum(our_shield) shields,
        sum(defended) shields_triggered, sum(immediate_retry) shield_immediate_retries,
        count(rating_change) elo_linked, sum(rating_change) elo_change"""
    completed = "outcome IS NOT NULL"
    hunger = completed + " AND modifier='HUNGER_GAMES' AND shot_limit=20"
    v2 = hunger + " AND strategy='fleet-mc-hunger-v2'"
    output = {"diagnostics": diagnostics, "battles_including_unfinished": len(battles),
              "all_by_team_modifier": query(f"SELECT team, modifier, {summary} FROM battles "
                                            f"WHERE {completed} GROUP BY ALL ORDER BY team, modifier"),
              "hunger_by_team_strategy": query(f"SELECT team, strategy, {summary} FROM battles "
                                               f"WHERE {hunger} GROUP BY ALL ORDER BY team, strategy"),
              "hunger_v2_by_bot": query(f"SELECT opponent_id, team, {summary} FROM battles "
                                         f"WHERE {v2} GROUP BY ALL ORDER BY n DESC"),
              "hunger_v2_runs": query(f"SELECT run_id, min(started) started, {summary} FROM battles "
                                       f"WHERE {v2} GROUP BY run_id ORDER BY started"),
              "hunger_v2_total": query(f"SELECT {summary} FROM battles WHERE {v2}"),
              "hunt_rings": query("SELECT opponent_id, team, modifier, shot_window, ring, count(*) shots, "
                                  "count(DISTINCT battle_id) matches FROM hunt GROUP BY ALL "
                                  "ORDER BY opponent_id, modifier, shot_window, ring"),
              "hunt_recent": query("WITH recent AS (SELECT *, row_number() OVER "
                                   "(PARTITION BY opponent_id, modifier ORDER BY started DESC) rn "
                                   "FROM battles WHERE outcome IS NOT NULL) "
                                   "SELECT h.opponent_id, h.team, h.modifier, h.ring, count(*) shots, "
                                   "count(DISTINCT h.battle_id) matches FROM hunt h "
                                   "JOIN recent r USING(battle_id) WHERE r.rn<=10 AND h.shot_window=10 "
                                   "GROUP BY ALL ORDER BY h.opponent_id, h.modifier, h.ring"),
              "placement_rings": query("SELECT opponent_id, team, modifier, length, ring, "
                                       "count(*) cells, count(DISTINCT battle_id) matches "
                                       "FROM revealed GROUP BY ALL "
                                       "ORDER BY opponent_id, modifier, length, ring")}
    (DATA / "top-opponents-profile.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    (DATA / "top-opponents-battles.json").write_text(json.dumps(battles, ensure_ascii=False) + "\n")
    print(json.dumps({"diagnostics": diagnostics, "v2_total": output["hunger_v2_total"],
                      "v2_by_bot": output["hunger_v2_by_bot"],
                      "hunger_by_team_strategy": output["hunger_by_team_strategy"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
