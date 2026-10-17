BEGIN;

-- ============================================================
-- ENTITY RELATIONSHIP OBSERVATIONS
--
-- Immutable event-level evidence for the temporal entity graph.
--
-- entity_relationships:
--     current/aggregate state of an edge.
--
-- entity_relationship_observations:
--     immutable evidence showing that one semantic drift event
--     observed one directed relationship.
--
-- The unique constraint on relationship_id + event_id makes
-- graph persistence idempotent across worker/API retries.
-- ============================================================

CREATE TABLE IF NOT EXISTS entity_relationship_observations (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    relationship_id UUID NOT NULL
        REFERENCES entity_relationships(id)
        ON DELETE CASCADE,

    semantic_drift_event_id UUID NOT NULL
        REFERENCES semantic_drift_events(id)
        ON DELETE CASCADE,

    source_entity_id UUID NOT NULL
        REFERENCES entities(id)
        ON DELETE CASCADE,

    target_entity_id UUID NOT NULL
        REFERENCES entities(id)
        ON DELETE CASCADE,

    relationship_type VARCHAR(50)
        NOT NULL
        DEFAULT 'mentioned',

    evidence_method VARCHAR(100)
        NOT NULL
        DEFAULT 'deterministic_name_domain_v1',

    observed_at TIMESTAMPTZ
        NOT NULL,

    created_at TIMESTAMPTZ
        NOT NULL
        DEFAULT NOW(),

    CONSTRAINT uq_entity_relationship_observation_event
        UNIQUE (
            relationship_id,
            semantic_drift_event_id
        ),

    CONSTRAINT ck_entity_relationship_observation_no_self_edge
        CHECK (
            source_entity_id <> target_entity_id
        )
);

CREATE INDEX IF NOT EXISTS
ix_entity_relationship_observations_organization_id
ON entity_relationship_observations (organization_id);

CREATE INDEX IF NOT EXISTS
ix_entity_relationship_observations_relationship_id
ON entity_relationship_observations (relationship_id);

CREATE INDEX IF NOT EXISTS
ix_entity_relationship_observations_event_id
ON entity_relationship_observations (semantic_drift_event_id);

CREATE INDEX IF NOT EXISTS
ix_entity_relationship_observations_source_entity_id
ON entity_relationship_observations (source_entity_id);

CREATE INDEX IF NOT EXISTS
ix_entity_relationship_observations_target_entity_id
ON entity_relationship_observations (target_entity_id);

CREATE INDEX IF NOT EXISTS
ix_entity_relationship_observations_org_observed_at
ON entity_relationship_observations (
    organization_id,
    observed_at
);

CREATE INDEX IF NOT EXISTS
ix_entity_relationship_observations_org_source_time
ON entity_relationship_observations (
    organization_id,
    source_entity_id,
    observed_at
);

CREATE INDEX IF NOT EXISTS
ix_entity_relationship_observations_org_target_time
ON entity_relationship_observations (
    organization_id,
    target_entity_id,
    observed_at
);

COMMIT;