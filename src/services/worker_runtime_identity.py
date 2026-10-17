from __future__ import annotations

import os
import socket
import uuid


def create_worker_instance_id() -> str:
    """
    Create an identity for one worker process incarnation.

    The identity is intentionally process-scoped rather than stable across
    restarts so operational state from separate incarnations cannot overwrite
    each other.
    """

    return (
        f"{socket.gethostname()}-"
        f"{os.getpid()}-"
        f"{uuid.uuid4().hex[:8]}"
    )
