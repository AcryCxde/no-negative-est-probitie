"""Replay last N ranking boards: complete partial fleets, compare shot counts offline."""
from __future__ import annotations

import json
import random
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import duckdb

from battleship_hack.game import STRATEGIES, cell_json, neighbors

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/bot2/analytics.duckdb"
DEFAULT_STRATEGIES = ("fleet-mc", "fleet-mc-duel", "fleet-mc-duel-v2")
SEEDS = (71001, 71002, 71003)
COMPLETIONS = 4


def reconstruct_ships(shots: list[dict]) -> tuple[list[set[tuple[int, int]]], set[tuple[int, int]], set[tuple[int, int]]]:
    hits: set[tuple[int, int]] = set()
    misses: set[tuple[int, int]] = set()
    ships: list[set[tuple[int, int]]] = []
    for event in shots:
        cell = (event["cell"]["row"], event["cell"]["column"])
        result = event["result"]
        if result == "MISS":
            misses.add(cell)
            continue
        if result not in {"HIT", "SUNK"}:
            continue
        hits.add(cell)
        if result != "SUNK":
            continue
        cluster, frontier = {cell}, [cell]
        while frontier:
            for neighbor in neighbors(frontier.pop()) & hits - cluster:
                cluster.add(neighbor)
                frontier.append(neighbor)
        ships.append(cluster)
        hits -= cluster
    return ships, hits, misses


def load_last_ranking(limit: int = 15) -> list[dict]:
    """Prefer a temp DuckDB copy; fall back to JSONL if the live file is locked."""
    import shutil
    import tempfile

    if DB.is_file():
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "analytics.duckdb"
            try:
                shutil.copy2(DB, copy)
                return _load_from_duckdb(copy, limit)
            except Exception as exc:
                print(f"DuckDB copy failed ({exc}); reading JSONL logs", flush=True)
    return _load_from_jsonl(limit)


def _load_from_duckdb(path: Path, limit: int) -> list[dict]:
    con = duckdb.connect(str(path), read_only=True)
    rows = con.execute(
        """
        SELECT battle_id, CAST(started_at AS VARCHAR), outcome, actual_strategy,
               CAST(opponent AS VARCHAR), CAST(rules AS VARCHAR), match_id
        FROM battles
        WHERE run_id IN (SELECT run_id FROM runs WHERE CAST(config AS VARCHAR) LIKE '%RANKING%')
        ORDER BY started_at DESC
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    battles = []
    for battle_id, started, outcome, strategy, opponent, rules, match_id in rows:
        shots = [
            json.loads(payload)
            for (payload,) in con.execute(
                """
                SELECT CAST(payload AS VARCHAR) FROM events
                WHERE battle_id=? AND type='SHOT_RESOLVED' ORDER BY sequence
                """,
                [battle_id],
            ).fetchall()
        ]
        ships, leftover, misses = reconstruct_ships(shots)
        battles.append(
            {
                "battle_id": battle_id,
                "started_at": started,
                "outcome": outcome,
                "strategy": strategy,
                "opponent": json.loads(opponent or "{}"),
                "rules": json.loads(rules),
                "match_id": match_id,
                "revealed_ships": [sorted(ship) for ship in ships],
                "leftover_hits": sorted(leftover),
                "misses": sorted(misses),
            }
        )
    con.close()
    return battles


def _load_from_jsonl(limit: int) -> list[dict]:
    logs = sorted((ROOT / "data/bot2/logs").glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    selected: list[dict] = []
    for path in logs:
        text = path.read_text(errors="ignore")
        if '"mode": "RANKING"' not in text and '"mode":"RANKING"' not in text:
            continue
        by_id: dict[str, dict] = {}
        order: list[str] = []
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload") or {}
            battle_id = event.get("battle_id")
            if event.get("direction") == "local" and event.get("type") == "BATTLE_STARTED" and battle_id:
                by_id[battle_id] = {
                    "battle_id": battle_id,
                    "started_at": event.get("timestamp"),
                    "outcome": None,
                    "strategy": payload.get("strategy"),
                    "opponent": payload.get("opponent") or {},
                    "rules": payload.get("rules") or {},
                    "match_id": None,
                    "shots": [],
                }
                order.append(battle_id)
            elif battle_id in by_id and event.get("type") == "SHOT_RESOLVED" and event.get("direction") == "in":
                by_id[battle_id]["shots"].append(payload)
            elif battle_id in by_id and event.get("type") == "BATTLE_FINISHED":
                by_id[battle_id]["outcome"] = payload.get("outcome")
            elif battle_id in by_id and event.get("type") == "HTTP_MATCH_LINKED":
                by_id[battle_id]["match_id"] = payload.get("matchId")
        for battle_id in reversed(order):
            item = by_id[battle_id]
            if not item["shots"] or not (item["opponent"].get("teamName") or item["opponent"].get("kind") == "REMOTE_BOT"):
                continue
            ships, leftover, misses = reconstruct_ships(item["shots"])
            selected.append(
                {
                    "battle_id": battle_id,
                    "started_at": item["started_at"],
                    "outcome": item["outcome"],
                    "strategy": item["strategy"],
                    "opponent": item["opponent"],
                    "rules": item["rules"],
                    "match_id": item["match_id"],
                    "revealed_ships": [sorted(ship) for ship in ships],
                    "leftover_hits": sorted(leftover),
                    "misses": sorted(misses),
                }
            )
            if len(selected) >= limit:
                return selected
    raise RuntimeError(f"Only found {len(selected)} ranking battles in JSONL")


def complete_board(battle: dict, rng: random.Random) -> list[set[tuple[int, int]]] | None:
    rules = battle["rules"]
    fleet = Counter({entry["length"]: entry["count"] for entry in rules["fleet"]})
    ships = [set(map(tuple, ship)) for ship in battle["revealed_ships"]]
    leftover = set(map(tuple, battle["leftover_hits"]))
    misses = set(map(tuple, battle["misses"]))
    for length in (len(ship) for ship in ships):
        fleet[length] -= 1
    if leftover:
        # Incomplete hit cluster: treat as one unfinished ship of unknown length.
        need = [length for length, count in sorted(fleet.items(), reverse=True) if count > 0 and length >= len(leftover)]
        if not need:
            return None
        length = need[0]
        fleet[length] -= 1
        board = {(r, c) for r in range(rules["rows"]) for c in range(rules["columns"])}
        blocked = set().union(*ships) | misses
        blocked |= set().union(*(neighbors(cell, True) for ship in ships for cell in ship)) - set().union(*ships)
        extensions = []
        for r, c in sorted(board - blocked | leftover):
            for dr, dc in ((0, 1), (1, 0)):
                cells = {(r + dr * i, c + dc * i) for i in range(length)}
                if leftover <= cells <= board and cells.isdisjoint(blocked - leftover):
                    if not any(neighbors(cell, True) & (blocked - leftover) for cell in cells - leftover):
                        extensions.append(cells)
        if not extensions:
            return None
        ships.append(rng.choice(extensions))
    remaining = sorted((length for length, count in fleet.items() for _ in range(count)), reverse=True)
    if not remaining:
        return ships
    board = {(r, c) for r in range(rules["rows"]) for c in range(rules["columns"])}
    occupied = set().union(*ships)
    blocked = occupied | misses | set().union(*(neighbors(cell, True) for cell in occupied)) - occupied
    options = {}
    for length in set(remaining):
        found = []
        for r, c in sorted(board):
            for dr, dc in ((0, 1), (1, 0)):
                cells = {(r + dr * i, c + dc * i) for i in range(length)}
                if cells <= board:
                    found.append(cells)
        options[length] = found
        rng.shuffle(options[length])

    def search(index: int, blocked_cells: set) -> list | None:
        if index == len(remaining):
            return []
        for cells in options[remaining[index]]:
            if blocked_cells.isdisjoint(cells):
                halo = set(cells).union(*(neighbors(cell, True) for cell in cells))
                tail = search(index + 1, blocked_cells | halo)
                if tail is not None:
                    return [cells] + tail
        return None

    for _ in range(80):
        placed = search(0, set(blocked))
        if placed is not None:
            return ships + placed
        for length in options:
            rng.shuffle(options[length])
    return None


def play(cls, rules: dict, ships: list[set], seed: int) -> dict:
    model = cls(
        {**rules, "modifier": "DUEL", "turnAfterHitRule": "HIT_SPENDS_SHOT", "shotsPerTurn": 1},
        seed,
    )
    occupied = set().union(*ships)
    shots = times = fallbacks = special = 0
    while +model.remaining:
        started = time.perf_counter()
        cell, reason = model.choose()
        times += (time.perf_counter() - started) * 1000
        assert cell not in model.used | model.empty
        fallbacks += "fallback" in reason
        special += "two-shot" in reason
        if cell in occupied:
            ship = next(entry for entry in ships if cell in entry)
            result = "SUNK" if ship <= model.used | {cell} else "HIT"
        else:
            result = "MISS"
        model.observe({"cell": cell_json(cell), "result": result})
        shots += 1
        if shots > 100:
            raise RuntimeError("shot budget exceeded")
    return {
        "shots": shots,
        "mean_ms": times / shots,
        "fallbacks": fallbacks,
        "special": special,
    }


def main() -> None:
    """Usage: sim_last_ranking.py [strategy ...]; the last name is the candidate."""
    names = sys.argv[1:] or list(DEFAULT_STRATEGIES)
    strategies = {name: STRATEGIES[name] for name in names}
    candidate, baselines = names[-1], names[:-1]
    out = ROOT / f"data/bot2/{candidate}-sim-last15.json"
    battles = load_last_ranking(15)
    rows = []
    completions_used = 0
    for battle in battles:
        name = battle["opponent"].get("teamName") or "?"
        revealed_cells = sum(len(ship) for ship in battle["revealed_ships"])
        full = revealed_cells == 20 and not battle["leftover_hits"]
        board_rows = []
        for completion_seed in range(COMPLETIONS if not full else 1):
            rng = random.Random(hash((battle["battle_id"], completion_seed)) & 0xFFFFFFFF)
            ships = (
                [set(map(tuple, ship)) for ship in battle["revealed_ships"]]
                if full
                else complete_board(battle, rng)
            )
            if ships is None or sum(len(ship) for ship in ships) != 20:
                continue
            completions_used += not full
            for seed in SEEDS:
                result = {"completion": completion_seed, "seed": seed, "strategies": {}}
                for label, cls in strategies.items():
                    result["strategies"][label] = play(cls, battle["rules"], ships, seed)
                board_rows.append(result)
        rows.append(
            {
                "battle_id": battle["battle_id"],
                "match_id": battle["match_id"],
                "started_at": battle["started_at"],
                "outcome": battle["outcome"],
                "opponent": name,
                "modifier": battle["rules"].get("modifier"),
                "historical_strategy": battle["strategy"],
                "full_board": full,
                "revealed_cells": revealed_cells,
                "pairs": board_rows,
            }
        )
        print(
            f"{battle['outcome']:4} {name:20} full={full} pairs={len(board_rows)}",
            flush=True,
        )

    flat = [item for row in rows for item in row["pairs"]]
    summary = {}
    for label in strategies:
        shots = [item["strategies"][label]["shots"] for item in flat]
        times = [item["strategies"][label]["mean_ms"] for item in flat]
        summary[label] = {
            "pairs": len(shots),
            "mean_shots": statistics.mean(shots) if shots else None,
            "median_shots": statistics.median(shots) if shots else None,
            "mean_ms": statistics.mean(times) if times else None,
            "fallbacks": sum(item["strategies"][label]["fallbacks"] for item in flat),
            "special_decisions": sum(item["strategies"][label]["special"] for item in flat),
        }
        if label != candidate:
            ours = [item["strategies"][candidate]["shots"] for item in flat]
            summary[label]["candidate_wins_losses_ties"] = [
                sum(a < b for a, b in zip(ours, shots)),
                sum(a > b for a, b in zip(ours, shots)),
                sum(a == b for a, b in zip(ours, shots)),
            ]
    means = {label: summary[label]["mean_shots"] for label in names}
    go = bool(
        means[candidate] is not None
        and all(means[label] is not None for label in baselines)
        and (not baselines or means[candidate] <= min(means[label] for label in baselines) - 0.5)
    )
    report = {
        "battles": len(rows),
        "pairs": len(flat),
        "partial_completions_used": completions_used,
        "seeds": list(SEEDS),
        "candidate": candidate,
        "summary": summary,
        "mean_shots_delta_vs": {
            label: means[candidate] - means[label] for label in baselines if means[candidate] and means[label]
        },
        "recommend_ranking": go,
        "decision_rule": (
            f"Go to ranking if {candidate} mean shots is at least 0.5 below every baseline "
            f"({', '.join(baselines)}) on completed last-15 boards."
        ),
        "limitations": [
            "Loss boards are completed by sampling remaining ships consistent with misses/halos; not ground truth.",
            "Offline shot count is not live win rate; opponent shoot/placement skill is not simulated.",
            "Sampling deadlines depend on hardware.",
        ],
        "rows": rows,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    printable = {key: value for key, value in report.items() if key != "rows"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
