BEGIN;

CREATE TABLE organization_work_schedules (
    organization_id UUID NOT NULL,
    work_kind VARCHAR(64) NOT NULL,
    retry_after TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT organization_work_schedules_pkey
        PRIMARY KEY (organization_id, work_kind),

    CONSTRAINT organization_work_schedules_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE
);

CREATE INDEX ix_organization_work_schedules_retry_after
    ON organization_work_schedules (work_kind, retry_after);

COMMIT;
