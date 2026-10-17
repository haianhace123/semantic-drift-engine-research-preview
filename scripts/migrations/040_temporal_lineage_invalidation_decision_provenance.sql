BEGIN;

-- ============================================================
-- TEMPORAL LINEAGE INVALIDATION DECISION PROVENANCE
--
-- Durable evidence that one persisted lineage invalidation
-- decision was produced while analyzing one semantic drift event.
--
-- Decisions remain independently deduplicated immutable policy
-- facts. This table records the temporal event provenance of
-- those facts without copying decision or execution state.
-- ============================================================

ALTER TABLE semantic_drift_events
ADD CONSTRAINT uq_semantic_drift_events_org_id
    UNIQUE (
        organization_id,
        id
    );

CREATE TABLE temporal_lineage_invalidation_decision_provenance (
    organization_id UUID NOT NULL,
    semantic_drift_event_id UUID NOT NULL,
    decision_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_temporal_lineage_invalidation_decision_provenance
        PRIMARY KEY (
            semantic_drift_event_id,
            decision_id
        ),

    CONSTRAINT fk_temporal_lineage_invalidation_provenance_event
        FOREIGN KEY (
            organization_id,
            semantic_drift_event_id
        )
        REFERENCES semantic_drift_events (
            organization_id,
            id
        )
        ON DELETE CASCADE,

    CONSTRAINT fk_temporal_lineage_invalidation_provenance_decision
        FOREIGN KEY (
            organization_id,
            decision_id
        )
        REFERENCES lineage_invalidation_decisions (
            organization_id,
            id
        )
        ON DELETE CASCADE
);

CREATE INDEX ix_temporal_lineage_invalidation_provenance_org_event
ON temporal_lineage_invalidation_decision_provenance (
    organization_id,
    semantic_drift_event_id,
    decision_id
);

CREATE INDEX ix_temporal_lineage_invalidation_provenance_org_decision
ON temporal_lineage_invalidation_decision_provenance (
    organization_id,
    decision_id,
    semantic_drift_event_id
);

COMMENT ON TABLE temporal_lineage_invalidation_decision_provenance IS
'Immutable evidence linking a semantic drift event to each persisted lineage invalidation decision produced while analyzing that event.';

COMMENT ON COLUMN temporal_lineage_invalidation_decision_provenance.semantic_drift_event_id IS
'Semantic drift event whose temporal lineage invalidation analysis produced the persisted decision.';

COMMENT ON COLUMN temporal_lineage_invalidation_decision_provenance.decision_id IS
'Persisted immutable lineage invalidation decision associated with the temporal event.';

COMMIT;