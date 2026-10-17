from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from src.core.database import SessionLocal
from src.services.worker_operational_health_service import (
    record_worker_cycle_failed,
    record_worker_cycle_started,
    record_worker_cycle_succeeded,
    record_worker_heartbeat,
    register_worker_started,
)


class WorkerOperationalReporter:
    """
    Process-facing adapter for durable worker operational state.

    State-transition semantics remain owned by
    worker_operational_health_service. This adapter owns only database session
    lifecycle and transaction completion for runtime reporting.
    """

    def __init__(
        self,
        *,
        worker_kind: str,
        worker_instance_id: str,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self.worker_kind = worker_kind
        self.worker_instance_id = worker_instance_id
        self._session_factory = session_factory

    def _write(
        self,
        operation,
        **kwargs,
    ) -> datetime:
        db = self._session_factory()

        try:
            recorded_at = operation(
                db,
                worker_kind=self.worker_kind,
                worker_instance_id=self.worker_instance_id,
                **kwargs,
            )
            db.commit()
            return recorded_at

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()

    def started(
        self,
        *,
        now: datetime | None = None,
    ) -> datetime:
        return self._write(
            register_worker_started,
            now=now,
        )

    def heartbeat(
        self,
        *,
        now: datetime | None = None,
    ) -> datetime:
        return self._write(
            record_worker_heartbeat,
            now=now,
        )

    def cycle_started(
        self,
        *,
        now: datetime | None = None,
    ) -> datetime:
        return self._write(
            record_worker_cycle_started,
            now=now,
        )

    def cycle_succeeded(
        self,
        *,
        now: datetime | None = None,
    ) -> datetime:
        return self._write(
            record_worker_cycle_succeeded,
            now=now,
        )

    def cycle_failed(
        self,
        error: object,
        *,
        now: datetime | None = None,
    ) -> datetime:
        return self._write(
            record_worker_cycle_failed,
            error=error,
            now=now,
        )
