from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from src.api.routes.lineage import get_materialization_dependency_diff
from src.core.database import SessionLocal
from src.domain.lineage_observation import LineageObservation, LineageRevisionReference
from src.models.lineage_observation import LineageObservationRecord
from src.models.organization import Organization
from src.services.lineage_observation_persistence_service import persist_lineage_observation
from src.services.lineage_projection_service import (
    load_exact_revision_dependents,
    load_lineage_observation_by_sequence,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Set RUN_DB_INTEGRATION_TESTS=1 to run database integration tests",
)


def _observation(sequence, dependencies):
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="policy-source",
        snapshot_external_id=f"snapshot-{sequence}",
        asset_external_id="policy-index",
        asset_kind="index",
        revision_external_id=f"index-{sequence}",
        sequence=sequence,
        dependencies=tuple(
            LineageRevisionReference(asset_external_id=a,
                                     revision_external_id=r,
                                     dependency_type=t)
            for a, r, t in dependencies
        ),
    )


def test_tenant_scoped_materialization_dependency_lifecycle():
    db = SessionLocal()
    created_orgs = []
    try:
        for _ in range(2):
            suffix = uuid.uuid4().hex[:12]
            org = Organization(
                name=f"transition-test-{suffix}",
                slug=f"transition-test-{suffix}",
                billing_email=f"{suffix}@example.com",
            )
            db.add(org)
            db.flush()
            created_orgs.append(org.id)
        owner, foreign = created_orgs
        observations = (
            _observation(1, (("policy", "v1", "indexed_from"), ("manual", "v1", "cites"))),
            _observation(2, (("policy", "v2", "indexed_from"), ("manual", "v1", "cites"))),
            _observation(3, ()),
        )
        for item in observations:
            persist_lineage_observation(db, organization_id=owner, observation=item)
        db.flush()

        params = dict(
            source_external_id="policy-source",
            asset_external_id="policy-index",
            from_sequence=1,
            to_sequence=2,
        )
        response = get_materialization_dependency_diff(
            **params, api_key=SimpleNamespace(organization_id=owner), db=db,
        )
        assert [(i.asset_external_id, i.revision_external_id, i.dependency_type)
                for i in response.added] == [("policy", "v2", "indexed_from")]
        assert [(i.asset_external_id, i.revision_external_id, i.dependency_type)
                for i in response.removed] == [("policy", "v1", "indexed_from")]
        assert len(response.unchanged) == 1

        later = get_materialization_dependency_diff(
            source_external_id="policy-source",
            asset_external_id="policy-index",
            from_sequence=2, to_sequence=3,
            api_key=SimpleNamespace(organization_id=owner), db=db,
        )
        assert len(later.removed) == 2
        assert later.added == []

        assert load_exact_revision_dependents(
            db, organization_id=owner, source_external_id="policy-source",
            asset_external_id="policy", revision_external_id="v1",
            current_only=True,
        ) == ()
        assert load_exact_revision_dependents(
            db, organization_id=owner, source_external_id="policy-source",
            asset_external_id="policy", revision_external_id="v2",
            current_only=True,
        ) == ()
        assert len(load_exact_revision_dependents(
            db, organization_id=owner, source_external_id="policy-source",
            asset_external_id="policy", revision_external_id="v1",
            current_only=False,
        )) == 1
        assert load_lineage_observation_by_sequence(
            db, organization_id=foreign, source_external_id="policy-source",
            asset_external_id="policy-index", observation_kind="materialization", sequence=1,
        ) is None

        with pytest.raises(HTTPException) as error:
            get_materialization_dependency_diff(
                **params, api_key=SimpleNamespace(organization_id=foreign), db=db,
            )
        assert error.value.status_code == 404

        with pytest.raises(HTTPException) as error:
            get_materialization_dependency_diff(
                source_external_id="policy-source", asset_external_id="policy-index",
                from_sequence=2, to_sequence=2,
                api_key=SimpleNamespace(organization_id=owner), db=db,
            )
        assert error.value.status_code == 422

        assert db.query(LineageObservationRecord).filter(
            LineageObservationRecord.organization_id == owner
        ).count() == 3
    finally:
        db.rollback()
        db.close()
