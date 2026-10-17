"""
System health and readiness endpoints.

Liveness answers whether the API process is running.
Readiness verifies that critical dependencies are reachable before
traffic should be routed to this instance.
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import APIRouter, Response, status
from redis import Redis
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.core.database import engine
from src.services.schema_compatibility_service import (
    assert_schema_compatible,
    SchemaCompatibilityError,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["system"],
)


def _redis_client() -> Redis:
    return Redis.from_url(
        os.getenv(
            "REDIS_URL",
            "redis://redis:6379/0",
        ),
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )


@router.get(
    "/health",
    summary="Liveness probe",
)
def health() -> dict[str, str]:
    """
    Confirm that the API process is alive.

    This endpoint deliberately avoids dependency checks so container
    orchestrators do not restart a healthy process merely because an
    external dependency is temporarily unavailable.
    """
    return {
        "status": "ok",
        "service": "semantic-drift-api",
    }


@router.get(
    "/ready",
    summary="Readiness probe",
)
def readiness(
    response: Response,
) -> dict[str, object]:
    """
    Verify PostgreSQL and Redis availability, and production schema compatibility.

    A failed readiness probe returns HTTP 503 so reverse proxies and
    orchestrators can stop sending new traffic to this instance.
    """
    started_at = time.perf_counter()

    checks: dict[str, dict[str, object]] = {}

    database_ready = False
    redis_ready = False

    database_started = time.perf_counter()

    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT 1")
            )
            if os.getenv("ENVIRONMENT", "development").lower() == "production":
                assert_schema_compatible(connection)

        database_ready = True

        checks["database"] = {
            "status": "ok",
            "latency_ms": round(
                (
                    time.perf_counter()
                    - database_started
                )
                * 1000,
                2,
            ),
        }
    except SchemaCompatibilityError:
        logger.warning("readiness_schema_incompatible")
        checks["database"] = {"status": "schema_incompatible"}
    except SQLAlchemyError:
        logger.warning("readiness_database_check_failed")
        checks["database"] = {"status": "unavailable"}

    redis_started = time.perf_counter()

    redis_client = _redis_client()

    try:
        redis_client.ping()

        redis_ready = True

        checks["redis"] = {
            "status": "ok",
            "latency_ms": round(
                (
                    time.perf_counter()
                    - redis_started
                )
                * 1000,
                2,
            ),
        }
    except Exception:
        logger.exception(
            "readiness_redis_check_failed"
        )

        checks["redis"] = {
            "status": "unavailable",
        }
    finally:
        redis_client.close()

    ready = (
        database_ready
        and redis_ready
    )

    if not ready:
        response.status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
        )

    return {
        "status": (
            "ready"
            if ready
            else "not_ready"
        ),
        "checks": checks,
        "latency_ms": round(
            (
                time.perf_counter()
                - started_at
            )
            * 1000,
            2,
        ),
    }