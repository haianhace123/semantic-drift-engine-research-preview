from __future__ import annotations

import logging
import threading
from collections.abc import Callable


DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 10.0
logger = logging.getLogger(__name__)


class WorkerOperationalHeartbeat:
    """
    Run durable worker heartbeats independently of work-cycle duration.

    The heartbeat thread owns only scheduling. Persistence remains owned
    by WorkerOperationalReporter. A reporting failure stops the heartbeat
    loop and is retained for the process owner to observe. The optional
    on_failure callback requests a cooperative worker shutdown, allowing
    the process owner to surface that failure rather than running silently.
    """

    def __init__(
        self,
        heartbeat: Callable[[], object],
        *,
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        on_failure: Callable[[], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(
                "interval_seconds must be greater than zero"
            )

        self._heartbeat = heartbeat
        self._on_failure = on_failure
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: BaseException | None = None
        self._lock = threading.Lock()

    @property
    def last_error(self) -> BaseException | None:
        with self._lock:
            return self._last_error

    def start(self) -> None:
        if (
            self._thread is not None
            and self._thread.is_alive()
        ):
            raise RuntimeError(
                "worker operational heartbeat is already running"
            )

        self._stop_event.clear()

        with self._lock:
            self._last_error = None

        self._thread = threading.Thread(
            target=self._run,
            name="worker-operational-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        thread = self._thread

        if (
            thread is not None
            and thread is not threading.current_thread()
        ):
            thread.join(
                timeout=self._interval_seconds + 1.0
            )

    def raise_if_failed(self) -> None:
        error = self.last_error

        if error is not None:
            raise RuntimeError(
                "worker operational heartbeat failed"
            ) from error

    def _run(self) -> None:
        while not self._stop_event.wait(
            self._interval_seconds
        ):
            try:
                self._heartbeat()
            except BaseException as exc:
                with self._lock:
                    self._last_error = exc

                self._stop_event.set()
                if self._on_failure is not None:
                    try:
                        self._on_failure()
                    except Exception:
                        logger.exception(
                            "Worker heartbeat failure shutdown callback failed."
                        )
                return
