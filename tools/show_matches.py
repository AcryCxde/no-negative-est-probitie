"""Разбор журналов партий бота: расстановки, куда стрелял соперник, статистика по видам расстановки.

Запуск: uv run python tools/show_matches.py bot_1/matches [сколько последних партий показать]
"""

import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SIZE = 10


@dataclass(frozen=True)
class Match:
    name: str
    fleet: set[tuple[int, int]]
    opponent_shots: list[tuple[int, int, str]]
    my_shots: list[tuple[int, int, str]]
    outcome: str
    timeouts: int
    opponent_first: bool

    @property
    def kind(self) -> str:
        rows = {r for r, _ in self.fleet}
        columns = {c for _, c in self.fleet}
        half = SIZE // 2
        if max(rows, default=0) < half or min(rows, default=SIZE) >= half:
            return "half"
        if max(columns, default=0) < half or min(columns, default=SIZE) >= half:
            return "half"
        return "uniform"


def load(path: Path) -> Match | None:
    fleet: set[tuple[int, int]] = set()
    mine: list[tuple[int, int, str]] = []
    theirs: list[tuple[int, int, str]] = []
    outcome, timeouts = "", 0
    opponent_first: bool | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        entry: dict[str, Any] = json.loads(line)
        msg = entry["msg"]
        if not isinstance(msg, dict):
            continue
        kind = msg.get("type")
        if opponent_first is None and kind in ("OPPONENT_SHOT", "TURN_REQUESTED"):
            opponent_first = kind == "OPPONENT_SHOT"
        if entry["dir"] == "out" and kind == "PLACE":
            fleet = {(c["row"], c["column"]) for ship in msg["ships"] for c in ship["cells"]}
        elif kind == "SHOT_RESOLVED":
            mine.append((msg["cell"]["row"], msg["cell"]["column"], msg["result"]))
        elif kind == "OPPONENT_SHOT":
            theirs.append((msg["cell"]["row"], msg["cell"]["column"], msg["result"]))
        elif kind == "TURN_TIMED_OUT":
            timeouts += 1
        elif kind == "MATCH_FINISHED":
            outcome = msg["outcome"]
    return (
        Match(path.stem, fleet, theirs, mine, outcome, timeouts, bool(opponent_first))
        if fleet and outcome
        else None
    )


def board(match: Match) -> str:
    grid = [["." for _ in range(SIZE)] for _ in range(SIZE)]
    for row, column in match.fleet:
        grid[row][column] = "#"
    for row, column, result in match.opponent_shots:
        grid[row][column] = (
            "X" if result in ("HIT", "SUNK") else ("o" if (row, column) not in match.fleet else "X")
        )
    return "\n".join(" ".join(line) for line in grid)


def main() -> None:
    directory = Path(sys.argv[1])
    show = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    matches = [m for p in sorted(directory.glob("*.jsonl")) if (m := load(p))]
    stats: dict[str, list[Match]] = defaultdict(list)
    for match in matches:
        stats[match.kind].append(match)
    print(f"партий: {len(matches)}")  # noqa: T201
    for kind, group in stats.items():
        wins = sum(1 for m in group if m.outcome == "WIN")
        lost = [m for m in group if m.outcome == "LOSS"]
        theirs = [len(m.opponent_shots) for m in lost]
        print(  # noqa: T201
            f"{kind:8} n={len(group)} win={wins} ({100 * wins // max(len(group), 1)}%) "
            f"наших выстрелов в среднем={sum(len(m.my_shots) for m in group) / len(group):.1f} "
            f"выстрелов соперника в проигранных={sum(theirs) / max(len(theirs), 1):.1f}"
        )
    for match in matches[max(len(matches) - show, 0) :]:
        print(f"\n{match.name} {match.kind} {match.outcome} пропусков={match.timeouts}")  # noqa: T201
        print(board(match))  # noqa: T201


if __name__ == "__main__":
    main()
