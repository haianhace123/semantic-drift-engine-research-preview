BEGIN;

-- ============================================================
-- ROLE MIGRATION
-- Old role: member
-- New role: user
-- ============================================================

ALTER TYPE user_role_enum
RENAME TO user_role_enum_old;

CREATE TYPE user_role_enum AS ENUM (
    'admin',
    'user',
    'viewer'
);

ALTER TABLE users
ALTER COLUMN role DROP DEFAULT;

ALTER TABLE users
ALTER COLUMN role TYPE user_role_enum
USING (
    CASE
        WHEN role::text = 'member'
            THEN 'user'
        ELSE
            role::text
    END
)::user_role_enum;

ALTER TABLE users
ALTER COLUMN role SET DEFAULT 'user';

DROP TYPE user_role_enum_old;


-- ============================================================
-- USER AUTH FIELDS
-- ============================================================

ALTER TABLE users
ADD COLUMN IF NOT EXISTS email_verified
BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS verification_token
VARCHAR(255);

ALTER TABLE users
ADD COLUMN IF NOT EXISTS verification_token_expires
TIMESTAMPTZ;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS verification_code_hash
VARCHAR(64);

ALTER TABLE users
ADD COLUMN IF NOT EXISTS verification_code_expires
TIMESTAMPTZ;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS reset_token
VARCHAR(255);

ALTER TABLE users
ADD COLUMN IF NOT EXISTS reset_token_expires
TIMESTAMPTZ;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS google_id
VARCHAR(255);

ALTER TABLE users
ADD COLUMN IF NOT EXISTS github_id
VARCHAR(255);

ALTER TABLE users
ADD COLUMN IF NOT EXISTS login_attempts
INTEGER NOT NULL DEFAULT 0;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS locked_until
TIMESTAMPTZ;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS refresh_token
VARCHAR(64);

ALTER TABLE users
ADD COLUMN IF NOT EXISTS refresh_token_expires
TIMESTAMPTZ;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS deleted_at
TIMESTAMPTZ;


-- OAuth accounts do not necessarily have passwords.

ALTER TABLE users
ALTER COLUMN password_hash DROP NOT NULL;


-- ============================================================
-- INDEXES
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS
uq_users_google_id
ON users (google_id)
WHERE google_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS
uq_users_github_id
ON users (github_id)
WHERE github_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS
ix_users_verification_token
ON users (verification_token);

CREATE INDEX IF NOT EXISTS
ix_users_verification_code_hash
ON users (verification_code_hash);

CREATE INDEX IF NOT EXISTS
ix_users_reset_token
ON users (reset_token);

CREATE INDEX IF NOT EXISTS
ix_users_refresh_token
ON users (refresh_token);

CREATE INDEX IF NOT EXISTS
ix_users_deleted_at
ON users (deleted_at);


-- ============================================================
-- CONSENT LOG
-- ============================================================

CREATE TABLE IF NOT EXISTS consent_logs (

    id UUID PRIMARY KEY,

    user_id UUID NULL
    REFERENCES users(id)
    ON DELETE CASCADE,

    consent_type VARCHAR(50)
    NOT NULL,

    granted BOOLEAN
    NOT NULL,

    policy_version VARCHAR(50)
    NOT NULL,

    ip_address VARCHAR(64),

    user_agent TEXT,

    metadata_json JSONB,

    created_at TIMESTAMPTZ
    DEFAULT NOW()
);


CREATE INDEX IF NOT EXISTS
ix_consent_logs_user_id
ON consent_logs(user_id);

CREATE INDEX IF NOT EXISTS
ix_consent_logs_consent_type
ON consent_logs(consent_type);

CREATE INDEX IF NOT EXISTS
ix_consent_logs_created_at
ON consent_logs(created_at);


COMMIT;