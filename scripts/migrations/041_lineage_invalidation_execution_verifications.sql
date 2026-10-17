BEGIN;

-- ============================================================
-- LINEAGE INVALIDATION EXECUTION VERIFICATIONS
--
-- Immutable organization-scoped evidence binding one successful
-- lineage invalidation execution result to the exact downstream
-- materialization observation supplied as post-repair evidence.
--
-- This binding is explicit causal provenance. Timestamps are
-- audit metadata only and are not used to infer that an
-- observation occurred after execution.
--
-- At most one authoritative verification observation may be
-- bound to one execution result. Replaying the same binding is
-- idempotent; conflicting bindings are rejected by the service.
-- ============================================================

ALTER TABLE lineage_invalidation_execution_results
    ADD CONSTRAINT uq_lineage_invalidation_execution_results_org_id
    UNIQUE (
        organization_id,
        id
    );

ALTER TABLE lineage_observations
    ADD CONSTRAINT uq_lineage_observations_org_id
    UNIQUE (
        organization_id,
        id
    );

CREATE TABLE lineage_invalidation_execution_verifications (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL,
    execution_result_id UUID NOT NULL,
    observation_id UUID NOT NULL,

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT lineage_invalidation_execution_verifications_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    CONSTRAINT lineage_invalidation_execution_verifications_result_fkey
        FOREIGN KEY (
            organization_id,
            execution_result_id
        )
        REFERENCES lineage_invalidation_execution_results (
            organization_id,
            id
        )
        ON DELETE CASCADE,

    CONSTRAINT lineage_invalidation_execution_verifications_observation_fkey
        FOREIGN KEY (
            organization_id,
            observation_id
        )
        REFERENCES lineage_observations (
            organization_id,
            id
        )
        ON DELETE CASCADE,

    CONSTRAINT uq_lineage_invalidation_execution_verification_result
        UNIQUE (
            organization_id,
            execution_result_id
        )
);

CREATE INDEX
    ix_lineage_invalidation_execution_verifications_org_observation
ON lineage_invalidation_execution_verifications (
    organization_id,
    observation_id,
    execution_result_id
);

CREATE INDEX
    ix_lineage_invalidation_execution_verifications_org_created_id
ON lineage_invalidation_execution_verifications (
    organization_id,
    created_at ASC,
    id ASC
);

COMMENT ON TABLE lineage_invalidation_execution_verifications IS
    'Immutable explicit evidence binding a successful lineage invalidation execution result to its authoritative post-repair materialization observation.';

COMMENT ON COLUMN lineage_invalidation_execution_verifications.execution_result_id IS
    'Successful terminal execution result whose downstream state is being verified.';

COMMENT ON COLUMN lineage_invalidation_execution_verifications.observation_id IS
    'Exact materialization observation explicitly supplied as post-repair evidence for the execution result.';

COMMENT ON COLUMN lineage_invalidation_execution_verifications.created_at IS
    'Time when the verification binding was durably recorded. Audit metadata only; causal ordering comes from the explicit binding.';

COMMIT;