"""Offline paired singleton-endgame experiment; no network or live strategy changes.

Exact counting is uniform over legal sets of remaining singleton cells. Missing
ships in losses are completed from that same model, reported separately.
"""
import argparse
import hashlib
import itertools
import json
import random
import statistics
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

from battleship_hack.game import HardEdge, HardQuadrants, cell_json, neighbors, parse_cell, validate_placement

ROOT = Path(__file__).resolve().parents[1]


class ExactSingles(HardEdge):
    """Experimental exact posterior, applied only after all larger ships sink."""
    def choose(self):
        if self.model_disabled is not None or self.hits or any(l > 1 for l in +self.remaining):
            return super().choose()
        cells = sorted(p[2][0] for p in self.candidates()[1] if not self.is_edge(p[2][0]))
        k = self.remaining[1]
        conflicts = [sum(1 << j for j, other in enumerate(cells)
                         if other == cell or other in neighbors(cell, True)) for cell in cells]

        @lru_cache(None)
        def count(mask, left):
            if left == 0:
                return 1
            if mask.bit_count() < left:
                return 0
            if left == 1:
                return mask.bit_count()
            bit = mask & -mask
            i = bit.bit_length() - 1
            return count(mask ^ bit, left) + count(mask & ~conflicts[i], left - 1)

        mask = (1 << len(cells)) - 1
        scores = [count(mask & ~conflicts[i], k - 1) for i in range(len(cells))]
        total = count(mask, k)
        if not total:
            return super().choose()
        assert sum(scores) == k * total
        best = max(scores)
        return self.rng.choice([c for c, s in zip(cells, scores) if s == best]), f"exact singles configs={total}; hit={best/total:.6f}"


class WeightedSingles(HardEdge):
    """SIS correction to the existing sequential sampler, singleton phase only."""
    def choose(self):
        if self.model_disabled is not None or self.hits or any(l > 1 for l in +self.remaining):
            return super().choose()
        if self.remaining[1] == 1:
            return super().choose()
        options = [p for p in self.candidates()[1] if not self.is_edge(p[2][0])]
        occupancy = Counter()
        accepted = 0
        for _ in range(4000):
            blocked, fleet, weight = 0, [], 1
            for _ in range(self.remaining[1]):
                available = [p for p in options if not p[0] & blocked]
                if not available:
                    break
                weight *= len(available)
                mask, halo, cells = self.rng.choice(available)
                blocked |= mask | halo
                fleet.extend(cells)
            else:
                for cell in fleet:
                    occupancy[cell] += weight
                accepted += 1
                if accepted == 1000:
                    break
        if not occupancy:
            return super().choose()
        best = max(occupancy.values())
        return self.rng.choice(sorted(c for c, s in occupancy.items() if s == best)), f"weighted singles samples={accepted}"


def reconstruct(events):
    starts = [e for e in events if e['type'] == 'BATTLE_STARTED'
              and e['payload']['opponent'].get('builtInBot') == 'HARD'][-20:]
    battles = []
    for start in starts:
        battle = [e for e in events if e.get('battle_id') == start['battle_id']]
        rules = start['payload']['rules']
        assert rules.get('modifier') is None
        model = HardEdge(rules, 0)
        ships, prefix, endgame = [], [], None
        shots = [e['payload'] for e in battle if e['type'] == 'SHOT_RESOLVED' and e['direction'] == 'in']
        for shot in shots:
            cell = parse_cell(shot['cell'])
            if shot['result'] == 'SUNK':
                cluster, todo = {cell}, [cell]
                while todo:
                    adjacent = neighbors(todo.pop()) & model.hits - cluster
                    cluster |= adjacent
                    todo.extend(adjacent)
                ships.append(sorted(cluster))
            model.observe(shot)
            prefix.append(shot)
            if endgame is None and not any(l > 1 for l in +model.remaining):
                endgame = list(prefix)
        assert endgame is not None and not model.hits
        finish = next(e['payload'] for e in battle if e['type'] == 'BATTLE_FINISHED')
        assert len([s for s in ships if len(s) > 1]) == 6
        checked, repeated = set(), []
        for shot in shots:
            observed = any(parse_cell(shot['cell']) in s for s in ships)
            cell = parse_cell(shot['cell'])
            if cell in checked:
                repeated.append(shot)
            else:
                assert observed == (shot['result'] in {'HIT', 'SUNK'}), (start['battle_id'], shot, ships)
            checked.add(cell)
        candidates = sorted(p[2][0] for p in model.candidates().get(1, []) if not model.is_edge(p[2][0]))
        missing = model.remaining[1]
        completions = [c for c in itertools.combinations(candidates, missing)
                       if all(b not in neighbors(a, True) for a, b in itertools.combinations(c, 2))]
        assert completions
        battles.append(dict(id=start['battle_id'], rules=rules, outcome=finish['outcome'],
                            ships=ships, prefix=endgame, missing=missing, completions=completions,
                            historical_shots=len(shots), repeated_results=repeated))
    assert len(battles) == 20
    return battles


def play(strategy, battle, ships, seed):
    model = strategy(battle['rules'], seed)
    for shot in battle['prefix']:
        model.observe(shot)
    occupied = {c for ship in ships for c in ship}
    shot_count, misses, times = 0, 0, []
    while +model.remaining:
        before = time.perf_counter()
        cell, reason = model.choose()
        times.append((time.perf_counter() - before) * 1000)
        assert cell not in model.used | model.empty
        assert 'fallback' not in reason or strategy is HardQuadrants
        assert not model.model_disabled
        if cell not in occupied:
            result = 'MISS'
            misses += 1
        else:
            ship = next(s for s in ships if cell in s)
            result = 'SUNK' if set(ship) <= model.used | {cell} else 'HIT'
        model.observe(dict(cell=cell_json(cell), result=result))
        shot_count += 1
        assert shot_count <= 100
    return dict(shots=shot_count, misses=misses, times=times)


def summary(rows):
    result = {}
    for name in ('hard-edge', 'weighted-singles', 'exact-singles', 'hard-quadrants'):
        values = [r[name]['misses'] for r in rows]
        timings = sorted(t for r in rows for t in r[name]['times'])
        result[name] = dict(mean_misses=statistics.mean(values),
                            p95_ms=timings[int(.95*(len(timings)-1))], max_ms=max(timings))
        if name != 'hard-edge':
            deltas = [r[name]['misses'] - r['hard-edge']['misses'] for r in rows]
            result[name].update(mean_delta=statistics.mean(deltas),
                                better=sum(d < 0 for d in deltas), equal=sum(d == 0 for d in deltas),
                                worse=sum(d > 0 for d in deltas))
    return result


def enrich(report):
    """Aggregate by original battle, never treat seeds as independent boards."""
    report['per_battle'] = []
    for item in report['inventory']:
        rows = [r for r in report['rows'] if r['index'] == item['index']]
        report['per_battle'].append(dict(index=item['index'], missing=item['missing'],
            means={name: statistics.mean(r[name]['misses'] for r in rows)
                   for name in ('hard-edge', 'weighted-singles', 'exact-singles', 'hard-quadrants')}))
    for group, full in [('observed_complete', True), ('model_completed_losses', False)]:
        battles = [b for b in report['per_battle'] if (b['missing'] == 0) == full]
        for name in ('weighted-singles', 'exact-singles', 'hard-quadrants'):
            deltas = [b['means'][name]-b['means']['hard-edge'] for b in battles]
            rng = random.Random(61010)
            bootstrap = sorted(statistics.mean(rng.choices(deltas, k=len(deltas))) for _ in range(10000))
            report['summaries'][group]['strategies'][name].update(
                battle_weighted_delta=statistics.mean(deltas),
                exploratory_battle_bootstrap_95=[bootstrap[250], bootstrap[9749]])
    report['limitations'].extend([
        'Complete boards are selected by historical wins, so are not representative of all HARD boards.',
        'Bootstrap intervals are exploratory, with only ten original boards in each group.',
        'Quadrant prior is assessed on the same series that supports it; requires held-out validation.'])
    return report


def self_check():
    # Tiny board: exact combinatorial identity is also checked at every decision.
    rules = dict(rows=5, columns=5, fleet=[dict(length=1, count=2)],
                 touchRule='NO_TOUCHING_EVEN_DIAGONALLY', modifier=None, moveTimeoutSeconds=1)
    bot = ExactSingles(rules, 7)
    cell, reason = bot.choose()
    options = [(r, c) for r in range(1, 4) for c in range(1, 4)]
    configs = [pair for pair in itertools.combinations(options, 2) if pair[1] not in neighbors(pair[0], True)]
    counts = Counter(c for pair in configs for c in pair)
    assert counts[cell] == max(counts.values()) and f'configs={len(configs)}' in reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, default=ROOT/'data/bot2/logs/dee56794-3689-400c-8984-dd02df38ac8b.jsonl')
    parser.add_argument('--seeds', type=int, default=20)
    parser.add_argument('--completions', type=int, default=20)
    parser.add_argument('--output', type=Path, default=ROOT/'data/bot2/local-hard-singles.json')
    args = parser.parse_args()
    self_check()
    events = [json.loads(line) for line in args.log.read_text().splitlines()]
    battles = reconstruct(events)
    rows, inventory = [], []
    for index, battle in enumerate(battles, 1):
        choices = battle['completions']
        rng = random.Random(61010 + index)
        selected = rng.sample(choices, min(args.completions, len(choices)))
        inventory.append(dict(index=index, battle=battle['id'], outcome=battle['outcome'],
                              missing=battle['missing'], compatible_completions=len(choices),
                              evaluated_completions=len(selected), prefix_shots=len(battle['prefix']),
                              repeated_results=battle['repeated_results']))
        for completion_id, completion in enumerate(selected):
            ships = battle['ships'] + [[c] for c in completion]
            validate_placement(battle['rules'], [dict(cells=[cell_json(c) for c in s]) for s in ships])
            for seed in range(args.seeds):
                row = dict(index=index, completion=completion_id, seed=seed,
                           observed_full=battle['missing'] == 0)
                for name, strategy in [('hard-edge', HardEdge), ('weighted-singles', WeightedSingles), ('exact-singles', ExactSingles), ('hard-quadrants', HardQuadrants)]:
                    row[name] = play(strategy, battle, ships, 100000 + seed)
                rows.append(row)
        print(f"battle {index}/20: missing={battle['missing']}, completions={len(selected)}/{len(choices)}", flush=True)
    groups = {name: [r for r in rows if r['observed_full'] == full]
              for name, full in [('observed_complete', True), ('model_completed_losses', False)]}
    report = dict(source=str(args.log), source_sha256=hashlib.sha256(args.log.read_bytes()).hexdigest(),
                  seeds=args.seeds, inventory=inventory,
                  summaries={name: dict(pairs=len(group), strategies=summary(group)) for name, group in groups.items()},
                  rows=rows, limitations=['Only singleton endgame changed; observed prefix held fixed.',
                  'Loss completions are model-based sensitivity analysis, not revealed enemy boards.',
                  'Seeds and completions within one battle are dependent; independent board count is 20.',
                  'No counterfactual win rate: changed attack can change enemy turns and shots.'])
    enrich(report)
    report['complete_board_total_shots'] = {
        name: statistics.mean(20 + sum(s['result'] == 'MISS' for s in battles[r['index']-1]['prefix'])
                              + r[name]['misses'] for r in rows if r['observed_full'])
        for name in ('hard-edge', 'weighted-singles', 'exact-singles', 'hard-quadrants')}
    report['code_sha256'] = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), ROOT/'src/battleship_hack/game.py')}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report['summaries'], indent=2))


if __name__ == '__main__':
    main()
