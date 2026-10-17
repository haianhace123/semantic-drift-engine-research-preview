from __future__ import annotations

import logging
import math
import threading
import time

from src.services.lineage_invalidation_execution_cycle_service import (
    LineageInvalidationExecutionCycleStatus,
    run_lineage_invalidation_execution_cycle,
)
from src.services.worker_operational_reporter import (
    WorkerOperationalReporter,
)


logger = logging.getLogger(__name__)


class LineageInvalidationExecutionWorkerConfigurationError(
    ValueError
):
    pass


class LineageInvalidationExecutionWorker:
    def __init__(
        self,
        *,
        poll_seconds: float,
        operational_reporter: WorkerOperationalReporter | None = None,
    ) -> None:
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise LineageInvalidationExecutionWorkerConfigurationError(
                "poll_seconds must be finite and greater than zero"
            )

        self.poll_seconds = poll_seconds
        self.operational_reporter = operational_reporter
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        while not self.stop_event.is_set():
            if self.operational_reporter is not None:
                self.operational_reporter.cycle_started()

            started_at = time.perf_counter()

            try:
                result = run_lineage_invalidation_execution_cycle()
            except Exception as exc:
                if self.operational_reporter is not None:
                    self.operational_reporter.cycle_failed(exc)

                duration_ms = round(
                    (time.perf_counter() - started_at) * 1000,
                    3,
                )
                logger.exception(
                    "Lineage invalidation execution cycle failed: "
                    "duration_ms=%s.",
                    duration_ms,
                )

                if self.stop_event.is_set():
                    break

                self.stop_event.wait(
                    self.poll_seconds
                )
                continue

            if self.operational_reporter is not None:
                self.operational_reporter.cycle_succeeded()

            duration_ms = round(
                (time.perf_counter() - started_at) * 1000,
                3,
            )

            if (
                result.status
                == LineageInvalidationExecutionCycleStatus.SUCCEEDED
            ):
                logger.info(
                    "Lineage invalidation execution cycle completed: "
                    "status=%s duration_ms=%s organization_id=%s "
                    "intent_id=%s attempt_id=%s.",
                    result.status.value,
                    duration_ms,
                    (
                        result.candidate.organization_id
                        if result.candidate is not None
                        else None
                    ),
                    (
                        result.candidate.intent_id
                        if result.candidate is not None
                        else None
                    ),
                    result.attempt_id,
                )
            elif (
                result.status
                == LineageInvalidationExecutionCycleStatus.SKIPPED
            ):
                logger.info(
                    "Lineage invalidation execution cycle skipped: "
                    "status=%s duration_ms=%s reason_code=%s "
                    "organization_id=%s intent_id=%s.",
                    result.status.value,
                    duration_ms,
                    result.reason_code,
                    (
                        result.candidate.organization_id
                        if result.candidate is not None
                        else None
                    ),
                    (
                        result.candidate.intent_id
                        if result.candidate is not None
                        else None
                    ),
                )
            elif (
                result.status
                == LineageInvalidationExecutionCycleStatus.FAILED
            ):
                logger.error(
                    "Lineage invalidation execution cycle completed: "
                    "status=%s duration_ms=%s reason_code=%s "
                    "organization_id=%s intent_id=%s attempt_id=%s.",
                    result.status.value,
                    duration_ms,
                    result.reason_code,
                    (
                        result.candidate.organization_id
                        if result.candidate is not None
                        else None
                    ),
                    (
                        result.candidate.intent_id
                        if result.candidate is not None
                        else None
                    ),
                    result.attempt_id,
                )

            if self.stop_event.is_set():
                break

            if result.status in (
                LineageInvalidationExecutionCycleStatus.IDLE,
                LineageInvalidationExecutionCycleStatus.SKIPPED,
            ):
                self.stop_event.wait(
                    self.poll_seconds
                )