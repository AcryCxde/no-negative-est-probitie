"""Состояние идущей партии, восстанавливаемое из событий платформы.

Состояние строится только из `MATCH_STARTED`/`MATCH_SNAPSHOT` и последующих событий, поэтому
после переподключения оно совпадает с состоянием без обрыва.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from seabattle.model import Cell, Fleet, Rules, ShotResult
from seabattle.protocol import ShotRecord


@dataclass(frozen=True, slots=True)
class ShotView:
    """Неизменяемый срез партии для стратегии: безопасно отдавать в другой поток."""

    rules: Rules
    shots: Mapping[Cell, ShotResult]
    opponent_shots: Mapping[Cell, ShotResult]
    shots_left: int
    shots_left_in_turn: int


@dataclass(slots=True)
class MatchState:
    rules: Rules
    fleet: Fleet | None = None
    placement_attempts: int = 0
    shots_left: int = 0
    shots: dict[Cell, ShotResult] = field(default_factory=dict)
    opponent_shots: dict[Cell, ShotResult] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.shots_left = self.rules.shot_limit_per_player

    def record_shot(self, shot: ShotRecord) -> None:
        _record(self.shots, shot)

    def record_opponent_shot(self, shot: ShotRecord) -> None:
        _record(self.opponent_shots, shot)

    def restore(
        self,
        fleet: Fleet | None,
        shots: Iterable[ShotRecord],
        opponent_shots: Iterable[ShotRecord],
        shots_left: int,
    ) -> None:
        self.fleet = fleet
        self.shots_left = shots_left
        self.shots.clear()
        self.opponent_shots.clear()
        for shot in shots:
            self.record_shot(shot)
        for shot in opponent_shots:
            self.record_opponent_shot(shot)

    def view(self, shots_left: int, shots_left_in_turn: int) -> ShotView:
        return ShotView(
            rules=self.rules,
            shots=MappingProxyType(dict(self.shots)),
            opponent_shots=MappingProxyType(dict(self.opponent_shots)),
            shots_left=shots_left,
            shots_left_in_turn=shots_left_in_turn,
        )


def _record(target: dict[Cell, ShotResult], shot: ShotRecord) -> None:
    # Щит отбивает попадание: клетка остаётся необстрелянной
    if shot.result is not ShotResult.DEFENDED:
        target[shot.cell] = shot.result
