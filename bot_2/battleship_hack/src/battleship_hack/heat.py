"""Per-team maps of where opponents fire their early hunt shots (placement input)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

Cell = tuple[int, int]


def hunt_shots(shots: list[tuple[Cell, str]], window: int) -> list[Cell]:
    """First `window` shots minus follow-ups next to an earlier hit (targeting, not habit)."""
    hits, cells = set(), []
    for (r, c), result in shots[:window]:
        if not {(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)} & hits:
            cells.append((r, c))
        if result in ("HIT", "SUNK"):
            hits.add((r, c))
    return cells


class HeatStore:
    """Recency-weighted hunt-shot frequency per team, shrunk toward the pooled map.

    A cell value is the expected number of that team's hunt shots landing on it
    within their first `window` shots of a match.
    """
    window = 20
    min_shots = 10
    hunger_weight = 3.0
    decay = 0.93
    prior_matches = 3.0

    def __init__(self):
        self.matches: dict[str, list[tuple[float, list[Cell]]]] = defaultdict(list)

    def add(self, team: str | None, shots: list[tuple[Cell, str]], modifier: str | None) -> None:
        if team and len(shots) >= self.min_shots:
            weight = self.hunger_weight if modifier == "HUNGER_GAMES" else 1.0
            self.matches[team].append((weight, hunt_shots(shots, self.window)))

    @classmethod
    def from_logs(cls, logs: Path) -> "HeatStore":
        store = cls()
        for path in sorted(logs.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
            battles = {}
            with path.open() as lines:
                for line in lines:
                    try:
                        record = json.loads(line)
                        kind, payload, battle = record["type"], record["payload"], record.get("battle_id")
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
                    if kind == "BATTLE_STARTED":
                        battles[battle] = ((payload.get("opponent") or {}).get("teamName"),
                                           (payload.get("rules") or {}).get("modifier"), [])
                    elif kind == "OPPONENT_SHOT" and battle in battles:
                        cell = payload["cell"]
                        battles[battle][2].append(((cell["row"], cell["column"]), payload.get("result")))
            for team, modifier, shots in battles.values():
                store.add(team, shots, modifier)
        return store

    def _sums(self, team: str) -> tuple[Counter, float]:
        heat, total = Counter(), 0.0
        entries = self.matches[team]
        for age, (weight, cells) in enumerate(reversed(entries)):
            weight *= self.decay ** age
            total += weight
            for cell in cells:
                heat[cell] += weight
        return heat, total

    def heat_for(self, team: str | None) -> dict[Cell, float] | None:
        sums = {name: self._sums(name) for name in self.matches}
        pooled_total = sum(total for _, total in sums.values())
        if not pooled_total:
            return None
        pooled = Counter()
        for heat, _ in sums.values():
            pooled.update(heat)
        pooled_map = {cell: value / pooled_total for cell, value in pooled.items()}
        if team not in sums:
            return pooled_map
        heat, total = sums[team]
        cells = set(heat) | set(pooled_map)
        return {cell: (heat[cell] + self.prior_matches * pooled_map.get(cell, 0.0)) / (total + self.prior_matches)
                for cell in cells}

    def matches_for(self, team: str | None) -> int:
        return len(self.matches.get(team, ())) if team else 0
