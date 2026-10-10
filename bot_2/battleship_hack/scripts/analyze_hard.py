import json
import argparse
import re
import statistics
from collections import Counter
from pathlib import Path
import duckdb

from battleship_hack.game import Density, Baseline, parse_cell, neighbors

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description='Replay fleet-mc MEDIUM/HARD events from DuckDB without server access.')
parser.add_argument('--db', type=Path, default=ROOT / 'data/bot2/analytics.duckdb')
parser.add_argument('--output', type=Path, help='Optional JSON summary including reconstructed sunk ships.')
args = parser.parse_args()
results = []
violations = []
errors = Counter()
db = duckdb.connect(str(args.db), read_only=True)
run_ids = [r[0] for r in db.execute("SELECT run_id FROM runs WHERE strategy='fleet-mc' ORDER BY started_at").fetchall()]
for run_id in run_ids:
    path = Path(run_id + '.jsonl')
    events = [dict(zip(('battle_id', 'type', 'direction', 'payload'), (battle_id, kind, direction, json.loads(payload))))
              for battle_id, kind, direction, payload in db.execute(
                  'SELECT battle_id, type, direction, payload FROM events WHERE run_id=? ORDER BY sequence', [run_id]).fetchall()]
    run = next(e['payload'] for e in events if e['type'] == 'RUN_STARTED')
    if run['strategy'] != 'fleet-mc':
        continue
    for start in [e for e in events if e['type'] == 'BATTLE_STARTED']:
        difficulty = start['payload']['opponent']['builtInBot']
        if difficulty not in {'MEDIUM', 'HARD'}:
            continue
        battle = [e for e in events if e['battle_id'] == start['battle_id']]
        rules = start['payload']['rules']
        own, enemy = Density(rules, 0), Baseline(rules, 0)
        counters = {'own': Counter(), 'enemy': Counter()}
        decision = None
        traces = []
        estimates = []
        samples = []
        times = []
        finishes = Counter()
        revealed_ships = []
        ships = next(e['payload']['ships'] for e in battle if e['type'] == 'PLACE')
        own_cells = {parse_cell(c) for ship in ships for c in ship['cells']}
        enemy_success = set()
        enemy_openings = []
        for e in battle:
            p = e['payload']
            if e['type'] == 'DECISION':
                decision = p
                times.append(p['elapsed_ms'])
                m = re.search(r'samples=(\d+)', p['reason'])
                if m:
                    samples.append(int(m[1]))
            if e['type'] not in {'SHOT_RESOLVED', 'OPPONENT_SHOT'}:
                continue
            side = 'own' if e['type'] == 'SHOT_RESOLVED' else 'enemy'
            model = own if side == 'own' else enemy
            cell = parse_cell(p['cell'])
            target = bool(model.hits)
            phase = 'target' if target else 'hunt'
            outcome = 'hit' if p['result'] in {'HIT', 'SUNK'} else 'miss'
            count = counters[side]
            count[f'{phase}_{outcome}'] += 1
            count['shots'] += 1
            count['hits' if outcome == 'hit' else 'misses'] += 1
            if cell in model.used or (side == 'own' and cell in model.empty):
                violations.append([path.stem, start['battle_id'], side, 'inferred illegal cell', cell])
            if side == 'enemy' and cell in model.empty:
                count['shots_in_inferred_empty'] += 1
            if side == 'own':
                if cell != parse_cell(decision['cell']):
                    violations.append([path.stem, start['battle_id'], side, 'decision mismatch', cell])
                if 'fallback' in decision['reason']:
                    count['fallbacks'] += 1
                if not target:
                    m = re.search(r'estimated_hit=([.\d]+)', decision['reason'])
                    if m:
                        estimates.append([float(m[1]), outcome == 'hit'])
                    edge = cell[0] in (0, rules['rows']-1) or cell[1] in (0, rules['columns']-1)
                    count[f"hunt_{'edge' if edge else 'inner'}_{outcome}"] += 1
                if all(l == 1 for l in +own.remaining):
                    count['singles_only_shots'] += 1
                    count['singles_only_hits'] += outcome == 'hit'
                if target and not any(cell in neighbors(h) for h in model.hits):
                    count['target_nonadjacent'] += 1
                before = dict(+own.remaining)
                hits_before = sorted(own.hits)
                model.observe(p)
                if p['result'] == 'SUNK':
                    changes = [l for l, n in before.items() if own.remaining[l] < n]
                    if len(changes) != 1:
                        violations.append([path.stem, start['battle_id'], side, 'bad sunk decrement', cell])
                    else:
                        finishes[changes[0]] += 1
                        cluster, todo = {cell}, [cell]
                        before_cells = set(hits_before)
                        while todo:
                            adjacent = neighbors(todo.pop()) & before_cells - cluster
                            cluster |= adjacent
                            todo.extend(adjacent)
                        revealed_ships.append({'length': len(cluster), 'cells': sorted(cluster),
                            'all_edge': all(r in (0, rules['rows']-1) or c in (0, rules['columns']-1) for r, c in cluster),
                            'all_inner': all(r not in (0, rules['rows']-1) and c not in (0, rules['columns']-1) for r, c in cluster)})
                traces.append({'n': count['shots'], 'cell': cell, 'result': p['result'], 'phase': phase,
                               'remaining': before, 'hits_before': hits_before, 'reason': decision['reason']})
            else:
                if count['shots'] <= 8:
                    enemy_openings.append([cell, p['result'], phase])
                if target and not any(cell in neighbors(h) for h in model.hits):
                    count['target_nonadjacent'] += 1
                actual = cell in own_cells
                if actual != (outcome == 'hit'):
                    violations.append([path.stem, start['battle_id'], side, 'placement/result mismatch', cell])
                if actual:
                    enemy_success.add(cell)
                model.observe(p)
        end = next(e['payload'] for e in battle if e['type'] == 'BATTLE_FINISHED')
        errors.update(e['type'] for e in battle if e['type'] in {'ERROR', 'PROTOCOL_ERROR', 'TURN_TIMED_OUT', 'COMMAND_REJECTED', 'SHOT_REJECTED', 'PLACEMENT_REJECTED', 'DISCONNECTED'})
        results.append({'run': path.stem, 'battle': start['battle_id'], 'difficulty': difficulty,
                        'outcome': end['outcome'], 'end_reason': end['endReason'],
                        'own': dict(counters['own']), 'enemy': dict(counters['enemy']),
                        'remaining': dict(+own.remaining), 'unsunk_hits': sorted(own.hits),
                        'own_unsunk_ships': [len(ship['cells']) for ship in ships if not {parse_cell(c) for c in ship['cells']} <= enemy_success],
                        'sunk_lengths': dict(finishes), 'estimates': estimates, 'samples': samples,
                        'times': times, 'traces': traces, 'enemy_openings': enemy_openings,
                        'revealed_ships': revealed_ships})

db.close()
summary = {'source': str(args.db.resolve()), 'violations': violations, 'errors': dict(errors), 'battles': []}
for difficulty in ('MEDIUM', 'HARD'):
    group = [b for b in results if b['difficulty'] == difficulty]
    if not group:
        continue
    totals = {side: sum((Counter(b[side]) for b in group), Counter()) for side in ('own', 'enemy')}
    estimates = [v for b in group for v in b['estimates']]
    times = [v for b in group for v in b['times']]
    sample_counts = Counter(v for b in group for v in b['samples'])
    ships = [ship for b in group for ship in b['revealed_ships']]
    summary[difficulty] = {'matches': len(group), 'wins': sum(b['outcome'] == 'WIN' for b in group),
                           'totals': totals, 'mc_mean_estimate': statistics.mean(p for p, hit in estimates),
                           'mc_actual_hit_rate': statistics.mean(hit for p, hit in estimates),
                           'mc_shots': len(estimates), 'mc_samples': dict(sample_counts),
                           'max_ms': max(times), 'p95_ms': statistics.quantiles(times, n=100, method='inclusive')[94],
                           'revealed_multi_ships': sum(ship['length'] > 1 for ship in ships),
                           'revealed_multi_ships_all_edge': sum(ship['length'] > 1 and ship['all_edge'] for ship in ships),
                           'revealed_singles': sum(ship['length'] == 1 for ship in ships),
                           'revealed_singles_all_inner': sum(ship['length'] == 1 and ship['all_inner'] for ship in ships)}
for b in results:
    if b['difficulty'] == 'HARD':
        summary['battles'].append({k: v for k, v in b.items() if k not in {'estimates', 'samples', 'times', 'traces'}})
if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k: v for k, v in summary.items() if k != 'battles'}, ensure_ascii=False, indent=2))
if violations:
    raise SystemExit(1)
