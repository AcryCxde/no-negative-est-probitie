"""Read-only analysis of the two requested fleet-mc ranking series."""
import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

import duckdb
from battleship_hack.game import Density, neighbors, parse_cell

ROOT = Path(__file__).resolve().parents[1]
RUNS = ['cc45fbe5-381c-422a-8783-c481f494cea5', '9e5adbfb-fc40-4dbc-8e15-57852ea61c18']


def analyze(run_ids):
    with duckdb.connect(str(ROOT/'data/bot2/analytics.duckdb'), read_only=True) as db:
        records = {run: [dict(battle_id=b, type=t, payload=json.loads(p)) for b, t, p in db.execute(
            'SELECT battle_id,type,payload FROM events WHERE run_id=? ORDER BY sequence', [run]).fetchall()]
                   for run in run_ids}
    result = dict(runs=[], battles=[])
    for run_id, events in records.items():
        metadata = next(e['payload'] for e in events if e['type'] == 'RUN_STARTED')
        stats = [e['payload'] for e in events if e['type'] == 'HTTP_STATISTICS']
        results = []
        for start in [e for e in events if e['type'] == 'BATTLE_STARTED']:
            battle = [e for e in events if e['battle_id'] == start['battle_id']]
            rules = start['payload']['rules']
            model = Density(rules, 0)
            counts = Counter()
            sunk, prefix, endgame = [], [], None
            for e in battle:
                if e['type'] != 'SHOT_RESOLVED':
                    continue
                p = e['payload']
                cell = parse_cell(p['cell'])
                phase = 'target' if model.hits else 'hunt'
                singles = not any(l > 1 for l in +model.remaining)
                hit = p['result'] in ('HIT', 'SUNK')
                counts['shots'] += 1
                counts['hits' if hit else 'misses'] += 1
                counts[f'{phase}_{"hits" if hit else "misses"}'] += 1
                counts['repeated_results'] += cell in model.used
                if singles:
                    counts['singles_shots'] += 1
                    counts['singles_hits' if hit else 'singles_misses'] += 1
                    if model.remaining[1] == 1:
                        counts['last_single_shots'] += 1
                        counts['last_single_misses'] += not hit
                if p['result'] == 'SUNK':
                    cluster, todo = {cell}, [cell]
                    while todo:
                        adjacent = neighbors(todo.pop()) & model.hits - cluster
                        cluster |= adjacent
                        todo.extend(adjacent)
                    sunk.append(sorted(cluster))
                model.observe(p)
                prefix.append(p)
                if endgame is None and singles is False and not any(l > 1 for l in +model.remaining):
                    endgame = list(prefix)
            end = next(e['payload'] for e in battle if e['type'] == 'BATTLE_FINISHED')
            links = [e['payload'] for e in battle if e['type'] == 'HTTP_MATCH_LINKED']
            decisions = [e['payload'] for e in battle if e['type'] == 'DECISION']
            errors = Counter(e['type'] for e in battle if e['type'] in ('TURN_TIMED_OUT','SHOT_REJECTED','ERROR','PROTOCOL_ERROR','DISCONNECTED'))
            row = dict(run=run_id, battle=start['battle_id'], rules=rules, outcome=end['outcome'],
                opponent=start['payload']['opponent'], counts=dict(counts), remaining=dict(+model.remaining),
                revealed_ships=sunk, singleton_prefix=endgame,
                rating_change=links[-1].get('ratingChange') if links else None,
                match_id=links[-1].get('matchId') if links else None,
                times=[d['elapsed_ms'] for d in decisions], errors=dict(errors),
                mc_fallback=sum('MC fallback' in d['reason'] for d in decisions))
            results.append(row)
        times = [t for b in results for t in b['times']]
        aggregate = sum((Counter(b['counts']) for b in results), Counter())
        result['runs'].append(dict(run=run_id, seed=metadata['seed'], matches=len(results),
            wins=sum(b['outcome']=='WIN' for b in results), losses=sum(b['outcome']=='LOSS' for b in results),
            rating_before=stats[0]['rating'], rating_snapshot_after=stats[-1]['rating'],
            linked_rating_change=sum(b['rating_change'] for b in results if b['rating_change'] is not None),
            known_changes=sum(b['rating_change'] is not None for b in results), counts=dict(aggregate),
            p95_ms=statistics.quantiles(times,n=100,method='inclusive')[94], max_ms=max(times),
            errors=dict(sum((Counter(b['errors']) for b in results),Counter()))))
        result['battles'].extend(results)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='+', default=RUNS)
    parser.add_argument('--output', type=Path, default=ROOT/'data/bot2/ranking-fleet-analysis.json')
    args = parser.parse_args()
    result = analyze(args.runs)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result['runs'],ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
