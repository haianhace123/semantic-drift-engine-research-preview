BEGIN;

CREATE TABLE worker_operational_states (
    worker_kind VARCHAR(64) NOT NULL,
    worker_instance_id VARCHAR(255) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    last_heartbeat_at TIMESTAMPTZ NOT NULL,
    last_cycle_started_at TIMESTAMPTZ,
    last_cycle_completed_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_failure_at TIMESTAMPTZ,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT worker_operational_states_pkey
        PRIMARY KEY (worker_kind, worker_instance_id),

    CONSTRAINT worker_operational_states_consecutive_failures_check
        CHECK (consecutive_failures >= 0)
);

CREATE INDEX ix_worker_operational_states_last_heartbeat_at
    ON worker_operational_states (last_heartbeat_at);

COMMIT;
