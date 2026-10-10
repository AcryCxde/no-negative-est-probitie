"""Summarize live SCHRODINGER_SHIP battles straight from JSONL logs (no DuckDB lock needed).

Usage: uv run python scripts/analyze_schrodinger_live.py [logs_dir]
"""

import collections
import json
import sys
from pathlib import Path

logs = Path(sys.argv[1] if len(sys.argv) > 1 else "data/bot2/logs")
battles = {}
order = []
last_stats = None

for path in sorted(logs.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
    for line in path.open():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        t, p, bid = r.get("type"), r.get("payload") or {}, r.get("battle_id")
        if t == "HTTP_STATISTICS":
            last_stats = (r["timestamp"], p)
        if t == "BATTLE_STARTED":
            if (p.get("rules") or {}).get("modifier") != "SCHRODINGER_SHIP":
                continue
            opp = p.get("opponent") or {}
            bon = p.get("bonuses") or {}
            if isinstance(bon, list):
                bon = {x["bonus"]: x["uses"] for x in bon}
            battles[bid] = b = collections.defaultdict(int)
            b.update(run=path.stem[:8], strategy=p.get("strategy"), opp=opp.get("teamName") or opp.get("builtInBot"), first=None,
                     bonuses=",".join(f"{k}{v}" for k, v in sorted(bon.items())),
                     opp_bonus=[], edge_cells=0, ship_cells=0, ts=r["timestamp"], our_last_hit_streak=0)
            order.append(bid)
            continue
        b = battles.get(bid)
        if b is None:
            continue
        if t == "PLACE":
            for s in p["ships"]:
                for c in s["cells"]:
                    b["ship_cells"] += 1
                    b["edge_cells"] += c["row"] in (0, 9) or c["column"] in (0, 9)
        elif t == "SHOT_RESOLVED":
            b["first"] = b["first"] or "us"
            b["our_shots"] += 1
            b["our_hits"] += p["result"] in ("HIT", "SUNK")
            b["our_" + p["result"].lower()] += 1
            if b["our_shots"] <= 10:
                b["our_hits10"] += p["result"] in ("HIT", "SUNK")
        elif t == "OPPONENT_SHOT":
            b["first"] = b["first"] or "opp"
            b["opp_shots"] += 1
            b["opp_hits"] += p["result"] in ("HIT", "SUNK")
            if b["opp_shots"] <= 10:
                b["opp_hits10"] += p["result"] in ("HIT", "SUNK")
        elif t == "OPPONENT_BONUS_USED":
            b["opp_bonus"].append(p.get("bonus"))
        elif t == "BONUS_RESOLVED":
            b["our_bonus_used"] += 1
        elif t == "TURN_TIMED_OUT" or t == "SHOT_REJECTED":
            b["errors"] += 1
        elif t == "DECISION":
            ms = p.get("elapsed_ms") or 0
            b["max_ms"] = max(b["max_ms"], ms)
        elif t == "MATCH_FINISHED":
            b["outcome"] = p.get("outcome")
            b["end"] = p.get("endReason")

rows = [battles[b] for b in order if battles[b].get("outcome") and battles[b]["our_shots"]]
print(f"{'run':8} {'opponent':20} {'res':4} {'1st':3} {'us s/h':>7} {'opp s/h':>7} {'opp h@10':>8} {'edge':>5} {'maxms':>6} bonuses / opp")
for b in rows:
    print(f"{b['run']:8} {str(b['opp'])[:20]:20} {b['outcome'][:4]:4} {b['first'] or '-':3} "
          f"{b['our_shots']:>3}/{b['our_hits']:<3} {b['opp_shots']:>3}/{b['opp_hits']:<3} {b['opp_hits10']:>8} "
          f"{b['edge_cells']:>2}/{b['ship_cells']:<2} {b['max_ms']:>6.0f} {b['bonuses']} / {','.join(filter(None, b['opp_bonus']))} err={b['errors']}")

def agg(group):
    w = sum(b["outcome"] == "WIN" for b in group)
    return f"{w}/{len(group)} WR={w / len(group):.0%}" if group else "-"

print("\nTotal:", agg(rows))
for key in ("strategy", "opp", "first", "bonuses"):
    print(f"\nBy {key}:")
    g = collections.defaultdict(list)
    for b in rows:
        g[b[key]].append(b)
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
        hits_us = sum(x["our_hits"] for x in v) / sum(x["our_shots"] for x in v)
        hits_opp = sum(x["opp_hits"] for x in v) / max(1, sum(x["opp_shots"] for x in v))
        print(f"  {str(k):22} {agg(v):14} our hit% {hits_us:.0%}  opp hit% {hits_opp:.0%}")

wins = [b for b in rows if b["outcome"] == "WIN"]
losses = [b for b in rows if b["outcome"] != "WIN"]
if wins:
    print(f"\nWins: mean shots to sink fleet {sum(b['our_shots'] for b in wins) / len(wins):.1f}")
if losses:
    print(f"Losses: our hits when we died {sum(b['our_hits'] for b in losses) / len(losses):.1f}/20, "
          f"opp shots to kill us {sum(b['opp_shots'] for b in losses) / len(losses):.1f}")
allopp = [b for b in rows]
print(f"Opp mean shots/hit-rate vs us: {sum(b['opp_shots'] for b in allopp) / len(allopp):.1f} shots, "
      f"{sum(b['opp_hits'] for b in allopp) / sum(b['opp_shots'] for b in allopp):.0%}; "
      f"opp hits in first 10 shots: {sum(b['opp_hits10'] for b in allopp) / len(allopp):.1f}, "
      f"ours: {sum(b['our_hits10'] for b in allopp) / len(allopp):.1f}")
for name, group in (("wins", wins), ("losses", losses)):
    print(f"  {name}: opp hits@10 {sum(b['opp_hits10'] for b in group) / len(group):.1f}, "
          f"our hits@10 {sum(b['our_hits10'] for b in group) / len(group):.1f}")

if last_stats:
    ts, s = last_stats
    print(f"\nLatest HTTP stats ({ts}): rating {s.get('rating'):.1f}, place {s.get('place')}")
    for r in s.get("rules") or []:
        print("  ", r)
