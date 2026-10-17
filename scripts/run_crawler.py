from __future__ import annotations

import logging
import signal

from src.core.config import settings
from src.crawler.scheduler import CrawlScheduler
from src.models.worker_operational_state import (
    WORKER_KIND_CRAWLER,
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
        worker_kind=WORKER_KIND_CRAWLER,
        worker_instance_id=create_worker_instance_id(),
    )
    reporter.started()

    worker = CrawlScheduler(
        poll_seconds=(
            settings.CRAWLER_SCHEDULER_POLL_SECONDS
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
            "Crawler scheduler shutdown requested by signal %s.",
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
        "Crawler scheduler started: poll_seconds=%s.",
        worker.poll_seconds,
    )

    try:
        worker.run()
        heartbeat.raise_if_failed()
    finally:
        heartbeat.stop()

    logger.info(
        "Crawler scheduler stopped."
    )


if __name__ == "__main__":
    main()