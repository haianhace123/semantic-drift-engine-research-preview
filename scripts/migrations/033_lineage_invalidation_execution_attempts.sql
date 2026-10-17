BEGIN;

-- ============================================================
-- LINEAGE INVALIDATION EXECUTION ATTEMPTS
--
-- Immutable worker-claim facts for authorized lineage
-- invalidation execution intents.
--
-- An execution intent proves that an immutable decision passed
-- operational authority checks at authorization time.
--
-- An execution attempt records a recoverable worker claim made
-- against that intent. A newer attempt supersedes an older
-- attempt as execution fencing authority.
--
-- attempt_number is monotonic within one organization-scoped
-- execution intent. The attempt UUID is the fencing identity.
--
-- claimed_at and lease_expires_at define the bounded claim
-- interval. They do not record execution success or failure.
--
-- Execution results, retry policy, external side effects, and
-- reconciliation state deliberately do not belong here.
-- ============================================================

ALTER TABLE lineage_invalidation_execution_intents
    ADD CONSTRAINT uq_lineage_invalidation_execution_intents_org_id
    UNIQUE (
        organization_id,
        id
    );


CREATE TABLE IF NOT EXISTS lineage_invalidation_execution_attempts (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL,
    intent_id UUID NOT NULL,

    attempt_number BIGINT NOT NULL,

    claimed_at TIMESTAMPTZ NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,

    CONSTRAINT lineage_invalidation_execution_attempts_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    CONSTRAINT lineage_invalidation_execution_attempts_intent_fkey
        FOREIGN KEY (
            organization_id,
            intent_id
        )
        REFERENCES lineage_invalidation_execution_intents (
            organization_id,
            id
        )
        ON DELETE CASCADE,

    CONSTRAINT ck_lineage_invalidation_execution_attempt_number_positive
        CHECK (
            attempt_number > 0
        ),

    CONSTRAINT ck_lineage_invalidation_execution_attempt_lease_window
        CHECK (
            lease_expires_at > claimed_at
        ),

    CONSTRAINT uq_lineage_invalidation_execution_attempt_number
        UNIQUE (
            organization_id,
            intent_id,
            attempt_number
        )
);


CREATE INDEX IF NOT EXISTS
    ix_lineage_invalidation_execution_attempts_org_intent_claimed_id
ON lineage_invalidation_execution_attempts (
    organization_id,
    intent_id,
    claimed_at ASC,
    id ASC
);


COMMENT ON TABLE lineage_invalidation_execution_attempts IS
    'Immutable organization-scoped worker claims for authorized lineage invalidation execution intents.';

COMMENT ON COLUMN lineage_invalidation_execution_attempts.id IS
    'Server-generated execution-attempt identity and fencing token.';

COMMENT ON COLUMN lineage_invalidation_execution_attempts.intent_id IS
    'Exact execution intent claimed by this attempt.';

COMMENT ON COLUMN lineage_invalidation_execution_attempts.attempt_number IS
    'Monotonic execution-attempt ordinal within one organization-scoped execution intent.';

COMMENT ON COLUMN lineage_invalidation_execution_attempts.claimed_at IS
    'Time when this execution claim was durably established.';

COMMENT ON COLUMN lineage_invalidation_execution_attempts.lease_expires_at IS
    'Exclusive end of this attempt claim interval. It is not execution result state.';

COMMIT;