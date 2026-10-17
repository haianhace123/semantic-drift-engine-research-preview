BEGIN;

UPDATE webhook_subscriptions
SET created_at = CURRENT_TIMESTAMP
WHERE created_at IS NULL;

ALTER TABLE webhook_subscriptions
    ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP,
    ALTER COLUMN created_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS ix_webhook_subscriptions_org_created_id
    ON webhook_subscriptions (
        organization_id,
        created_at ASC,
        id ASC
    );

COMMIT;
