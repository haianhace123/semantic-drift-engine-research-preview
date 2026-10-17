BEGIN;

-- ============================================================
-- SCHEMA CONSTRAINT ALIGNMENT
--
-- Align databases upgraded through historical migrations with
-- the canonical 000 baseline schema.
--
-- 1. Promote the existing tenant-domain unique index on
--    entities into a named UNIQUE constraint.
--
-- 2. Ensure verification-code lookup is indexed.
-- ============================================================


-- ============================================================
-- ENTITY TENANT + DOMAIN UNIQUENESS
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_entities_organization_domain'
          AND conrelid = 'entities'::regclass
    ) THEN

        IF EXISTS (
            SELECT 1
            FROM pg_class index_relation
            JOIN pg_namespace namespace
              ON namespace.oid = index_relation.relnamespace
            WHERE index_relation.relkind = 'i'
              AND index_relation.relname = 'uq_entities_organization_domain'
              AND namespace.nspname = 'public'
        ) THEN

            ALTER TABLE entities
            ADD CONSTRAINT uq_entities_organization_domain
            UNIQUE USING INDEX uq_entities_organization_domain;

        ELSE

            ALTER TABLE entities
            ADD CONSTRAINT uq_entities_organization_domain
            UNIQUE (
                organization_id,
                domain
            );

        END IF;

    END IF;
END
$$;


-- ============================================================
-- EMAIL VERIFICATION CODE LOOKUP
-- ============================================================

CREATE INDEX IF NOT EXISTS
ix_users_verification_code_hash
ON users (
    verification_code_hash
);


COMMIT;