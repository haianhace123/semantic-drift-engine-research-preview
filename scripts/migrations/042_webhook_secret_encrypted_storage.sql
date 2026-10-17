BEGIN;

ALTER TABLE webhook_subscriptions
    ADD COLUMN secret_ciphertext BYTEA,
    ADD COLUMN secret_nonce BYTEA,
    ADD COLUMN secret_encryption_version SMALLINT;

ALTER TABLE webhook_subscriptions
    ALTER COLUMN secret_key DROP NOT NULL;

ALTER TABLE webhook_subscriptions
    ADD CONSTRAINT ck_webhook_subscriptions_secret_storage
    CHECK (
        (
            secret_key IS NOT NULL
            AND LENGTH(BTRIM(secret_key)) > 0
            AND secret_ciphertext IS NULL
            AND secret_nonce IS NULL
            AND secret_encryption_version IS NULL
        )
        OR
        (
            secret_key IS NULL
            AND secret_ciphertext IS NOT NULL
            AND OCTET_LENGTH(secret_ciphertext) > 0
            AND secret_nonce IS NOT NULL
            AND OCTET_LENGTH(secret_nonce) = 12
            AND secret_encryption_version IS NOT NULL
            AND secret_encryption_version > 0
        )
    );

COMMIT;