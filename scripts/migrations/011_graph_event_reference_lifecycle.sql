BEGIN;

ALTER TABLE entity_relationships
    DROP CONSTRAINT IF EXISTS entity_relationships_first_event_id_fkey;

ALTER TABLE entity_relationships
    DROP CONSTRAINT IF EXISTS entity_relationships_last_event_id_fkey;

ALTER TABLE entity_relationships
    ALTER COLUMN first_event_id DROP NOT NULL;

ALTER TABLE entity_relationships
    ALTER COLUMN last_event_id DROP NOT NULL;

ALTER TABLE entity_relationships
    ADD CONSTRAINT entity_relationships_first_event_id_fkey
    FOREIGN KEY (first_event_id)
    REFERENCES semantic_drift_events(id)
    ON DELETE SET NULL;

ALTER TABLE entity_relationships
    ADD CONSTRAINT entity_relationships_last_event_id_fkey
    FOREIGN KEY (last_event_id)
    REFERENCES semantic_drift_events(id)
    ON DELETE SET NULL;

COMMIT;