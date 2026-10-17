BEGIN;

-- ============================================================
-- ENTITY RELATIONSHIP GRAPH V1
--
-- Persists deterministic relationship evidence discovered from
-- semantic drift events.
--
-- Design goals:
-- - organization scoped;
-- - directed source -> target edges;
-- - event provenance retained;
-- - temporal first/last seen timestamps;
-- - deterministic upsert support;
-- - no external graph database required;
-- - suitable foundation for later topology/centrality analysis.
-- ============================================================


CREATE TABLE IF NOT EXISTS entity_relationships (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    source_entity_id UUID NOT NULL
        REFERENCES entities(id)
        ON DELETE CASCADE,

    target_entity_id UUID NOT NULL
        REFERENCES entities(id)
        ON DELETE CASCADE,

    first_event_id UUID NOT NULL
        REFERENCES semantic_drift_events(id)
        ON DELETE CASCADE,

    last_event_id UUID NOT NULL
        REFERENCES semantic_drift_events(id)
        ON DELETE CASCADE,

    relationship_type VARCHAR(50)
        NOT NULL
        DEFAULT 'mentioned',

    evidence_method VARCHAR(100)
        NOT NULL
        DEFAULT 'deterministic_name_domain_v1',

    evidence_count INTEGER
        NOT NULL
        DEFAULT 1,

    strength DOUBLE PRECISION
        NOT NULL
        DEFAULT 1.0,

    confidence DOUBLE PRECISION
        NOT NULL
        DEFAULT 1.0,

    first_seen_at TIMESTAMPTZ
        NOT NULL
        DEFAULT NOW(),

    last_seen_at TIMESTAMPTZ
        NOT NULL
        DEFAULT NOW(),

    is_active BOOLEAN
        NOT NULL
        DEFAULT TRUE,

    created_at TIMESTAMPTZ
        NOT NULL
        DEFAULT NOW(),

    updated_at TIMESTAMPTZ
        NOT NULL
        DEFAULT NOW(),

    CONSTRAINT uq_entity_relationship_edge
        UNIQUE (
            organization_id,
            source_entity_id,
            target_entity_id,
            relationship_type
        ),

    CONSTRAINT ck_entity_relationship_no_self_edge
        CHECK (
            source_entity_id <> target_entity_id
        ),

    CONSTRAINT ck_entity_relationship_evidence_count
        CHECK (
            evidence_count >= 1
        ),

    CONSTRAINT ck_entity_relationship_strength
        CHECK (
            strength >= 0.0
            AND strength <= 1.0
        ),

    CONSTRAINT ck_entity_relationship_confidence
        CHECK (
            confidence >= 0.0
            AND confidence <= 1.0
        ),

    CONSTRAINT ck_entity_relationship_time_order
        CHECK (
            last_seen_at >= first_seen_at
        )
);


CREATE INDEX IF NOT EXISTS
ix_entity_relationships_organization_id
ON entity_relationships (
    organization_id
);


CREATE INDEX IF NOT EXISTS
ix_entity_relationships_source_entity_id
ON entity_relationships (
    source_entity_id
);


CREATE INDEX IF NOT EXISTS
ix_entity_relationships_target_entity_id
ON entity_relationships (
    target_entity_id
);


CREATE INDEX IF NOT EXISTS
ix_entity_relationships_org_source
ON entity_relationships (
    organization_id,
    source_entity_id
);


CREATE INDEX IF NOT EXISTS
ix_entity_relationships_org_target
ON entity_relationships (
    organization_id,
    target_entity_id
);


CREATE INDEX IF NOT EXISTS
ix_entity_relationships_org_active
ON entity_relationships (
    organization_id,
    is_active
);


CREATE INDEX IF NOT EXISTS
ix_entity_relationships_last_seen_at
ON entity_relationships (
    last_seen_at
);


COMMIT;