"""Paired offline checks on revealed ranking boards, without server access."""
import json
import random
import statistics
import time
from pathlib import Path

from battleship_hack.game import FleetMC, FleetMCExact, cell_json

from ranking_target_candidate import FleetMCTarget

ROOT = Path(__file__).resolve().parents[1]


def restore(cls, model, seed):
    probe = cls(model.rules, seed)
    for attr in ('used', 'empty', 'hits'):
        setattr(probe, attr, set(getattr(model, attr)))
    probe.remaining = model.remaining.copy()
    return probe


def finish(cls, board, seed):
    model = cls(board['rules'], seed)
    for shot in board['singleton_prefix']:
        model.observe(shot)
    ships = [set(map(tuple,s)) for s in board['revealed_ships']]
    occupied = set().union(*ships)
    misses, times, fallbacks = 0, [], 0
    while +model.remaining:
        t = time.perf_counter()
        cell, reason = model.choose()
        times.append((time.perf_counter()-t)*1000)
        fallbacks += 'fallback' in reason
        assert cell not in model.used | model.empty
        hit = cell in occupied
        if not hit:
            result = 'MISS'
            misses += 1
        else:
            ship = next(s for s in ships if cell in s)
            result = 'SUNK' if ship <= model.used | {cell} else 'HIT'
        model.observe(dict(cell=cell_json(cell), result=result))
    return dict(misses=misses, times=times, fallbacks=fallbacks)


def main():
    analysis = json.loads((ROOT/'data/bot2/ranking-fleet-analysis.json').read_text())
    boards = [b for b in analysis['battles'] if b['outcome']=='WIN']
    target_rows, endgame_rows = [], []
    for board in boards:
        occupied = {tuple(c) for ship in board['revealed_ships'] for c in ship}
        assert len(occupied) == 20
        events = [json.loads(l) for l in (ROOT/'data/bot2/logs'/f"{board['run']}.jsonl").read_text().splitlines()]
        shots = [e['payload'] for e in events if e['type']=='SHOT_RESOLVED' and e['battle_id']==board['battle']]
        model = FleetMC(board['rules'], 0)
        for n, shot in enumerate(shots,1):
            if model.hits:
                for seed in range(3):
                    row = dict(battle=board['battle'], shot=n, seed=seed)
                    for name, cls in [('baseline',FleetMC),('target',FleetMCTarget)]:
                        probe = restore(cls,model,seed)
                        t = time.perf_counter()
                        cell, reason = probe.choose()
                        assert cell not in model.used | model.empty
                        row[name] = dict(hit=cell in occupied, ms=(time.perf_counter()-t)*1000, fallback='fallback' in reason)
                    target_rows.append(row)
            model.observe(shot)
        for seed in range(20):
            endgame_rows.append(dict(battle=board['battle'], seed=seed,
                baseline=finish(FleetMC,board,61010+seed), exact=finish(FleetMCExact,board,61010+seed)))
        print(f"board completed: {board['battle']}",flush=True)
    per_board = [statistics.mean(r['exact']['misses']-r['baseline']['misses'] for r in endgame_rows if r['battle']==b['battle']) for b in boards]
    rng = random.Random(61010)
    bootstrap = sorted(statistics.mean(rng.choices(per_board,k=len(per_board))) for _ in range(10000))
    def p95(values):
        return statistics.quantiles(values,n=100,method='inclusive')[94]
    report = dict(known_boards=len(boards), target_pairs=len(target_rows), endgame_pairs=len(endgame_rows),
        target_summary={name:dict(hits=sum(r[name]['hit'] for r in target_rows),
            p95_ms=p95([r[name]['ms'] for r in target_rows]), fallbacks=sum(r[name]['fallback'] for r in target_rows)) for name in ('baseline','target')},
        endgame_summary={name:dict(mean_misses=statistics.mean(r[name]['misses'] for r in endgame_rows),
            p95_ms=p95([t for r in endgame_rows for t in r[name]['times']]),
            fallbacks=sum(r[name]['fallbacks'] for r in endgame_rows)) for name in ('baseline','exact')},
        exact_delta=statistics.mean(per_board), exploratory_board_bootstrap_95=[bootstrap[250],bootstrap[9749]],
        target_rows=target_rows,endgame_rows=endgame_rows,
        limitations=['Only six complete boards from historical wins; losses have hidden ships.',
            'Seeds and observed states from one board are dependent.',
            'Target check measures immediate hits on historical states, not full-game win rate.',
            'Exact check starts from a fixed historical singleton-phase state.',
            'These are local experiments, not additional ranking matches.'])
    (ROOT/'data/bot2/ranking-fleet-variants.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if not k.endswith('rows')},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
