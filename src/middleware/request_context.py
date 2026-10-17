"""
HTTP request correlation and access logging middleware.

Every request receives a stable request ID that is:

- accepted from X-Request-ID when valid;
- generated when absent;
- attached to application logs;
- returned to clients;
- included in the structured access log.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from typing import Callable

from starlette.types import (
    ASGIApp,
    Message,
    Receive,
    Scope,
    Send,
)

from src.core.logging import (
    bind_request_id,
    reset_request_id,
)


logger = logging.getLogger(
    "semantic_drift.access"
)


_REQUEST_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,128}$"
)


class RequestContextMiddleware:
    """Add request correlation and structured access logging."""

    def __init__(
        self,
        app: ASGIApp,
    ) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(
                scope,
                receive,
                send,
            )

            return

        request_id = self._resolve_request_id(
            scope
        )

        token = bind_request_id(
            request_id
        )

        started_at = time.perf_counter()

        status_code = 500

        async def send_with_request_id(
            message: Message,
        ) -> None:
            nonlocal status_code

            if (
                message["type"]
                == "http.response.start"
            ):
                status_code = int(
                    message["status"]
                )

                headers = list(
                    message.get(
                        "headers",
                        [],
                    )
                )

                headers.append(
                    (
                        b"x-request-id",
                        request_id.encode(
                            "ascii"
                        ),
                    )
                )

                message["headers"] = headers

            await send(
                message
            )

        try:
            await self.app(
                scope,
                receive,
                send_with_request_id,
            )
        except Exception:
            logger.exception(
                "request_failed",
                extra={
                    "method": scope.get(
                        "method"
                    ),
                    "path": scope.get(
                        "path"
                    ),
                    "status_code": 500,
                },
            )

            raise
        finally:
            latency_ms = round(
                (
                    time.perf_counter()
                    - started_at
                )
                * 1000,
                2,
            )

            client = scope.get(
                "client"
            )

            client_ip = (
                client[0]
                if client
                else None
            )

            logger.info(
                "request_completed",
                extra={
                    "method": scope.get(
                        "method"
                    ),
                    "path": scope.get(
                        "path"
                    ),
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "client_ip": client_ip,
                },
            )

            reset_request_id(
                token
            )

    @staticmethod
    def _resolve_request_id(
        scope: Scope,
    ) -> str:
        for raw_name, raw_value in scope.get(
            "headers",
            []
        ):
            if (
                raw_name.lower()
                != b"x-request-id"
            ):
                continue

            try:
                candidate = raw_value.decode(
                    "ascii"
                )
            except UnicodeDecodeError:
                break

            if _REQUEST_ID_PATTERN.fullmatch(
                candidate
            ):
                return candidate

            break

        return uuid.uuid4().hex