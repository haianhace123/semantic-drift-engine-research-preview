from __future__ import annotations

import uuid

import pytest

from src.domain.lineage_pgvector_collection import (
    sde_pgvector_collection_metadata,
)


def test_collection_identity_metadata_is_tenant_and_index_scoped() -> None:
    organization_id = uuid.uuid4()
    metadata = sde_pgvector_collection_metadata(
        organization_id=organization_id,
        source_external_id=" source-a ",
        index_external_id=" index-a ",
    )
    assert metadata == {
        "sde_organization_id": str(organization_id),
        "sde_source_external_id": "source-a",
        "sde_index_external_id": "index-a",
    }


@pytest.mark.parametrize("field", ["source_external_id", "index_external_id"])
def test_collection_identity_rejects_missing_fields(field: str) -> None:
    values = {
        "organization_id": uuid.uuid4(),
        "source_external_id": "source-a",
        "index_external_id": "index-a",
    }
    values[field] = " "
    with pytest.raises(ValueError):
        sde_pgvector_collection_metadata(**values)


def test_collection_identity_requires_uuid_tenant() -> None:
    with pytest.raises(TypeError):
        sde_pgvector_collection_metadata(
            organization_id="tenant-a",
            source_external_id="source-a",
            index_external_id="index-a",
        )
