BEGIN;

ALTER TABLE entities
ADD COLUMN IF NOT EXISTS organization_id uuid;

WITH ownership AS (
    SELECT
        entity_id,
        MIN(organization_id::text)::uuid AS organization_id,
        COUNT(DISTINCT organization_id) AS organization_count
    FROM monitored_pages
    GROUP BY entity_id
)
UPDATE entities AS entity
SET organization_id = ownership.organization_id
FROM ownership
WHERE ownership.entity_id = entity.id
  AND entity.organization_id IS NULL
  AND ownership.organization_count = 1;

DO $$
DECLARE
    ambiguous_entity_count integer;
    missing_organization_count integer;
BEGIN
    SELECT COUNT(*)
    INTO ambiguous_entity_count
    FROM (
        SELECT entity_id
        FROM monitored_pages
        GROUP BY entity_id
        HAVING COUNT(DISTINCT organization_id) > 1
    ) AS ambiguous_entities;

    IF ambiguous_entity_count > 0 THEN
        RAISE EXCEPTION
            'Entity tenant migration aborted: % entities are referenced by multiple organizations.',
            ambiguous_entity_count;
    END IF;

    SELECT COUNT(*)
    INTO missing_organization_count
    FROM entities
    WHERE organization_id IS NULL;

    IF missing_organization_count > 0 THEN
        RAISE EXCEPTION
            'Entity tenant migration aborted: % entities could not be assigned to an organization.',
            missing_organization_count;
    END IF;
END
$$;

ALTER TABLE entities
ALTER COLUMN organization_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'entities_organization_id_fkey'
    ) THEN
        ALTER TABLE entities
        ADD CONSTRAINT entities_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE;
    END IF;
END
$$;

DROP INDEX IF EXISTS ix_entities_domain;

CREATE INDEX IF NOT EXISTS ix_entities_organization_id
ON entities (organization_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_organization_domain
ON entities (organization_id, domain);

COMMIT;