"""Расстановки против HARD по журналам партий: доли побед, выстрелы соперника, сверка модели HARD.

Запуск: PYTHONPATH=. uv run python tools/analyze_matches.py bot_1/matches
Берутся только партии против HARD. Партии с пропущенными ходами (сеть) в сравнении не участвуют.
"""

import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from seabattle.model import Cell, Fleet, Ship, ShotResult
from tools.bench_strategy import play_out
from tools.hard_model import HardModel
from tools.show_matches import SIZE, Match, load

Z_95 = 1.96
HITS_FOR_COMPARISON = 10


def wilson(wins: int, games: int) -> tuple[float, float]:
    if games == 0:
        return 0.0, 1.0
    p = wins / games
    denominator = 1 + Z_95**2 / games
    centre = (p + Z_95**2 / (2 * games)) / denominator
    spread = Z_95 * math.sqrt(p * (1 - p) / games + Z_95**2 / (4 * games**2)) / denominator
    return centre - spread, centre + spread


def fleet_of(match: Match) -> Fleet:
    """Корабли не касаются даже углом, поэтому связные по 8 соседям клетки — один корабль."""
    rest = {Cell(r, c) for r, c in match.fleet}
    ships: list[Ship] = []
    while rest:
        group, frontier = set(), [rest.pop()]
        while frontier:
            cell = frontier.pop()
            group.add(cell)
            for near in cell.neighbors8():
                if near in rest:
                    rest.remove(near)
                    frontier.append(near)
        ships.append(Ship(tuple(sorted(group))))
    return tuple(ships)


def hard_model_shots(match: Match) -> list[tuple[int, int, ShotResult]]:
    shots, _ = play_out(HardModel(), fleet_of(match), 0)
    return [(cell.row, cell.column, result) for cell, result in shots.items()]


def model_agrees(match: Match) -> bool:
    """Модель повторяет все записанные выстрелы HARD, включая результат."""
    predicted = hard_model_shots(match)
    seen = [(r, c, ShotResult(res)) for r, c, res in match.opponent_shots]
    return predicted[: len(seen)] == seen


def shots_until_hits(shots: list[tuple[int, int, ShotResult]], hits: int) -> int | None:
    seen = 0
    for index, (_, _, result) in enumerate(shots, start=1):
        seen += result in (ShotResult.HIT, ShotResult.SUNK)
        if seen == hits:
            return index
    return None


def report_wins(groups: dict[str, list[Match]]) -> None:
    print("вид      n   побед   доля   95% Уилсона      HARD ходит первым: доля побед")  # noqa: T201
    for kind, group in sorted(groups.items()):
        wins = sum(m.outcome == "WIN" for m in group)
        low, high = wilson(wins, len(group))
        parts = []
        for first in (True, False):
            sub = [m for m in group if m.opponent_first is first]
            parts.append(f"{sum(m.outcome == 'WIN' for m in sub)}/{len(sub)}")
        print(  # noqa: T201
            f"{kind:8} {len(group):3} {wins:5} {wins / len(group):6.0%}  "
            f"[{low:4.0%}; {high:4.0%}]      HARD первым {parts[0]}, мы первыми {parts[1]}"
        )


def report_model(groups: dict[str, list[Match]]) -> None:
    print("\nмодель HARD на полных партиях (без обрезки победой):")  # noqa: T201
    for kind, group in sorted(groups.items()):
        totals = [len(hard_model_shots(m)) for m in group]
        reached = [shots_until_hits(hard_model_shots(m), HITS_FOR_COMPARISON) or 0 for m in group]
        print(  # noqa: T201
            f"{kind:8} выстрелов до победы HARD: среднее {statistics.mean(totals):.1f} "
            f"± {statistics.stdev(totals) / math.sqrt(len(totals)):.1f}; "
            f"до {HITS_FOR_COMPARISON} попаданий: {statistics.mean(reached):.1f}"
        )


def opening_heatmap(matches: list[Match], count: int = 10) -> str:
    heat = [[0] * SIZE for _ in range(SIZE)]
    for match in matches:
        for row, column, _ in match.opponent_shots[:count]:
            heat[row][column] += 1
    return "\n".join(" ".join(f"{value:3}" for value in line) for line in heat)


def main() -> None:
    directory = Path(sys.argv[1])
    matches = [m for p in sorted(directory.glob("*-HARD-*.jsonl")) if (m := load(p))]
    usable = [m for m in matches if m.timeouts == 0]
    print(f"партий {len(matches)}, без пропущенных ходов {len(usable)}")  # noqa: T201
    agreed = sum(model_agrees(m) for m in matches)
    print(f"модель HARD повторила записанные выстрелы в {agreed} из {len(matches)} партий")  # noqa: T201
    groups: dict[str, list[Match]] = defaultdict(list)
    for match in usable:
        groups[match.kind].append(match)
    report_wins(groups)
    report_model(groups)
    print("\nпервые 10 выстрелов HARD, все партии:")  # noqa: T201
    print(opening_heatmap(matches))  # noqa: T201


if __name__ == "__main__":
    main()
