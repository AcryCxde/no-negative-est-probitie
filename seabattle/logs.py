"""Журнал: стабильный текст события и поля `key=value` через `extra`."""

import logging
import sys

_STANDARD = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = " ".join(f"{k}={v}" for k, v in record.__dict__.items() if k not in _STANDARD)
        return f"{base} {fields}" if fields else base


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(KeyValueFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)
    # Заголовки рукопожатия с токеном не должны попадать в журнал даже на DEBUG
    logging.getLogger("websockets").setLevel(logging.WARNING)
