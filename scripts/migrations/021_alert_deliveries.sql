BEGIN;

-- ============================================================
-- ALERT DELIVERY QUEUE
--
-- Alerts are durable intelligence records. External delivery is
-- modeled separately so one Alert can fan out to multiple
-- destinations while each destination owns its own retry,
-- idempotency and delivery lifecycle.
--
-- Initial destination type:
--
--     webhook
--
-- destination_id:
--     Stable identifier of the destination configuration.
--     For webhook deliveries this is WebhookSubscription.id.
--
-- delivery_key:
--     Deterministic SHA-256 identity derived from stable delivery
--     inputs. Production code will derive it from:
--
--         alert_id | destination_type | destination_id
--
--     This prevents duplicate queue rows when fan-out is retried.
--
-- payload:
--     Durable payload snapshot prepared before external I/O.
--     Delivery workers must not depend on mutable Alert content to
--     reconstruct an already queued attempt.
--
-- attempt_count:
--     Number of external delivery attempts already started.
--
-- next_attempt_at:
--     Earliest materialization time at which a pending/retryable
--     delivery may be claimed again.
--
-- delivered_at:
--     Set only after the external destination acknowledges success.
--
-- No external network call belongs in the transaction that creates
-- AlertDelivery rows.
-- ============================================================


CREATE TABLE IF NOT EXISTS alert_deliveries (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL,
    alert_id UUID NOT NULL,

    destination_type VARCHAR(32) NOT NULL,
    destination_id UUID NOT NULL,

    delivery_key VARCHAR(64) NOT NULL,

    status VARCHAR(20) NOT NULL
        DEFAULT 'pending',

    payload JSONB NOT NULL,

    attempt_count INTEGER NOT NULL
        DEFAULT 0,

    next_attempt_at TIMESTAMPTZ,

    last_attempt_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,

    last_error TEXT,

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT NOW(),

    updated_at TIMESTAMPTZ,

    CONSTRAINT alert_deliveries_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    CONSTRAINT alert_deliveries_alert_id_fkey
        FOREIGN KEY (alert_id)
        REFERENCES alerts(id)
        ON DELETE CASCADE,

    CONSTRAINT ck_alert_deliveries_attempt_count
        CHECK (attempt_count >= 0)
);


-- One durable queue row per Alert + destination.
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_alert_deliveries_delivery_key
ON alert_deliveries (
    organization_id,
    delivery_key
);


-- Primary worker claim/retry path.
CREATE INDEX IF NOT EXISTS
    ix_alert_deliveries_status_next_attempt
ON alert_deliveries (
    status,
    next_attempt_at ASC,
    created_at ASC,
    id ASC
);


-- Organization-scoped delivery workspace / diagnostics.
CREATE INDEX IF NOT EXISTS
    ix_alert_deliveries_org_created_at
ON alert_deliveries (
    organization_id,
    created_at DESC,
    id DESC
);


-- Alert -> destination delivery lookup.
CREATE INDEX IF NOT EXISTS
    ix_alert_deliveries_alert
ON alert_deliveries (
    alert_id,
    created_at ASC,
    id ASC
);


COMMIT;