"""Диспетчер партии: сообщения платформы → решения стратегии → команды бота.

Не знает ни про сокеты (работает через `Transport`), ни про тактику (работает через `Strategy`).
Любой сбой стратегии заменяется допустимым запасным ответом: бот не теряет ход и не падает.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from random import Random
from typing import Final, Protocol, assert_never

from seabattle.model import BotMode, BuiltInBot, Cell, Fleet, RuleModifier
from seabattle.placement import PlacementError, random_fleet, validate_fleet
from seabattle.protocol import (
    ChangeMode,
    CommandRejected,
    Inbound,
    MatchFinished,
    MatchSnapshot,
    MatchStarted,
    ModeChanged,
    ModeRejected,
    OpponentShot,
    OpponentTurnTimedOut,
    Outbound,
    Place,
    PlacementAccepted,
    PlacementRejected,
    ProtocolError,
    Shoot,
    ShotRejected,
    ShotResolved,
    TurnRequested,
    TurnTimedOut,
    Unhandled,
    encode_outbound,
    parse_inbound,
)
from seabattle.state import MatchState
from seabattle.strategy import Strategy, fallback_shot

log = logging.getLogger(__name__)

MAX_PLACEMENT_ATTEMPTS: Final = 5
# Доли moveTimeoutSeconds: к SOFT стратегия должна ответить сама, на HARD ядро её уже не ждёт.
SOFT_DEADLINE_SHARE: Final = 0.4
HARD_DEADLINE_SHARE: Final = 0.6


class TransportClosedError(Exception):
    """Соединение закрыто; `code` — код закрытия WebSocket, если он известен."""

    def __init__(self, code: int | None) -> None:
        super().__init__(code)
        self.code = code


class Transport(Protocol):
    async def recv(self) -> str: ...

    async def send(self, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ModeRequest:
    mode: BotMode
    opponent: BuiltInBot | None


class BotRunner:
    """Живёт дольше соединения: состояние партии переживает обрыв и перестраивается из снимка."""

    def __init__(self, strategy: Strategy, mode: ModeRequest, rng: Random | None = None) -> None:
        self._strategy = strategy
        self._mode = mode
        self._rng = rng or Random()
        self._match: MatchState | None = None

    async def serve(self, transport: Transport) -> None:
        """Обслуживает одно соединение до его закрытия (`TransportClosedError`)."""
        # Новое соединение стартует в RANKING, поэтому режим объявляем при каждом подключении
        await self._send(transport, ChangeMode(self._mode.mode, self._mode.opponent))
        while True:
            raw = await transport.recv()
            try:
                message = parse_inbound(raw)
            except ProtocolError:
                log.warning("Сообщение платформы не разобрано", exc_info=True)
                continue
            try:
                await self._handle(transport, message)
            except TransportClosedError:
                raise
            except Exception:  # граница сообщения: сбой одного обработчика не должен ронять бота
                log.exception("Сообщение не обработано", extra={"kind": type(message).__name__})

    async def _handle(self, transport: Transport, message: Inbound) -> None:  # noqa: PLR0912
        match message:
            case MatchStarted(rules=rules, opponent=opponent):
                self._match = MatchState(rules)
                log.info(
                    "Партия началась", extra={"opponent": opponent, "modifier": rules.modifier}
                )
                await self._place(transport)
            case MatchSnapshot() as snapshot:
                self._match = MatchState(snapshot.rules)
                self._match.restore(
                    snapshot.ships, snapshot.shots, snapshot.opponent_shots, snapshot.shots_left
                )
                log.info("Состояние партии восстановлено", extra={"shots": len(snapshot.shots)})
                if snapshot.ships is None:
                    await self._place(transport)
            case PlacementRejected(reason=reason):
                log.warning("Расстановка отклонена", extra={"reason": reason})
                await self._place(transport)
            case PlacementAccepted():
                log.info("Расстановка принята")
            case TurnRequested() as turn:
                await self._shoot(transport, turn)
            case ShotResolved(shot=shot):
                if self._match is not None:
                    self._match.record_shot(shot)
            case OpponentShot(shot=shot):
                if self._match is not None:
                    self._match.record_opponent_shot(shot)
            case ShotRejected(cell=cell, reason=reason):
                log.warning("Выстрел отклонён", extra={"cell": cell, "reason": reason})
            case TurnTimedOut(move_number=number):
                log.warning("Ход пропущен по времени", extra={"move_number": number})
            case OpponentTurnTimedOut(move_number=number):
                log.info("Соперник пропустил ход", extra={"move_number": number})
            case MatchFinished(outcome=outcome, end_reason=reason):
                log.info("Партия окончена", extra={"outcome": outcome, "end_reason": reason})
                self._match = None
            case ModeChanged(mode=mode):
                log.info("Режим принят", extra={"mode": mode})
            case ModeRejected(mode=mode, reason=reason):
                log.error("Режим отклонён", extra={"mode": mode, "reason": reason})
            case CommandRejected(command=command, reason=reason):
                log.warning("Команда отклонена", extra={"command": command, "reason": reason})
            case Unhandled(kind=kind):
                log.debug("Сообщение пропущено", extra={"kind": kind})
            case _:
                assert_never(message)

    async def _place(self, transport: Transport) -> None:
        match = self._match
        if match is None or match.rules.modifier is RuleModifier.SPEEDRUN:
            return  # в «Спидране» флот расставляет платформа
        if match.placement_attempts >= MAX_PLACEMENT_ATTEMPTS:
            log.error("Лимит попыток расстановки исчерпан")
            return
        match.placement_attempts += 1
        fleet = self._plan_fleet(match)
        match.fleet = fleet
        await self._send(transport, Place(fleet))

    def _plan_fleet(self, match: MatchState) -> Fleet:
        # Первая попытка — стратегия, повторные (после отказа платформы) — ядро
        if match.placement_attempts == 1:
            try:
                fleet = self._strategy.place(match.rules, self._rng)
                validate_fleet(match.rules, fleet)
            except Exception:  # граница плагина: любой сбой стратегии заменяем запасным ответом
                log.exception("Стратегия не смогла расставить флот, берём случайную расстановку")
            else:
                return fleet
        try:
            return random_fleet(match.rules, self._rng)
        except PlacementError:
            log.exception("Запасная расстановка не найдена")
            raise

    async def _shoot(self, transport: Transport, turn: TurnRequested) -> None:
        match = self._match
        if match is None:
            log.error("Ход запрошен вне известной партии", extra={"move_number": turn.move_number})
            return
        started = time.monotonic()
        view = match.view(turn.shots_left, turn.shots_left_in_turn)
        timeout = match.rules.move_timeout_seconds
        cell = fallback_shot(view, self._rng)
        reason: str | None = None
        try:
            chosen = await asyncio.wait_for(
                asyncio.to_thread(
                    self._strategy.choose_shot,
                    view,
                    Random(self._rng.random()),
                    started + timeout * SOFT_DEADLINE_SHARE,
                ),
                timeout=timeout * HARD_DEADLINE_SHARE,
            )
        except TimeoutError:
            reason = "timeout"
        except Exception:  # граница плагина: сбой стратегии не должен стоить хода
            log.exception("Стратегия упала при выборе выстрела")
            reason = "error"
        else:
            if _is_legal(match, chosen):
                cell = chosen
            else:
                reason = "illegal"
        if reason is not None:
            log.warning(
                "Запасной выстрел", extra={"reason": reason, "move_number": turn.move_number}
            )
        await self._send(transport, Shoot(cell))

    async def _send(self, transport: Transport, message: Outbound) -> None:
        await transport.send(encode_outbound(message))


def _is_legal(match: MatchState, cell: Cell) -> bool:
    return match.rules.contains(cell) and cell not in match.shots
