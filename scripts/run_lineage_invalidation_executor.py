from __future__ import annotations

import logging
import signal

from src.core.config import settings
from src.models.worker_operational_state import (
    WORKER_KIND_LINEAGE_INVALIDATION,
)
from src.services.lineage_invalidation_execution_worker import (
    LineageInvalidationExecutionWorker,
)
from src.services.worker_operational_heartbeat import (
    WorkerOperationalHeartbeat,
)
from src.services.worker_operational_reporter import (
    WorkerOperationalReporter,
)
from src.services.worker_runtime_identity import (
    create_worker_instance_id,
)


logger = logging.getLogger(__name__)


def main() -> None:
    reporter = WorkerOperationalReporter(
        worker_kind=WORKER_KIND_LINEAGE_INVALIDATION,
        worker_instance_id=create_worker_instance_id(),
    )
    reporter.started()

    worker = LineageInvalidationExecutionWorker(
        poll_seconds=(
            settings.LINEAGE_INVALIDATION_EXECUTOR_POLL_SECONDS
        ),
        operational_reporter=reporter,
    )

    heartbeat = WorkerOperationalHeartbeat(
        reporter.heartbeat,
        on_failure=worker.stop,
    )
    heartbeat.start()

    def stop_worker(
        signum,
        frame,
    ) -> None:
        del frame

        logger.info(
            "Lineage invalidation executor shutdown requested "
            "by signal %s.",
            signum,
        )

        worker.stop()

    for sig in (
        signal.SIGINT,
        signal.SIGTERM,
    ):
        try:
            signal.signal(
                sig,
                stop_worker,
            )
        except (
            ValueError,
            OSError,
        ):
            pass

    logger.info(
        "Lineage invalidation executor started: poll_seconds=%s.",
        worker.poll_seconds,
    )

    try:
        worker.run()
        heartbeat.raise_if_failed()
    finally:
        heartbeat.stop()

    logger.info(
        "Lineage invalidation executor stopped."
    )


if __name__ == "__main__":
    main()