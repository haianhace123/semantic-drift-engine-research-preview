BEGIN;

-- ============================================================
-- LINEAGE INVALIDATION EXECUTION ATTEMPT TARGET BINDING
--
-- An execution attempt must pin the exact immutable physical
-- target-binding revision that was current while the claim held
-- target authority.
--
-- Later binding publications must not redirect already-claimed
-- execution work to a different physical downstream target.
--
-- The logical target remains derived from the immutable
-- execution intent and invalidation decision. This column stores
-- only the independent physical-routing revision coordinate.
-- ============================================================

ALTER TABLE lineage_invalidation_execution_attempts
    ADD COLUMN binding_revision BIGINT NOT NULL;

ALTER TABLE lineage_invalidation_execution_attempts
    ADD CONSTRAINT ck_lineage_invalidation_execution_attempt_binding_revision_positive
    CHECK (
        binding_revision > 0
    );

COMMENT ON COLUMN
    lineage_invalidation_execution_attempts.binding_revision IS
    'Exact immutable execution-target binding revision pinned while this attempt held target authority.';

COMMIT;
