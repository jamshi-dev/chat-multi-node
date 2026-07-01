"""Structured (JSON) logging using only the stdlib.

Keeping this dependency-free avoids pulling in structlog for a small service while
still emitting machine-parseable lines that play nicely with log aggregators.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(
    logging.makeLogRecord({}).__dict__.keys()
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Promote any structured extras (logger.info("x", extra={...})) to top level.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn access logs are noisy and unstructured; let our app logs carry signal.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
