BEGIN;

ALTER TABLE webhook_subscriptions
    DROP CONSTRAINT ck_webhook_subscriptions_secret_storage;

ALTER TABLE webhook_subscriptions
    DROP COLUMN secret_key;

ALTER TABLE webhook_subscriptions
    ALTER COLUMN secret_ciphertext SET NOT NULL,
    ALTER COLUMN secret_nonce SET NOT NULL,
    ALTER COLUMN secret_encryption_version SET NOT NULL;

ALTER TABLE webhook_subscriptions
    ADD CONSTRAINT ck_webhook_subscriptions_secret_encrypted_storage
    CHECK (
        OCTET_LENGTH(secret_ciphertext) > 0
        AND OCTET_LENGTH(secret_nonce) = 12
        AND secret_encryption_version > 0
    );

COMMIT;