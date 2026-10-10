"""A deliberately simple baseline: random valid placement, hunt and target."""
import random
import time
from collections import Counter
from functools import lru_cache

Cell = tuple[int, int]


def cell_json(cell: Cell) -> dict:
    return {"row": cell[0], "column": cell[1]}


def parse_cell(value: dict) -> Cell:
    return value["row"], value["column"]


def neighbors(cell: Cell, diagonal: bool = False) -> set[Cell]:
    r, c = cell
    return {(r + dr, c + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)
            if (dr or dc) and (diagonal or abs(dr) + abs(dc) == 1)}


def validate_placement(rules: dict, ships: list[dict]) -> None:
    expected = Counter({entry["length"]: entry["count"] for entry in rules["fleet"]})
    assert Counter(len(ship["cells"]) for ship in ships) == expected, "fleet mismatch"
    occupied: set[Cell] = set()
    for ship in ships:
        cells = {parse_cell(c) for c in ship["cells"]}
        assert len(cells) == len(ship["cells"]), "duplicate cells"
        assert all(0 <= r < rules["rows"] and 0 <= c < rules["columns"] for r, c in cells)
        rows, cols = {r for r, _ in cells}, {c for _, c in cells}
        assert ((len(rows) == 1 and max(cols) - min(cols) + 1 == len(cells)) or
                (len(cols) == 1 and max(rows) - min(rows) + 1 == len(cells))), "not straight"
        assert not occupied.intersection(cells), "overlap"
        assert not any(neighbors(c, True) & occupied for c in cells), "touching"
        occupied.update(cells)


def _interior_depth(cell: Cell, rows: int, columns: int) -> int:
    r, c = cell
    return min(r, rows - 1 - r, c, columns - 1 - c)


def place(rules: dict, rng: random.Random, prefer_interior: bool = False,
          perimeter_multi: bool = False) -> list[dict]:
    if rules["touchRule"] != "NO_TOUCHING_EVEN_DIAGONALLY":
        raise ValueError("Unsupported touchRule; no guessed placement")
    lengths = sorted((f["length"] for f in rules["fleet"] for _ in range(f["count"])), reverse=True)
    rows, columns = rules["rows"], rules["columns"]
    candidates = {}
    for length in set(lengths):
        options = set()
        for r in range(rows):
            for c in range(columns):
                for dr, dc in ((0, 1), (1, 0)):
                    cells = tuple((r + dr * i, c + dc * i) for i in range(length))
                    if all(x < rows and y < columns for x, y in cells):
                        options.add(cells)
        if perimeter_multi and length > 1:
            options = {cells for cells in options
                       if all(_interior_depth(cell, rows, columns) == 0 for cell in cells)}
        ordered = sorted(options)
        if prefer_interior:
            # Deeper fleets first; jitter only within the same depth sum.
            ordered.sort(key=lambda cells: (-sum(_interior_depth(cell, rows, columns) for cell in cells),
                                            rng.random()))
            candidates[length] = ordered
        else:
            candidates[length] = ordered
            rng.shuffle(candidates[length])
    deadline = time.monotonic() + min(0.25, rules["placementWindowSeconds"] * 0.1)

    def search(index: int, blocked: set[Cell]) -> list | None:
        if time.monotonic() > deadline:
            raise TimeoutError("Placement budget exhausted")
        if index == len(lengths):
            return []
        for cells in candidates[lengths[index]]:
            if blocked.isdisjoint(cells):
                halo = set(cells).union(*(neighbors(c, True) for c in cells))
                tail = search(index + 1, blocked | halo)
                if tail is not None:
                    return [cells] + tail
        return None

    result = search(0, set())
    if result is None:
        raise ValueError("No valid fleet found")
    ships = [{"cells": [cell_json(c) for c in cells]} for cells in result]
    validate_placement(rules, ships)
    return ships


class Baseline:
    def __init__(self, rules: dict, seed: int):
        self.rules = rules
        self.rng = random.Random(seed)
        self.used: set[Cell] = set()
        self.empty: set[Cell] = set()
        self.hits: set[Cell] = set()
        self.shots = 0

    def place_fleet(self) -> list[dict]:
        return place(self.rules, self.rng)

    def observe(self, event: dict) -> None:
        cell, result = parse_cell(event["cell"]), event["result"]
        self.shots += 1
        if result == "DEFENDED":
            self.used.discard(cell)  # Shield does not mark the cell as shot.
            return
        self.used.add(cell)
        if result not in {"HIT", "SUNK"}:
            return
        self.hits.add(cell)
        if self.rules["touchRule"] == "NO_TOUCHING_EVEN_DIAGONALLY":
            r, c = cell
            self.empty.update((r + dr, c + dc) for dr in (-1, 1) for dc in (-1, 1))
        if result == "SUNK":
            cluster, frontier = {cell}, [cell]
            while frontier:
                for neighbor in neighbors(frontier.pop()) & self.hits - cluster:
                    cluster.add(neighbor)
                    frontier.append(neighbor)
            self.hits.difference_update(cluster)
            if self.rules["touchRule"] == "NO_TOUCHING_EVEN_DIAGONALLY":
                self.empty.update(set().union(*(neighbors(c, True) for c in cluster)))

    def choose(self) -> tuple[Cell, str]:
        legal = {(r, c) for r in range(self.rules["rows"]) for c in range(self.rules["columns"])} - self.used - self.empty
        targets = set().union(*(neighbors(c) for c in self.hits)) & legal if self.hits else set()
        choices = sorted(targets or legal)
        if not choices:
            # Keep responding if future rules/results contradict our inference.
            choices = sorted({(r, c) for r in range(self.rules["rows"]) for c in range(self.rules["columns"])} - self.used)
        if not choices:
            raise ValueError("No unshot cells remain")
        return self.rng.choice(choices), "target adjacent hit" if targets else "random hunt"




class Density(Baseline):
    """Remaining ship placements, constrained by hits/misses and no-touch halos.

    Independent-ship scores are a heuristic, not an exact fleet posterior.
    """
    def __init__(self, rules: dict, seed: int):
        super().__init__(rules, seed)
        self.remaining = Counter({f["length"]: f["count"] for f in rules["fleet"]})
        self.columns = rules["columns"]
        self.board = {(r, c) for r in range(rules["rows"]) for c in range(self.columns)}
        self.placements = {}
        for length in self.remaining:
            options = set()
            for r, c in sorted(self.board):
                for dr, dc in ((0, 1), (1, 0)):
                    cells = tuple((r+dr*i, c+dc*i) for i in range(length))
                    if set(cells) <= self.board:
                        options.add(cells)
            self.placements[length] = [
                (self.mask(cells), self.mask(set().union(*(neighbors(c, True) for c in cells)) - set(cells)), cells)
                for cells in sorted(options)
            ]

    def mask(self, cells) -> int:
        result = 0
        for r, c in cells:
            if (r, c) in self.board:
                result |= 1 << (r*self.columns+c)
        return result

    def placement_weight(self, mask: int):
        return 1

    def pick(self, available: list) -> tuple[tuple, float]:
        """One sampled placement and its importance factor (uniform: len)."""
        return self.rng.choice(available), len(available)

    def observe(self, event: dict) -> None:
        if event["result"] == "SUNK":
            cell = parse_cell(event["cell"])
            cluster, frontier = {cell}, [cell]
            while frontier:
                for adjacent in (neighbors(frontier.pop()) & self.hits) - cluster:
                    cluster.add(adjacent)
                    frontier.append(adjacent)
            length = len(cluster)
            if self.remaining[length] > 0:
                self.remaining[length] -= 1
        super().observe(event)

    def candidates(self) -> dict:
        blocked = self.mask(self.empty | (self.used-self.hits))
        hits = self.mask(self.hits)
        clusters, unseen = [], set(self.hits)
        while unseen:
            component = {unseen.pop()}
            frontier = list(component)
            while frontier:
                adjacent = neighbors(frontier.pop()) & unseen
                unseen -= adjacent
                component |= adjacent
                frontier.extend(adjacent)
            clusters.append(self.mask(component))
        result = {}
        no_touch = self.rules["touchRule"] == "NO_TOUCHING_EVEN_DIAGONALLY"
        for length, count in self.remaining.items():
            if count <= 0:
                continue
            result[length] = [p for p in self.placements[length]
                if not p[0] & blocked
                and (not no_touch or not p[1] & hits)
                and all(p[0] & cluster in (0, cluster) for cluster in clusters)]
        return result

    def density_choice(self, candidates: dict) -> tuple[Cell, str]:
        legal = self.board - self.used - self.empty
        hunt, target = Counter(), Counter()
        hits = self.mask(self.hits)
        for length, options in candidates.items():
            for mask, _, cells in options:
                weight = self.remaining[length] * self.placement_weight(mask)
                for cell in cells:
                    if cell in legal:
                        hunt[cell] += weight
                        if mask & hits:
                            target[cell] += weight
        scores = target or hunt
        if not scores:
            cell, _ = super().choose()
            return cell, "density fallback: no consistent scored placements"
        best = max(scores.values())
        tied = sorted(c for c, score in scores.items() if abs(score-best) < 1e-12)
        cell = self.rng.choice(tied)
        return cell, f"{'target' if target else 'hunt'} density; score={best:.4f}; remaining={dict(+self.remaining)}"

    def choose(self) -> tuple[Cell, str]:
        return self.density_choice(self.candidates())


class FleetMC(Density):
    """Bounded full-fleet sampling for hunt; density for immediate hit resolution.

    Sequential packing is biased; do not call these estimates exact posterior
    probabilities. Each accepted fleet satisfies all placement/empty constraints.
    """
    def choose(self) -> tuple[Cell, str]:
        candidates = self.candidates()
        if self.hits or self.rules["touchRule"] != "NO_TOUCHING_EVEN_DIAGONALLY":
            return self.density_choice(candidates)
        lengths = sorted((length for length, count in self.remaining.items() for _ in range(count)),
                         reverse=True)
        if not lengths or any(not candidates.get(length) for length in lengths):
            return self.density_choice(candidates)
        return self.fleet_choice(candidates)

    def fleet_choice(self, candidates: dict) -> tuple[Cell, str]:
        """Same bounded sampler, reusable with opponent-specific placements."""
        lengths = sorted((length for length, count in self.remaining.items() for _ in range(count)),
                         reverse=True)
        deadline = time.monotonic() + min(0.08, self.rules["moveTimeoutSeconds"] * 0.12)
        legal = self.board - self.used - self.empty
        occupancy = Counter()
        samples = 0
        attempts = 0
        while samples < 1000 and attempts < 4000 and time.monotonic() < deadline:
            attempts += 1
            blocked, fleet = 0, []
            for length in lengths:
                available = [p for p in candidates[length] if not p[0] & blocked]
                if not available:
                    break
                (mask, halo, cells), _ = self.pick(available)
                blocked |= mask | halo
                fleet.extend(cells)
            else:
                samples += 1
                occupancy.update(c for c in fleet if c in legal)
        if samples < 30 or not occupancy:
            cell, reason = self.density_choice(candidates)
            return cell, f"MC fallback samples={samples}; {reason}"
        best = max(occupancy.values())
        tied = sorted(c for c, score in occupancy.items() if score == best)
        cell = self.rng.choice(tied)
        return cell, f"hunt fleet-mc; samples={samples}; estimated_hit={best/samples:.3f}; remaining={dict(+self.remaining)}"


class FleetMCExact(FleetMC):
    """Universal singleton-endgame experiment, without an opponent-specific prior."""
    def choose(self) -> tuple[Cell, str]:
        if (self.hits or self.rules["touchRule"] != "NO_TOUCHING_EVEN_DIAGONALLY"
                or any(length > 1 for length in +self.remaining) or not self.remaining[1]):
            return super().choose()
        options = sorted((p[2][0], p[0]) for p in self.candidates()[1])
        cells = [cell for cell, _ in options]
        weights = [self.placement_weight(mask) for _, mask in options]
        left = self.remaining[1]
        if not cells:
            return super().choose()
        if left == 1:
            top = max(weights)
            cell = self.rng.choice([c for c, w in zip(cells, weights) if w == top])
            return cell, f"hunt fleet-mc exact single; configurations={len(cells)}; estimated_hit={top/sum(weights):.3f}"
        uniform = all(w == 1 for w in weights)
        conflicts = [sum(1 << j for j, (r, c) in enumerate(cells)
                         if abs(r-cell[0]) <= 1 and abs(c-cell[1]) <= 1) for cell in cells]
        deadline = time.monotonic() + min(0.025, self.rules["moveTimeoutSeconds"] * 0.04)
        calls = 0

        @lru_cache(maxsize=50000)
        def count(mask, k):
            nonlocal calls
            calls += 1
            if calls > 50000 or calls % 128 == 0 and time.monotonic() > deadline:
                raise TimeoutError("Singleton exact budget exhausted")
            if k == 0:
                return 1
            if mask.bit_count() < k:
                return 0
            if k == 1:
                if uniform:
                    return mask.bit_count()
                return sum(weights[j] for j in range(len(cells)) if mask >> j & 1)
            bit = mask & -mask
            i = bit.bit_length() - 1
            return count(mask ^ bit, k) + weights[i] * count(mask & ~conflicts[i], k-1)

        mask = (1 << len(cells)) - 1
        try:
            total = count(mask, left)
            scores = [weights[i] * count(mask & ~conflicts[i], left-1) for i in range(len(cells))]
        except TimeoutError:
            cell, reason = super().choose()
            return cell, f"singleton exact fallback (budget); {reason}"
        if not total:
            cell, reason = super().choose()
            return cell, f"singleton exact fallback (inconsistent); {reason}"
        best = max(scores)
        cell = self.rng.choice([c for c, score in zip(cells, scores) if score == best])
        return cell, f"hunt fleet-mc exact singles; configurations={total}; estimated_hit={best/total:.3f}"


def is_duel(rules: dict) -> bool:
    return rules.get("modifier") == "DUEL" or (
        rules.get("turnAfterHitRule") == "HIT_SPENDS_SHOT" and rules.get("shotsPerTurn", 1) == 1)


def is_musketeers(rules: dict) -> bool:
    return rules.get("modifier") == "THREE_MUSKETEERS"


def is_miss_race(rules: dict) -> bool:
    """Modes where only misses cost tempo: DUEL (one shot per turn) and
    THREE_MUSKETEERS (hits are free, the turn ends after shotsPerTurn misses)."""
    return is_duel(rules) or is_musketeers(rules)


class FleetMCDuel(FleetMCExact):
    """DUEL/THREE_MUSKETEERS: weighted fleet posterior and bounded lookahead over two own shots.

    No assumption about enemy placement habits. Opponent turns between our
    shots are not simulated, so this is a two-shot hit objective, not minimax.
    """
    def choose(self) -> tuple[Cell, str]:
        if (not is_miss_race(self.rules) or self.rules["touchRule"] != "NO_TOUCHING_EVEN_DIAGONALLY"
                or not any(l > 1 for l in +self.remaining)):
            return super().choose()
        deadline = time.monotonic() + min(0.08, self.rules["moveTimeoutSeconds"] * 0.12)
        sample_deadline = time.monotonic() + min(0.05, self.rules["moveTimeoutSeconds"] * 0.075)
        hits = self.mask(self.hits)
        candidates = {length: [p for p in options if p[0] & hits != p[0]]
                      for length, options in self.candidates().items()}
        lengths = sorted((length for length, count in self.remaining.items() for _ in range(count)), reverse=True)
        bank = []
        total_weight, square_weight = 0, 0
        for _ in range(8000):
            if len(bank) >= 512 or time.monotonic() >= sample_deadline:
                break
            blocked, occupied, weight, ships = 0, 0, 1, []
            for length in lengths:
                available = [p for p in candidates[length] if not p[0] & blocked]
                if not available:
                    break
                (mask, halo, _), factor = self.pick(available)
                weight *= factor
                blocked |= mask | halo
                occupied |= mask
                ships.append(mask)
            else:
                if occupied & hits != hits:
                    continue
                bank.append((occupied, tuple(ships), weight))
                total_weight += weight
                square_weight += weight * weight
        effective = total_weight * total_weight / square_weight if square_weight else 0
        if effective < 40:
            cell, reason = super().choose()
            return cell, f"duel fallback samples={len(bank)}; ess={effective:.1f}; {reason}"
        cell, probability, score, considered = self.lookahead(bank, deadline)
        return cell, (f"duel fleet-mc two-shot; samples={len(bank)}; ess={effective:.1f}; "
                      f"estimated_hit={probability:.3f}; expected_two_hits={score:.3f}; candidates={considered}")

    def lookahead(self, bank: list, deadline: float) -> tuple[Cell, float, float, int]:
        legal = self.mask(self.board - self.used - self.empty)
        used = self.mask(self.used)
        total = sum(weight for _, _, weight in bank)
        occupancy = Counter()
        for occupied, _, weight in bank:
            remaining = occupied & legal
            while remaining:
                bit = remaining & -remaining
                occupancy[bit] += weight
                remaining ^= bit
        best = max(occupancy.values())
        candidates = sorted((bit for bit, weight in occupancy.items() if weight >= best-total*0.05),
                            key=lambda bit: (-occupancy[bit], bit))[:6]
        # A valid greedy answer is ready before any lookahead, even at the deadline.
        scores = {}
        for bit in candidates:
            if time.monotonic() >= deadline:
                break
            branches = {}
            complete = True
            for n, (occupied, ships, weight) in enumerate(bank):
                if n % 32 == 0 and time.monotonic() >= deadline:
                    complete = False
                    break
                sunk = next((ship for ship in ships if ship & bit and not ship & ~(used | bit)), 0)
                outcome = ("SUNK", sunk) if sunk else ("HIT" if occupied & bit else "MISS", 0)
                after = legal & ~bit
                if sunk:
                    sunk_cells = [(i//self.columns, i%self.columns) for i in range(self.rules["rows"]*self.columns)
                                  if sunk & (1 << i)]
                    halo = self.mask(set().union(*(neighbors(c, True) for c in sunk_cells)))
                    after &= ~halo
                counts = branches.setdefault(outcome, Counter())
                remaining = occupied & after
                while remaining:
                    another = remaining & -remaining
                    counts[another] += weight
                    remaining ^= another
            if complete:
                scores[bit] = (occupancy[bit] + sum(max(counts.values(), default=0) for counts in branches.values()))/total
        if scores:
            top = max(scores.values())
            tied = sorted(bit for bit, score in scores.items() if abs(score-top) < 1e-12)
            selected = self.rng.choice(tied)
        else:
            selected = self.rng.choice(sorted(bit for bit, weight in occupancy.items() if weight == best))
            top = occupancy[selected]/total
        i = selected.bit_length()-1
        return (i//self.columns, i%self.columns), occupancy[selected]/total, top, len(scores)


class FleetMCDuelV2(FleetMCDuel):
    """DUEL v2: keep two-shot fleet lookahead, prefer checkerboard on ties.

    On the latest ranking boards the one-shot-only experiment was worse than
    FleetMCDuel. This revision keeps the duel sampler/lookahead and only changes
    tie-breaking toward even parity (covers every length>=2 ship on a standard
    grid), including the greedy occupancy fallback when lookahead times out.
    """
    def lookahead(self, bank: list, deadline: float) -> tuple[Cell, float, float, int]:
        legal = self.mask(self.board - self.used - self.empty)
        used = self.mask(self.used)
        total = sum(weight for _, _, weight in bank)
        occupancy = Counter()
        for occupied, _, weight in bank:
            remaining = occupied & legal
            while remaining:
                bit = remaining & -remaining
                occupancy[bit] += weight
                remaining ^= bit
        best = max(occupancy.values())
        candidates = sorted((bit for bit, weight in occupancy.items() if weight >= best - total * 0.05),
                            key=lambda bit: (-occupancy[bit], bit))[:6]
        scores = {}
        for bit in candidates:
            if time.monotonic() >= deadline:
                break
            branches = {}
            complete = True
            for n, (occupied, ships, weight) in enumerate(bank):
                if n % 32 == 0 and time.monotonic() >= deadline:
                    complete = False
                    break
                sunk = next((ship for ship in ships if ship & bit and not ship & ~(used | bit)), 0)
                outcome = ("SUNK", sunk) if sunk else ("HIT" if occupied & bit else "MISS", 0)
                after = legal & ~bit
                if sunk:
                    sunk_cells = [(i // self.columns, i % self.columns)
                                  for i in range(self.rules["rows"] * self.columns)
                                  if sunk & (1 << i)]
                    halo = self.mask(set().union(*(neighbors(c, True) for c in sunk_cells)))
                    after &= ~halo
                counts = branches.setdefault(outcome, Counter())
                remaining = occupied & after
                while remaining:
                    another = remaining & -remaining
                    counts[another] += weight
                    remaining ^= another
            if complete:
                scores[bit] = (occupancy[bit] + sum(max(counts.values(), default=0)
                                                    for counts in branches.values())) / total
        ranked = scores or {bit: weight / total for bit, weight in occupancy.items()}
        selected = self._select(ranked)
        i = selected.bit_length() - 1
        return (i // self.columns, i % self.columns), occupancy[selected] / total, ranked[selected], len(scores)

    def _select(self, ranked: dict[int, float]) -> int:
        top = max(ranked.values())
        return self._parity_pick([bit for bit, score in ranked.items() if abs(score - top) < 1e-12])

    def _parity_pick(self, bits: list[int]) -> int:
        even = [bit for bit in bits
                if ((bit.bit_length() - 1) // self.columns + (bit.bit_length() - 1) % self.columns) % 2 == 0]
        pool = sorted(even or bits)
        return self.rng.choice(pool)

    def choose(self) -> tuple[Cell, str]:
        cell, reason = super().choose()
        if reason.startswith("duel fleet-mc two-shot"):
            return cell, reason.replace("duel fleet-mc two-shot", "duel-v2 two-shot parity", 1)
        if reason.startswith("duel fallback"):
            return cell, reason.replace("duel fallback", "duel-v2 fallback", 1)
        return cell, reason


class FleetMCBigShip(FleetMCDuelV2):
    """duel-v2 plus a largest-ship lattice in hunt mode.

    Every straight segment of length L contains exactly one cell with
    (row + column) % L == k, so firing on one residue class is guaranteed to
    find the longest remaining ship (classic "diagonals for the four-decker").
    Among near-best posterior cells we prefer that lattice; residue k is the class
    with the fewest open cells, i.e. the cheapest one left to cover.
    """
    tolerance = 0.03

    def _select(self, ranked: dict[int, float]) -> int:
        length = max((length for length, count in self.remaining.items() if count > 0), default=0)
        if self.hits or length < 3:
            return super()._select(ranked)
        legal = self.board - self.used - self.empty
        open_cells = Counter((r + c) % length for r, c in legal)
        residue = min(range(length), key=lambda k: (open_cells[k], k))
        top = max(ranked.values())
        lattice = {bit: score for bit, score in ranked.items()
                   if score >= top * (1 - self.tolerance)
                   and sum(divmod(bit.bit_length() - 1, self.columns)) % length == residue}
        if not lattice:
            return super()._select(ranked)
        return super()._select(lattice)

    def choose(self) -> tuple[Cell, str]:
        cell, reason = super().choose()
        return cell, reason.replace("duel-v2", "bigship", 1)


class FleetMCInterior(FleetMCDuelV2):
    """Miss-race hunt + placement biased inland (anti edge-avoiding opponents).

    Ranking boards vs «Низкие ожидания»: our first-8 shots were ~82% on the rim
    while their ships (HIT/SUNK cells) were ~17% edge. Keep duel-v2 two-shot
    lookahead; reweight hunt scores by distance from the rim, then among
    near-best cells prefer depth and even parity. Own placement searches
    interior-first. Targeting with active hits stays on the parent policy.
    """
    tolerance = 0.12
    depth_bonus = 0.55  # multiplicative: score *= 1 + depth_bonus * depth

    def place_fleet(self) -> list[dict]:
        return place(self.rules, self.rng, prefer_interior=True)

    def is_edge(self, cell: Cell) -> bool:
        r, c = cell
        return r in (0, self.rules["rows"] - 1) or c in (0, self.columns - 1)

    def interior_depth(self, cell: Cell) -> int:
        return _interior_depth(cell, self.rules["rows"], self.columns)

    def _reweight(self, scored: dict[Cell, float]) -> dict[Cell, float]:
        return {cell: score * (1.0 + self.depth_bonus * self.interior_depth(cell))
                for cell, score in scored.items()}

    def _pick_hunt(self, scored: dict[Cell, float]) -> Cell:
        weighted = self._reweight(scored)
        top = max(weighted.values())
        near = {cell: score for cell, score in weighted.items() if score >= top * (1 - self.tolerance)}
        # Prefer any non-edge cell still in the near band before rim leftovers.
        inland = {cell: score for cell, score in near.items() if not self.is_edge(cell)}
        pool_scores = inland or near
        best_depth = max(self.interior_depth(cell) for cell in pool_scores)
        deep = [cell for cell in pool_scores if self.interior_depth(cell) == best_depth]
        even = [cell for cell in deep if (cell[0] + cell[1]) % 2 == 0]
        return self.rng.choice(sorted(even or deep))

    def _select(self, ranked: dict[int, float]) -> int:
        if self.hits:
            return super()._select(ranked)
        scored = {}
        for bit, score in ranked.items():
            index = bit.bit_length() - 1
            scored[(index // self.columns, index % self.columns)] = score
        chosen = self._pick_hunt(scored)
        return 1 << (chosen[0] * self.columns + chosen[1])

    def fleet_choice(self, candidates: dict) -> tuple[Cell, str]:
        lengths = sorted((length for length, count in self.remaining.items() for _ in range(count)),
                         reverse=True)
        deadline = time.monotonic() + min(0.08, self.rules["moveTimeoutSeconds"] * 0.12)
        legal = self.board - self.used - self.empty
        occupancy = Counter()
        samples = 0
        attempts = 0
        while samples < 1000 and attempts < 4000 and time.monotonic() < deadline:
            attempts += 1
            blocked, fleet = 0, []
            for length in lengths:
                available = [p for p in candidates[length] if not p[0] & blocked]
                if not available:
                    break
                mask, halo, cells = self.rng.choice(available)
                blocked |= mask | halo
                fleet.extend(cells)
            else:
                samples += 1
                occupancy.update(c for c in fleet if c in legal)
        if samples < 30 or not occupancy:
            cell, reason = self.density_choice(candidates)
            return cell, f"MC fallback samples={samples}; {reason}"
        cell = self._pick_hunt({cell: float(score) for cell, score in occupancy.items()})
        return cell, (f"hunt fleet-mc interior; samples={samples}; "
                      f"estimated_hit={occupancy[cell]/samples:.3f}; depth={self.interior_depth(cell)}; "
                      f"remaining={dict(+self.remaining)}")

    def density_choice(self, candidates: dict) -> tuple[Cell, str]:
        if self.hits:
            return super().density_choice(candidates)
        legal = self.board - self.used - self.empty
        hunt = Counter()
        for length, options in candidates.items():
            weight = self.remaining[length]
            for _, _, cells in options:
                for cell in cells:
                    if cell in legal:
                        hunt[cell] += weight
        if not hunt:
            return super().density_choice(candidates)
        cell = self._pick_hunt({cell: float(score) for cell, score in hunt.items()})
        return cell, (f"hunt density interior; score={hunt[cell]:.4f}; depth={self.interior_depth(cell)}; "
                      f"remaining={dict(+self.remaining)}")

    def choose(self) -> tuple[Cell, str]:
        cell, reason = super().choose()
        if reason.startswith("duel-v2 two-shot"):
            return cell, reason.replace("duel-v2 two-shot", "interior two-shot", 1)
        if reason.startswith("duel-v2 fallback"):
            return cell, reason.replace("duel-v2 fallback", "interior fallback", 1)
        if "interior" in reason:
            return cell, reason
        return cell, reason


def is_schrodinger(rules: dict) -> bool:
    return rules.get("modifier") == "SCHRODINGER_SHIP"


class FleetMCSchrodinger(FleetMCInterior):
    """SCHRODINGER_SHIP: sinks are never announced (HIT replaces SUNK).

    A hit cluster is recorded as sunk once no consistent placement of a
    remaining length strictly contains it; that restores remaining counts,
    halos and the interior hunt of the parent. Open clusters are resolved by a
    weighted fleet sampler where a cluster may already be a whole ship, so
    probing the ends of a probably-finished ship competes with hunting.
    Without the modifier this is exactly fleet-mc-interior.
    """
    min_samples = 30
    min_ess = 20

    def __init__(self, rules: dict, seed: int):
        super().__init__(rules, seed)
        self.schrodinger = is_schrodinger(rules)
        self.inferred_sunk: list[tuple[Cell, ...]] = []

    def observe(self, event: dict) -> None:
        super().observe(event)
        if self.schrodinger and self.rules["touchRule"] == "NO_TOUCHING_EVEN_DIAGONALLY":
            self._infer_sunk()

    def _components(self) -> list[set[Cell]]:
        components, unseen = [], set(self.hits)
        while unseen:
            component = {unseen.pop()}
            frontier = list(component)
            while frontier:
                adjacent = neighbors(frontier.pop()) & unseen
                unseen -= adjacent
                component |= adjacent
                frontier.extend(adjacent)
            components.append(component)
        return components

    def _infer_sunk(self) -> None:
        # Each sink adds a halo and lowers remaining counts, which may close other clusters.
        while self.hits:
            candidates = self.candidates()
            for component in sorted(self._components(), key=min):
                cmask = self.mask(component)
                if not any(mask & cmask == cmask and mask != cmask
                           for options in candidates.values() for mask, _, _ in options):
                    self._mark_sunk(component)
                    break
            else:
                return

    def _mark_sunk(self, component: set[Cell]) -> None:
        cells = tuple(sorted(component))
        self.hits -= component
        if self.remaining[len(cells)] > 0:
            self.remaining[len(cells)] -= 1
        self.empty.update(set().union(*(neighbors(c, True) for c in cells)) - component)
        self.inferred_sunk.append(cells)

    def choose(self) -> tuple[Cell, str]:
        if not self.schrodinger or self.rules["touchRule"] != "NO_TOUCHING_EVEN_DIAGONALLY":
            return super().choose()
        if self.hits:
            result = self._open_cluster_choice()
            if result is not None:
                return result
            cell, reason = super().choose()
            return cell, f"schrodinger fallback; inferred_sunk={len(self.inferred_sunk)}; {reason}"
        cell, reason = super().choose()
        return cell, f"schrodinger inferred_sunk={len(self.inferred_sunk)}; {reason}"

    def _open_cluster_choice(self) -> tuple[Cell, str] | None:
        deadline = time.monotonic() + min(0.08, self.rules["moveTimeoutSeconds"] * 0.12)
        candidates = self.candidates()
        components = [self.mask(c) for c in sorted(self._components(), key=min)]
        hits = self.mask(self.hits)
        covers = [[(length, p) for length, options in candidates.items() for p in options if p[0] & component]
                  for component in components]
        if any(not options for options in covers):
            return None
        free = {length: [p for p in options if not p[0] & hits] for length, options in candidates.items()}
        legal = self.mask(self.board - self.used - self.empty)
        occupancy, complete = Counter(), [0] * len(components)
        total = square = samples = 0
        order = list(range(len(components)))
        for _ in range(6000):
            if samples >= 600 or time.monotonic() >= deadline:
                break
            self.rng.shuffle(order)
            left = Counter(self.remaining)
            blocked = occupied = 0
            weight, exact, ok = 1, [], True
            for i in order:
                if occupied & components[i]:
                    continue  # merged into one ship with an earlier cluster
                available = [(length, p) for length, p in covers[i] if left[length] > 0 and not p[0] & blocked]
                if not available:
                    ok = False
                    break
                weight *= len(available)
                length, (mask, halo, _) = self.rng.choice(available)
                left[length] -= 1
                blocked |= mask | halo
                occupied |= mask
                if mask == components[i]:
                    exact.append(i)
            if not ok:
                continue
            for length in sorted(left.elements(), reverse=True):
                available = [p for p in free[length] if not p[0] & blocked]
                if not available:
                    ok = False
                    break
                weight *= len(available)
                mask, halo, _ = self.rng.choice(available)
                blocked |= mask | halo
                occupied |= mask
            if not ok:
                continue
            samples += 1
            total += weight
            square += weight * weight
            for i in exact:
                complete[i] += weight
            bits = occupied & legal
            while bits:
                bit = bits & -bits
                occupancy[bit] += weight
                bits ^= bit
        effective = total * total / square if square else 0
        if samples < self.min_samples or effective < self.min_ess or not occupancy:
            return None
        probability = {}
        for bit, value in occupancy.items():
            index = bit.bit_length() - 1
            probability[(index // self.columns, index % self.columns)] = value / total
        adjacent = set().union(*(neighbors(c) for c in self.hits))
        target = {cell: p for cell, p in probability.items() if cell in adjacent}
        hunt = {cell: p for cell, p in probability.items() if cell not in adjacent}
        best_target = max(target.values(), default=0.0)
        mode, cell = "target", None
        if hunt and max(hunt.values()) > best_target:
            picked = self._pick_hunt(hunt)
            if probability[picked] > best_target:
                mode, cell = "hunt", picked
        if cell is None:
            if not target:
                return None
            cell = self.rng.choice(sorted(c for c, p in target.items() if abs(p - best_target) < 1e-12))
        done = ",".join(f"{w / total:.2f}" for w in complete)
        return cell, (f"schrodinger {mode}; samples={samples}; ess={effective:.1f}; "
                      f"estimated_hit={probability[cell]:.3f}; open_clusters={len(components)}; "
                      f"p_complete=[{done}]; inferred_sunk={len(self.inferred_sunk)}; "
                      f"remaining={dict(+self.remaining)}")


class FleetMCSchrodingerRim(FleetMCSchrodinger):
    """fleet-mc-schrodinger with rim placement; shooting is unchanged.

    Ranking SCHRODINGER_SHIP logs: our interior fleet (0-1 of 20 cells on the
    rim) met opponents whose first 15 shots were 0-16% rim for most teams.
    Multi-cell ships go on the perimeter in most matches, singles anywhere.
    """
    perimeter_share = 0.8

    def place_fleet(self) -> list[dict]:
        if self.rng.random() < self.perimeter_share:
            try:
                return place(self.rules, self.rng, perimeter_multi=True)
            except (ValueError, TimeoutError):
                pass
        return place(self.rules, self.rng)


class FleetMCSchrodingerRimV2(FleetMCSchrodingerRim):
    """fleet-mc-schrodinger-rim without the interior hunt bias.

    Opponent boards from our logs are close to uniform (rim cells 7.6 vs 7.3
    random); live hunt hit rate was 23% inland vs 26% on the rim, and a replay
    on 163 real boards gave -1.05 ± 0.51 shots. Hunt takes the top MC cells
    (within near_band) with even-parity tie-break; placement stays rim.
    """
    near_band = 0.03

    def _pick_hunt(self, scored: dict[Cell, float]) -> Cell:
        top = max(scored.values())
        near = [cell for cell, score in scored.items() if score >= top * (1 - self.near_band)]
        even = [cell for cell in near if (cell[0] + cell[1]) % 2 == 0]
        return self.rng.choice(sorted(even or near))


def is_hunger(rules: dict) -> bool:
    return rules.get("modifier") == "HUNGER_GAMES"


class FleetMCHunger(FleetMCSchrodingerRimV2):
    """HUNGER_GAMES: maximize hits within shotLimitPerPlayer, not shots to sink.

    Hunt: MC occupancy where a cell of a length-L ship is worth the hit plus
    the follow-up hits it unlocks within the shots left (vs hunting them at
    hunt_rate); with one shot left this is plain P(hit). Targeting is unchanged.
    Placement by opponent: ranking logs, opponent hits in their first 20 shots:
    rim fleet 0.75-4 for most teams vs 7-10 inland; RIM_HUNTERS the opposite
    (Банька Парилка 10.5 rim vs 3.3 inland).
    """
    RIM_HUNTERS = {"Банька Парилка", "История про Минск"}
    hunt_rate = 0.25
    target_misses = 1.0

    def __init__(self, rules: dict, seed: int):
        super().__init__(rules, seed)
        self.shot_limit = rules.get("shotLimitPerPlayer") or 100
        self.shots_left: int | None = None
        self.opponent_team: str | None = None

    def place_fleet(self) -> list[dict]:
        if self.opponent_team in self.RIM_HUNTERS:
            return place(self.rules, self.rng, prefer_interior=True)
        try:
            return place(self.rules, self.rng, perimeter_multi=True)
        except (ValueError, TimeoutError):
            return place(self.rules, self.rng)

    def left(self) -> int:
        return self.shots_left if self.shots_left is not None else max(1, self.shot_limit - self.shots)

    def ship_value(self, length: int) -> float:
        left = self.left()
        if length == 1 or left <= 1:
            return 1.0
        used = min(left - 1, length - 1 + self.target_misses)
        return 1.0 + used * ((length - 1) / (length - 1 + self.target_misses) - self.hunt_rate)

    def fleet_choice(self, candidates: dict) -> tuple[Cell, str]:
        lengths = sorted((length for length, count in self.remaining.items() for _ in range(count)),
                         reverse=True)
        value = {length: self.ship_value(length) for length in set(lengths)}
        deadline = time.monotonic() + min(0.08, self.rules["moveTimeoutSeconds"] * 0.12)
        legal = self.board - self.used - self.empty
        occupancy, hit = Counter(), Counter()
        samples = attempts = 0
        while samples < 1000 and attempts < 4000 and time.monotonic() < deadline:
            attempts += 1
            blocked, fleet = 0, []
            for length in lengths:
                available = [p for p in candidates[length] if not p[0] & blocked]
                if not available:
                    break
                mask, halo, cells = self.rng.choice(available)
                blocked |= mask | halo
                fleet.extend((cell, length) for cell in cells)
            else:
                samples += 1
                for cell, length in fleet:
                    if cell in legal:
                        occupancy[cell] += value[length]
                        hit[cell] += 1
        if samples < 30 or not occupancy:
            cell, reason = self.density_choice(candidates)
            return cell, f"MC fallback samples={samples}; {reason}"
        cell = self._pick_hunt({cell: float(score) for cell, score in occupancy.items()})
        return cell, (f"hunger hunt; samples={samples}; estimated_hit={hit[cell]/samples:.3f}; "
                      f"value={occupancy[cell]/samples:.3f}; shots_left={self.left()}; "
                      f"remaining={dict(+self.remaining)}")

    def density_choice(self, candidates: dict) -> tuple[Cell, str]:
        if self.hits:
            return super().density_choice(candidates)
        legal = self.board - self.used - self.empty
        hunt = Counter()
        for length, options in candidates.items():
            weight = self.remaining[length] * self.ship_value(length)
            for _, _, cells in options:
                for cell in cells:
                    if cell in legal:
                        hunt[cell] += weight
        if not hunt:
            return super().density_choice(candidates)
        cell = self._pick_hunt({cell: float(score) for cell, score in hunt.items()})
        return cell, f"hunger density; score={hunt[cell]:.4f}; shots_left={self.left()}; remaining={dict(+self.remaining)}"


class FleetMCHungerHeat(FleetMCHunger):
    """fleet-mc-hunger with placement against the opponent's own hunt-shot map.

    HUNGER_GAMES logs: opponents re-aimed at our rim fleet (Ранил? Прости 100%
    rim shots, 0/6; Низкие ожидания 49%). The client passes `heat` (expected
    early hunt shots per cell for this team, heat.HeatStore); placement samples
    rim/inland/uniform fleets and picks among the least exposed. `explore` keeps
    a share of uniform fleets so the opponent cannot re-adapt to one pattern.
    Without a map: inland, rim only against CENTER_HUNTERS. SHIELD only when
    the opponent is accurate this match (they re-shoot DEFENDED cells at once).
    """
    uses_heat = True
    CENTER_HUNTERS = {"Солидный состав", "Нейро Мугивары", "Who is wasp", "Хранители роутера"}
    explore = 0.15
    pool_per_family = 120
    top_share = 0.05
    shield_min_accuracy = 0.45

    def __init__(self, rules: dict, seed: int):
        super().__init__(rules, seed)
        self.heat: dict[Cell, float] | None = None
        self.placement_note = ""

    def place_fleet(self) -> list[dict]:
        if self.heat is None:
            rim = self.opponent_team in self.CENTER_HUNTERS
            self.placement_note = f"no heat map; {'rim' if rim else 'inland'}"
            if rim:
                try:
                    return place(self.rules, self.rng, perimeter_multi=True)
                except (ValueError, TimeoutError):
                    pass
            return place(self.rules, self.rng, prefer_interior=True)
        if self.rng.random() < self.explore:
            self.placement_note = "explore uniform"
            return place(self.rules, self.rng)
        deadline = time.monotonic() + min(0.5, self.rules["placementWindowSeconds"] * 0.05)
        pool = []
        families = ({}, {"perimeter_multi": True}, {"prefer_interior": True})
        for _ in range(self.pool_per_family):
            for options in families:
                if time.monotonic() > deadline:
                    break
                try:
                    pool.append(place(self.rules, self.rng, **options))
                except (ValueError, TimeoutError):
                    continue
        if not pool:
            self.placement_note = "heat pool empty; uniform"
            return place(self.rules, self.rng)
        scored = sorted(((self.exposure(ships), n) for n, ships in enumerate(pool)))
        top = scored[:max(1, int(len(scored) * self.top_share))]
        exposure, index = self.rng.choice(top)
        self.placement_note = (f"heat pick exposure={exposure:.2f}; best={scored[0][0]:.2f}; "
                               f"median={scored[len(scored) // 2][0]:.2f}; pool={len(pool)}")
        return pool[index]

    def exposure(self, ships: list[dict]) -> float:
        return sum(self.heat.get(parse_cell(cell), 0.0) for ship in ships for cell in ship["cells"])


class FleetMCHungerV2(FleetMCHungerHeat):
    """Heat placement + fleet-conditioned, two-shot targeting in HUNGER_GAMES.

    Hunt keeps hunger's long-ship value. With open hits, sample complete fleets
    that cover every hit and exclude already-complete ships (SUNK is announced).
    Choose for expected hits over two shots, or plain occupancy on the last shot.
    SHIELD must not consume a shot while we are finishing an enemy ship.
    """
    shield_while_targeting = False
    target_samples = 256
    target_min_ess = 30

    def choose(self) -> tuple[Cell, str]:
        if (is_hunger(self.rules) and self.hits
                and self.rules["touchRule"] == "NO_TOUCHING_EVEN_DIAGONALLY"):
            result = self.target_choice()
            if result is not None:
                return result
            cell, reason = super().choose()
            return cell, f"hunger-v2 target fallback; {reason}"
        return super().choose()

    def target_choice(self) -> tuple[Cell, str] | None:
        started = time.monotonic()
        budget = min(0.08, self.rules["moveTimeoutSeconds"] * 0.12)
        deadline, sample_deadline = started + budget, started + budget * 0.65
        hits = self.mask(self.hits)
        candidates = {length: [p for p in options if p[0] & hits != p[0]]
                      for length, options in self.candidates().items()}
        # Anchor hits before packing unseen ships so rare hit constraints do not
        # spend the whole budget on rejection sampling. Fixed cluster order and
        # labelled ship counts keep the proposal's importance factors explicit.
        components = [self.mask(c) for c in sorted(self._components(), key=min)]
        covers = [[(length, p) for length, options in candidates.items()
                   for p in options if p[0] & component] for component in components]
        free = {length: [p for p in options if not p[0] & hits]
                for length, options in candidates.items()}
        bank, total, square = [], 0, 0
        for _ in range(4000):
            if len(bank) >= self.target_samples or time.monotonic() >= sample_deadline:
                break
            left = Counter(self.remaining)
            blocked = occupied = 0
            ships, weight = [], 1
            for component, options in zip(components, covers):
                if occupied & component:
                    continue  # Disconnected hits can belong to the same ship.
                available = [(length, p) for length, p in options
                             if left[length] > 0 and not p[0] & blocked]
                if not available:
                    break
                counts = [left[length] for length, _ in available]
                weight *= sum(counts)
                length, (mask, halo, _) = self.rng.choices(available, weights=counts, k=1)[0]
                left[length] -= 1
                blocked |= mask | halo
                occupied |= mask
                ships.append(mask)
            else:
                for length in sorted(left.elements(), reverse=True):
                    available = [p for p in free[length] if not p[0] & blocked]
                    if not available:
                        break
                    weight *= len(available)
                    mask, halo, _ = self.rng.choice(available)
                    blocked |= mask | halo
                    occupied |= mask
                    ships.append(mask)
                else:
                    bank.append((occupied, tuple(ships), weight))
                    total += weight
                    square += weight * weight
        effective = total * total / square if square else 0
        if len(bank) < 30 or effective < self.target_min_ess:
            return None
        if self.left() <= 1:
            legal = self.mask(self.board - self.used - self.empty)
            occupancy = Counter()
            for occupied, _, weight in bank:
                bits = occupied & legal
                while bits:
                    bit = bits & -bits
                    occupancy[bit] += weight
                    bits ^= bit
            if not occupancy:
                return None
            selected = self._select({bit: weight / total for bit, weight in occupancy.items()})
            index = selected.bit_length() - 1
            cell, probability = (index // self.columns, index % self.columns), occupancy[selected] / total
            score, considered = probability, 0
        else:
            cell, probability, score, considered = self.lookahead(bank, deadline)
        return cell, (f"hunger-v2 target; samples={len(bank)}; ess={effective:.1f}; "
                      f"estimated_hit={probability:.3f}; expected_hits={score:.3f}; "
                      f"horizon={min(2, self.left())}; candidates={considered}; shots_left={self.left()}")


class FleetMCLowExp(FleetMCDuelV2):
    """Counter-profile for «Низкие ожидания» (Donatello/Raphael/Michelangelo).

    Attack: duel-v2 fleet sampling with an empirical placement prior. PRIOR is
    observed/uniform frequency of a ship's innermost ring (0 rim, 1, 2+) from
    their ships we sank in ranking logs (docs/research/low-expectations).
    Placement: their hunt found our rim ships later for every length, so
    multi-cell ships go on the perimeter in most matches; the rest uniform.
    DISTANCE is held for the small-ship endgame (see BonusBag).
    """
    PRIOR = {4: (0.46, 1.79, 0.93), 3: (0.52, 1.31, 1.51),
             2: (0.63, 1.49, 1.23), 1: (0.79, 1.63, 0.91)}
    perimeter_share = 0.8
    distance_policy = "endgame"

    def __init__(self, rules: dict, seed: int):
        super().__init__(rules, seed)
        rows = rules["rows"]
        self.prior_weights = {}
        for length, options in self.placements.items():
            table = self.PRIOR.get(length, (1.0, 1.0, 1.0))
            for mask, _, cells in options:
                depth = min(_interior_depth(cell, rows, self.columns) for cell in cells)
                self.prior_weights[mask] = table[min(depth, 2)]

    def placement_weight(self, mask: int):
        return self.prior_weights.get(mask, 1.0)

    def pick(self, available: list) -> tuple[tuple, float]:
        weights = [self.prior_weights.get(p[0], 1.0) for p in available]
        return self.rng.choices(available, weights)[0], sum(weights)

    def place_fleet(self) -> list[dict]:
        if self.rng.random() < self.perimeter_share:
            try:
                return place(self.rules, self.rng, perimeter_multi=True)
            except (ValueError, TimeoutError):
                pass
        return place(self.rules, self.rng)

    def distance_probe(self, budget: float = 0.06) -> Cell | None:
        """DISTANCE cell maximizing expected proven-empty cells over sampled fleets."""
        if self.hits or not +self.remaining:
            return None
        candidates = self.candidates()
        lengths = sorted((length for length, count in self.remaining.items() for _ in range(count)), reverse=True)
        if any(not candidates.get(length) for length in lengths):
            return None
        deadline = time.monotonic() + budget
        legal = sorted(self.board - self.used - self.empty)
        fleets = []
        while len(fleets) < 300 and time.monotonic() < deadline:
            blocked, cells, weight = 0, [], 1.0
            for length in lengths:
                available = [p for p in candidates[length] if not p[0] & blocked]
                if not available:
                    break
                (mask, halo, placed), factor = self.pick(available)
                weight *= factor
                blocked |= mask | halo
                cells.extend(placed)
            else:
                fleets.append((cells, weight))
        if len(fleets) < 30:
            return None
        total = sum(weight for _, weight in fleets)
        best, best_value = None, -1.0
        for probe in legal:
            distances = sorted(abs(probe[0]-r) + abs(probe[1]-c) for r, c in legal)
            value = 0.0
            for cells, weight in fleets:
                d = min(abs(probe[0]-r) + abs(probe[1]-c) for r, c in cells)
                # d == 0 forces a sure hit; value it like a few avoided misses.
                value += weight * (3 if d == 0 else sum(1 for x in distances if x < d))
            if value > best_value:
                best, best_value = probe, value
        return best if best_value / total >= 2 else None

    def choose(self) -> tuple[Cell, str]:
        cell, reason = super().choose()
        return cell, f"lowexp prior; {reason}"


class HardEdge(FleetMC):
    """Observed HARD prior: multi-cell ships on the perimeter, singles inside.

    This is an explicit training experiment, not a placement rule. A conflicting
    observation or insufficient model coverage disables it for the whole match.
    """
    def __init__(self, rules: dict, seed: int):
        super().__init__(rules, seed)
        self.model_disabled = None
        if rules.get("modifier") is not None or rules["touchRule"] != "NO_TOUCHING_EVEN_DIAGONALLY":
            self.model_disabled = "unsupported rules/modifier"

    def is_edge(self, cell: Cell) -> bool:
        r, c = cell
        return r in (0, self.rules["rows"]-1) or c in (0, self.columns-1)

    def observe(self, event: dict) -> None:
        cell, result = parse_cell(event["cell"]), event["result"]
        if self.model_disabled is None:
            if result == "HIT" and not self.is_edge(cell):
                self.model_disabled = "multi-cell hit inside"
            elif result == "SUNK":
                cluster, frontier = {cell}, [cell]
                while frontier:
                    adjacent = neighbors(frontier.pop()) & self.hits - cluster
                    cluster |= adjacent
                    frontier.extend(adjacent)
                if len(cluster) == 1 and self.is_edge(cell):
                    self.model_disabled = "single sunk on perimeter"
                elif len(cluster) > 1 and not all(self.is_edge(c) for c in cluster):
                    self.model_disabled = "multi-cell sunk away from perimeter"
        super().observe(event)

    def generic_choice(self) -> tuple[Cell, str]:
        cell, reason = super().choose()
        return cell, f"hard-edge fallback ({self.model_disabled}); {reason}"

    def choose(self) -> tuple[Cell, str]:
        if self.model_disabled is not None:
            return self.generic_choice()
        candidates = {length: [p for p in options
            if (all(not self.is_edge(c) for c in p[2]) if length == 1
                else all(self.is_edge(c) for c in p[2]))]
            for length, options in self.candidates().items()}
        hits = self.mask(self.hits)
        covered = 0
        for options in candidates.values():
            for mask, _, _ in options:
                covered |= mask & hits
        if (not candidates or any(not options for options in candidates.values())
                or hits & ~covered):
            self.model_disabled = "no compatible model placements"
            return self.generic_choice()
        if hits:
            cell, reason = self.density_choice(candidates)
            return cell, f"hard-edge model=perimeter; {reason}"
        if sum(self.remaining.values()) == 1 and self.remaining[1] == 1:
            # Exact equal scores for the last single: avoid MC's maximum noise.
            cell = self.rng.choice(sorted(p[2][0] for p in candidates[1]))
            return cell, "hard-edge hunt single inside; equal model weights; remaining={1: 1}"
        cell, reason = self.fleet_choice(candidates)
        if reason.startswith("MC fallback"):
            self.model_disabled = "insufficient compatible model samples"
            return self.generic_choice()
        return cell, f"hard-edge model=perimeter; {reason}"


class HardQuadrants(HardEdge):
    """HARD experiment: one interior single in each quarter of a 10x10 field."""
    def __init__(self, rules: dict, seed: int):
        super().__init__(rules, seed)
        self.single_sectors: set[int] = set()
        self.quadrants_disabled = None
        if rules["rows"] != 10 or rules["columns"] != 10 or self.remaining[1] != 4:
            self.quadrants_disabled = "unsupported field/fleet"

    def sector(self, cell: Cell) -> int:
        return 2 * (cell[0] >= self.rules["rows"]//2) + (cell[1] >= self.columns//2)

    def observe(self, event: dict) -> None:
        cell = parse_cell(event["cell"])
        if event["result"] == "SUNK" and not (neighbors(cell) & self.hits):
            sector = self.sector(cell)
            if sector in self.single_sectors:
                self.quadrants_disabled = "two singles in one sector"
            self.single_sectors.add(sector)
        super().observe(event)

    def choose(self) -> tuple[Cell, str]:
        if self.model_disabled is not None or self.hits or any(length > 1 and count > 0 for length, count in self.remaining.items()):
            return super().choose()
        if self.quadrants_disabled is not None:
            cell, reason = super().choose()
            return cell, f"hard-quadrants fallback ({self.quadrants_disabled}); {reason}"
        options = self.candidates().get(1, [])
        groups = [[p for p in options if not self.is_edge(p[2][0]) and self.sector(p[2][0]) == sector]
                  for sector in range(4) if sector not in self.single_sectors]
        if len(groups) != self.remaining[1] or not groups or any(not group for group in groups):
            self.quadrants_disabled = "no compatible sector placements"
            return self.choose()
        groups.sort(key=len)
        try:
            occupancy, layouts = self.single_occupancy(groups)
        except TimeoutError:
            self.quadrants_disabled = "exact counting budget exhausted"
            return self.choose()
        if not layouts:
            self.quadrants_disabled = "no compatible sector fleets"
            return self.choose()
        best = max(occupancy.values())
        selected = self.rng.choice(sorted(cell for cell, score in occupancy.items() if score == best))
        return selected, (f"hard-quadrants hunt singles; exact_layouts={layouts}; estimated_hit={best/layouts:.3f}; "
                          f"sectors_left={4-len(self.single_sectors)}; remaining={dict(+self.remaining)}")

    def single_occupancy(self, groups: list) -> tuple[Counter, int]:
        """Exact marginals under the sector prior, with no-touch across borders.

        DP drops blocked bits that cannot affect any remaining sector. Prefix
        multiplicities and suffix counts retain every compatible layout's weight.
        """
        deadline = time.monotonic() + min(0.08, self.rules["moveTimeoutSeconds"]*0.12)
        future = [0] * (len(groups)+1)
        for index in range(len(groups)-1, -1, -1):
            future[index] = future[index+1]
            for mask, _, _ in groups[index]:
                future[index] |= mask

        @lru_cache(maxsize=None)
        def count(index: int, blocked: int) -> int:
            if time.monotonic() > deadline:
                raise TimeoutError("Sector counting budget exhausted")
            if index == len(groups):
                return 1
            return sum(count(index+1, (blocked | halo) & future[index+1])
                       for mask, halo, _ in groups[index] if not mask & blocked)

        layouts = count(0, 0)
        occupancy = Counter()
        prefixes = {0: 1}
        for index, group in enumerate(groups):
            following = Counter()
            for blocked, ways in prefixes.items():
                if time.monotonic() > deadline:
                    raise TimeoutError("Sector marginals budget exhausted")
                for mask, halo, cells in group:
                    if not mask & blocked:
                        next_blocked = (blocked | halo) & future[index+1]
                        suffixes = count(index+1, next_blocked)
                        if suffixes:
                            occupancy[cells[0]] += ways * suffixes
                            following[next_blocked] += ways
            prefixes = following
        return occupancy, layouts


STRATEGIES = {"baseline": Baseline, "density": Density, "fleet-mc": FleetMCExact, "fleet-mc-v1": FleetMC,
              "fleet-mc-duel": FleetMCDuel, "fleet-mc-duel-v2": FleetMCDuelV2,
              "fleet-mc-bigship": FleetMCBigShip, "fleet-mc-interior": FleetMCInterior,
              "fleet-mc-schrodinger": FleetMCSchrodinger, "fleet-mc-schrodinger-rim": FleetMCSchrodingerRim,
              "fleet-mc-schrodinger-rim-v2": FleetMCSchrodingerRimV2,
              "fleet-mc-hunger": FleetMCHunger, "fleet-mc-hunger-heat": FleetMCHungerHeat,
              "fleet-mc-hunger-v2": FleetMCHungerV2,
              "fleet-mc-lowexp": FleetMCLowExp,
              "ranking-auto": FleetMCExact, "ranking-lowexp": FleetMCExact,
              "hard-edge": HardEdge, "hard-quadrants": HardQuadrants}

