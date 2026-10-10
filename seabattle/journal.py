"""Журнал партий: каждое сообщение платформы и бота — строка JSON в файле партии.

Нужен для разбора боёв без доступа к журналам платформы. Сбой записи отключает журнал, но не бота.
"""

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)


class MatchJournal(Protocol):
    def start(self, label: str) -> None: ...

    def record(self, direction: str, text: str) -> None: ...

    def finish(self) -> None: ...


class NullJournal:
    def start(self, label: str) -> None:
        pass

    def record(self, direction: str, text: str) -> None:
        pass

    def finish(self) -> None:
        pass


class DirectoryJournal:
    """Файл на партию в каталоге; хранит не больше `keep` последних файлов."""

    def __init__(self, directory: Path, keep: int = 300) -> None:
        self._directory = directory
        self._keep = keep
        self._file: Any = None
        self._started = 0.0
        self._broken = False

    def start(self, label: str) -> None:
        self.finish()
        if self._broken:
            return
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            path = self._directory / f"{stamp}-{label}.jsonl"
            self._file = path.open("a", encoding="utf-8")
            self._started = time.monotonic()
            self._prune()
        except OSError:
            self._disable()

    def record(self, direction: str, text: str) -> None:
        if self._file is None:
            return
        try:
            try:
                body: Any = json.loads(text)
            except ValueError:
                body = text
            elapsed_ms = round((time.monotonic() - self._started) * 1000)
            line = json.dumps({"ms": elapsed_ms, "dir": direction, "msg": body}, ensure_ascii=False)
            self._file.write(line + "\n")
            self._file.flush()
        except OSError:
            self._disable()

    def finish(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                log.warning("Журнал партии не закрыт", exc_info=True)
            self._file = None

    def _prune(self) -> None:
        files = sorted(self._directory.glob("*.jsonl"))
        for old in files[: max(0, len(files) - self._keep)]:
            old.unlink(missing_ok=True)

    def _disable(self) -> None:
        log.error("Журнал партий отключён из-за ошибки записи", exc_info=True)
        self._broken = True
        self._file = None
