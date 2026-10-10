"""Read-only profile of «Низкие ожидания» bots from our local ranking logs.

Attack: their OPPONENT_SHOT stream against our known placement.
Placement: ships we fully sank (SUNK clusters) on their board.
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "data/bot2/logs"
TEAM = "Низкие ожидания"
NAMES = {
    "4375650f-0085-471c-b1fb-cef3e2750066": "Donatello",
    "10ef34fa-dc01-47ba-9c61-3de57660e4fd": "Raphael",
    "993a9f36-2711-49da-91c0-37e740851c61": "Michelangelo",
}
FLEET = (4, 3, 3, 2, 2, 2, 1, 1, 1, 1)


def cell(c: dict) -> tuple[int, int]:
    return c["row"], c["column"]


def ring(r: int, c: int) -> int:
    return min(r, c, 9 - r, 9 - c)


def around(cells) -> set[tuple[int, int]]:
    return {(r + dr, c + dc) for r, c in cells for dr in (-1, 0, 1) for dc in (-1, 0, 1)
            if 0 <= r + dr < 10 and 0 <= c + dc < 10}


def orth(p):
    r, c = p
    return {(r + dr, c + dc) for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)) if 0 <= r + dr < 10 and 0 <= c + dc < 10}


def clusters_from_shots(shots):
    """Sunk ships from a shot stream with HIT/SUNK results."""
    hits, ships = set(), []
    for p, res in shots:
        if res in ("HIT", "SUNK"):
            hits.add(p)
        if res == "SUNK":
            cl, todo = {p}, [p]
            while todo:
                for n in orth(todo.pop()) & hits - cl:
                    cl.add(n)
                    todo.append(n)
            ships.append(frozenset(cl))
            hits -= cl
    return ships


def load():
    battles = []
    for f in glob.glob(str(LOGS / "*.jsonl")):
        events = [json.loads(line) for line in open(f)]
        by = defaultdict(list)
        for e in events:
            if e.get("battle_id"):
                by[e["battle_id"]].append(e)
        for bid, evs in by.items():
            start = next((e for e in evs if e["type"] == "BATTLE_STARTED"), None)
            if not start or (start["payload"].get("opponent") or {}).get("teamName") != TEAM:
                continue
            fin = next((e for e in evs if e["type"] == "BATTLE_FINISHED"), None)
            place = next((e for e in evs if e["type"] == "PLACE"), None)
            ours = [frozenset(cell(c) for c in s["cells"]) for s in place["payload"]["ships"]] if place else []
            battles.append(dict(
                started=start["timestamp"],
                bot=NAMES.get(start["payload"]["opponent"]["botId"], "?"),
                modifier=start["payload"]["rules"].get("modifier"),
                rules=start["payload"]["rules"],
                outcome=fin["payload"]["outcome"] if fin else None,
                our_ships=ours,
                their_shots=[(cell(e["payload"]["cell"]), e["payload"]["result"]) for e in evs
                             if e["type"] == "OPPONENT_SHOT" and e["payload"].get("cell")],
                our_shots=[(cell(e["payload"]["cell"]), e["payload"]["result"]) for e in evs
                           if e["type"] == "SHOT_RESOLVED" and e["payload"].get("cell")],
                their_bonus=[e["payload"] for e in evs if e["type"] == "OPPONENT_BONUS_USED"],
            ))
    return sorted(battles, key=lambda b: b["started"])


def random_fleet(rng):
    while True:
        occ, blocked, ships = set(), set(), []
        ok = True
        for n in FLEET:
            for _ in range(200):
                h = rng.random() < 0.5
                r, c = rng.randrange(10 - (0 if h else n - 1)), rng.randrange(10 - (n - 1 if h else 0))
                cells = {(r, c + i) if h else (r + i, c) for i in range(n)}
                if not cells & blocked:
                    occ |= cells
                    blocked |= around(cells)
                    ships.append(frozenset(cells))
                    break
            else:
                ok = False
                break
        if ok:
            return ships


def placement_features(ships):
    f = Counter()
    for s in ships:
        n = len(s)
        f[f"n{n}"] += 1
        f[f"edge{n}"] += any(ring(*p) == 0 for p in s)
        f[f"corner{n}"] += any(p in {(0, 0), (0, 9), (9, 0), (9, 9)} for p in s)
        f[f"ring1_{n}"] += min(ring(*p) for p in s) == 1
        f[f"inner{n}"] += min(ring(*p) for p in s) >= 2
        if n > 1:
            f[f"horiz{n}"] += len({p[0] for p in s}) == 1
    return f


def attack(battles):
    out = {}
    for name in sorted({b["bot"] for b in battles}):
        bs = [b for b in battles if b["bot"] == name]
        stats = Counter()
        first = []
        hunt_cells = Counter()
        miss_to_kill = []
        for b in bs:
            ours = set().union(*b["our_ships"]) if b["our_ships"] else set()
            hits, sunk_halo, diag_ok = set(), set(), set()
            seq = b["their_shots"]
            first.append(tuple(p for p, _ in seq[:5]))
            open_hits = set()
            shipcell = {p: s for s in b["our_ships"] for p in s}
            misses = 0
            for p, res in seq:
                hunting = not open_hits
                stats["shots"] += 1
                stats["hunt" if hunting else "target"] += 1
                if hunting:
                    hunt_cells[p] += 1
                    stats["hunt_ring0"] += ring(*p) == 0
                    stats["hunt_ring1"] += ring(*p) == 1
                    stats["hunt_parity_even"] += (p[0] + p[1]) % 2 == 0
                    stats["hunt_hit"] += res in ("HIT", "SUNK")
                if p in sunk_halo:
                    stats["into_known_empty_halo"] += 1
                if p in diag_ok:
                    stats["into_hit_diagonal"] += 1
                if res == "MISS":
                    misses += 1
                    stats["miss"] += 1
                    if not hunting:
                        stats["target_miss"] += 1
                if res in ("HIT", "SUNK"):
                    stats["hit"] += 1
                    open_hits.add(p)
                    diag_ok |= {(p[0] + a, p[1] + d) for a in (-1, 1) for d in (-1, 1)}
                if res == "SUNK":
                    s = shipcell.get(p, {p})
                    open_hits -= s
                    sunk_halo |= around(s) - s
            if ours and ours <= {p for p, r in seq if r in ("HIT", "SUNK")}:
                miss_to_kill.append(misses)
        repeats = Counter(first)
        out[name] = dict(
            games=len(bs),
            modifiers=dict(Counter(b["modifier"] for b in bs)),
            our_result=dict(Counter(b["outcome"] for b in bs)),
            shots=stats["shots"], hit_rate=round(stats["hit"] / max(1, stats["shots"]), 3),
            hunt_shots=stats["hunt"],
            hunt_hit_rate=round(stats["hunt_hit"] / max(1, stats["hunt"]), 3),
            hunt_ring0=round(stats["hunt_ring0"] / max(1, stats["hunt"]), 3),
            hunt_ring1=round(stats["hunt_ring1"] / max(1, stats["hunt"]), 3),
            hunt_parity_even=round(stats["hunt_parity_even"] / max(1, stats["hunt"]), 3),
            target_shots=stats["target"], target_miss=stats["target_miss"],
            into_known_empty_halo=stats["into_known_empty_halo"],
            into_hit_diagonal=stats["into_hit_diagonal"],
            misses_to_sink_all=miss_to_kill,
            first_shot=Counter(f[0] for f in first if f).most_common(5),
            identical_first5=max(repeats.values()) if repeats else 0,
            top_hunt_cells=hunt_cells.most_common(10),
        )
    return out


def placement(battles, samples=20000):
    rng = random.Random(1)
    base = Counter()
    for _ in range(samples):
        base += placement_features(random_fleet(rng))
    baseline = {k: round(v / samples, 3) for k, v in base.items()}
    out = {"uniform_random_per_fleet": baseline}
    for name in sorted({b["bot"] for b in battles}):
        feats, full, heat = Counter(), 0, Counter()
        boards = []
        for b in battles:
            if b["bot"] != name:
                continue
            ships = clusters_from_shots(b["our_shots"])
            if len(ships) == 10 and sorted(map(len, ships)) == sorted(FLEET):
                full += 1
                feats += placement_features(ships)
                for s in ships:
                    heat.update(s)
                boards.append(sorted(sorted(s) for s in ships))
        out[name] = dict(full_fleets=full,
                         per_fleet={k: round(v / max(1, full), 3) for k, v in feats.items()},
                         heat=sorted(heat.items(), key=lambda x: -x[1])[:15],
                         boards=boards)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=ROOT / "data/bot2/low-expectations-analysis.json")
    args = ap.parse_args()
    battles = load()
    result = dict(battles=len(battles), attack=attack(battles), placement=placement(battles))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=list) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "placement"}, ensure_ascii=False, indent=1, default=list))
    for k, v in result["placement"].items():
        print(k, json.dumps({x: y for x, y in v.items() if x != "boards"} if isinstance(v, dict) and "boards" in v else v,
                            ensure_ascii=False, default=list))


if __name__ == "__main__":
    main()
