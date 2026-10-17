"""
Application logging for Semantic Drift Engine.

The logger emits structured JSON to stdout so container runtimes and
log collectors can index fields without parsing human-formatted text.

Request correlation is propagated through a ContextVar and injected
into every application log record.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any


_request_id: ContextVar[str] = ContextVar(
    "request_id",
    default="-",
)


class RequestContextFilter(logging.Filter):
    """Attach request-scoped fields to every log record."""

    def filter(
        self,
        record: logging.LogRecord,
    ) -> bool:
        record.request_id = _request_id.get()

        return True


class JsonFormatter(logging.Formatter):
    """Render one machine-readable JSON object per log record."""

    RESERVED_ATTRIBUTES = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "request_id",
        "taskName",
    }

    def format(
        self,
        record: logging.LogRecord,
    ) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(
                record,
                "request_id",
                "-",
            ),
        }

        for key, value in record.__dict__.items():
            if (
                key in self.RESERVED_ATTRIBUTES
                or key.startswith("_")
            ):
                continue

            if key in {
                "request_id",
            }:
                continue

            try:
                json.dumps(value)
                payload[key] = value
            except (
                TypeError,
                ValueError,
            ):
                payload[key] = str(value)

        if record.exc_info:
            payload["exception"] = (
                self.formatException(
                    record.exc_info
                )
            )

        if record.stack_info:
            payload["stack"] = (
                self.formatStack(
                    record.stack_info
                )
            )

        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )


def bind_request_id(
    request_id: str,
) -> Token[str]:
    """Bind a request ID to the current execution context."""

    return _request_id.set(
        request_id
    )


def reset_request_id(
    token: Token[str],
) -> None:
    """Restore the previous request context."""

    _request_id.reset(
        token
    )


def current_request_id() -> str:
    """Return the request ID bound to the current context."""

    return _request_id.get()


def setup_logging(
    log_level: str | None = None,
    log_file: str | None = None,
) -> logging.Logger:
    """
    Configure process-wide structured logging.

    Container deployments should collect stdout rather than relying on
    files inside ephemeral container filesystems. `log_file` remains in
    the signature for backwards compatibility but is intentionally not
    used.
    """
    del log_file

    configured_level = (
        log_level
        or os.getenv(
            "LOG_LEVEL",
            "INFO",
        )
    ).upper()

    level = getattr(
        logging,
        configured_level,
        logging.INFO,
    )

    root_logger = logging.getLogger()

    root_logger.setLevel(
        level
    )

    # Uvicorn workers may import the application more than once.
    # Replacing our own handlers avoids duplicated lines without
    # mutating unrelated handlers installed by external libraries.
    for handler in list(
        root_logger.handlers
    ):
        if getattr(
            handler,
            "_semantic_drift_handler",
            False,
        ):
            root_logger.removeHandler(
                handler
            )

            try:
                handler.close()
            except Exception:
                pass

    console_handler = logging.StreamHandler(
        sys.stdout
    )

    console_handler.setLevel(
        level
    )

    console_handler.setFormatter(
        JsonFormatter()
    )

    console_handler.addFilter(
        RequestContextFilter()
    )

    console_handler._semantic_drift_handler = True  # type: ignore[attr-defined]

    root_logger.addHandler(
        console_handler
    )

    logging.getLogger(
        "httpx"
    ).setLevel(
        logging.WARNING
    )

    logging.getLogger(
        "playwright"
    ).setLevel(
        logging.WARNING
    )

    return root_logger