"""Offline DUEL shot-count comparison on complete historical ranking boards."""
import json
import statistics
import time
from pathlib import Path
from battleship_hack.game import FleetMCExact, FleetMCDuel, cell_json

ROOT = Path(__file__).resolve().parents[1]


def play(cls, board, seed):
    rules = {**board['rules'], 'modifier':'DUEL', 'turnAfterHitRule':'HIT_SPENDS_SHOT', 'shotsPerTurn':1}
    model = cls(rules, seed)
    ships = [set(map(tuple,s)) for s in board['revealed_ships']]
    occupied = set().union(*ships)
    shots, times, fallback, lookahead = 0, [], 0, 0
    while +model.remaining:
        start = time.perf_counter()
        cell, reason = model.choose()
        times.append((time.perf_counter()-start)*1000)
        assert cell not in model.used | model.empty
        fallback += 'fallback' in reason
        lookahead += 'two-shot' in reason
        if cell in occupied:
            ship = next(s for s in ships if cell in s)
            result = 'SUNK' if ship <= model.used | {cell} else 'HIT'
        else:
            result = 'MISS'
        model.observe(dict(cell=cell_json(cell),result=result))
        shots += 1
        assert shots <= 100
    return dict(shots=shots,times=times,fallbacks=fallback,lookahead=lookahead)


if __name__ == '__main__':
    analysis=json.loads((ROOT/'data/bot2/ranking-fleet-analysis.json').read_text())
    boards=[b for b in analysis['battles'] if b['outcome']=='WIN']
    rows=[]
    for b in boards:
        for seed in (61010,61011):
            rows.append(dict(battle=b['battle'],seed=seed,
                baseline=play(FleetMCExact,b,seed),duel=play(FleetMCDuel,b,seed)))
        print(f"DUEL board done: {b['battle']}",flush=True)
    report=dict(boards=len(boards),pairs=len(rows),strategies={name:dict(
        mean_shots=statistics.mean(r[name]['shots'] for r in rows),
        p95_ms=statistics.quantiles([t for r in rows for t in r[name]['times']],n=100,method='inclusive')[94],
        max_ms=max(t for r in rows for t in r[name]['times']),
        fallback_decisions=sum(r[name]['fallbacks'] for r in rows),
        lookahead_decisions=sum(r[name]['lookahead'] for r in rows)) for name in ('baseline','duel')},
        rows=rows,limitations=['Only six boards revealed by historical wins; two seeds each.',
            'DUEL rules imposed locally; no live DUEL opponents or win rate measured.',
            'Sampling deadlines depend on hardware; shot counts are not fully deterministic.'])
    (ROOT/'data/bot2/duel-local-check.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))
