BEGIN;

-- ============================================================
-- AUTH SECURITY EVENTS
--
-- Immutable audit trail for security-sensitive authentication
-- actions. Never store credentials, passwords, refresh tokens,
-- OTP codes, verification/reset tokens, or API secrets here.
-- ============================================================

CREATE TABLE IF NOT EXISTS auth_security_events (

    id UUID PRIMARY KEY,

    organization_id UUID NULL
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    user_id UUID NULL
        REFERENCES users(id)
        ON DELETE CASCADE,

    event_type VARCHAR(64)
        NOT NULL,

    ip_address VARCHAR(64),

    user_agent TEXT,

    metadata_json JSONB,

    created_at TIMESTAMPTZ
        NOT NULL
        DEFAULT NOW()
);


CREATE INDEX IF NOT EXISTS
ix_auth_security_events_organization_id
ON auth_security_events(organization_id);


CREATE INDEX IF NOT EXISTS
ix_auth_security_events_user_id
ON auth_security_events(user_id);


CREATE INDEX IF NOT EXISTS
ix_auth_security_events_event_type
ON auth_security_events(event_type);


CREATE INDEX IF NOT EXISTS
ix_auth_security_events_created_at
ON auth_security_events(created_at);


CREATE INDEX IF NOT EXISTS
ix_auth_security_events_org_created_at
ON auth_security_events(
    organization_id,
    created_at DESC
);


CREATE INDEX IF NOT EXISTS
ix_auth_security_events_user_created_at
ON auth_security_events(
    user_id,
    created_at DESC
);


COMMIT;