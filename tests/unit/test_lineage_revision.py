import pytest

from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)


def test_lineage_revision_identity_normalizes_required_identities():
    identity = LineageRevisionIdentity(
        source_external_id=" source-a ",
        asset_external_id=" chunk-a ",
        revision_external_id=" chunk-a@v1 ",
    )

    assert identity == LineageRevisionIdentity(
        source_external_id="source-a",
        asset_external_id="chunk-a",
        revision_external_id="chunk-a@v1",
    )


@pytest.mark.parametrize(
    (
        "source_external_id",
        "asset_external_id",
        "revision_external_id",
    ),
    (
        (" ", "chunk-a", "chunk-a@v1"),
        ("source-a", " ", "chunk-a@v1"),
        ("source-a", "chunk-a", " "),
    ),
)
def test_lineage_revision_identity_rejects_empty_required_identity(
    source_external_id,
    asset_external_id,
    revision_external_id,
):
    with pytest.raises(
        ValueError,
        match="must not be empty",
    ):
        LineageRevisionIdentity(
            source_external_id=source_external_id,
            asset_external_id=asset_external_id,
            revision_external_id=revision_external_id,
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "source_external_id",
        "asset_external_id",
        "revision_external_id",
    ),
)
def test_lineage_revision_identity_accepts_maximum_length(
    field_name,
):
    values = {
        "source_external_id": "source-a",
        "asset_external_id": "chunk-a",
        "revision_external_id": "chunk-a@v1",
    }
    values[field_name] = "x" * 512

    identity = LineageRevisionIdentity(**values)

    assert len(getattr(identity, field_name)) == 512


@pytest.mark.parametrize(
    "field_name",
    (
        "source_external_id",
        "asset_external_id",
        "revision_external_id",
    ),
)
def test_lineage_revision_identity_rejects_oversized_identity(
    field_name,
):
    values = {
        "source_external_id": "source-a",
        "asset_external_id": "chunk-a",
        "revision_external_id": "chunk-a@v1",
    }
    values[field_name] = "x" * 513

    with pytest.raises(
        ValueError,
        match="must not exceed 512 characters",
    ):
        LineageRevisionIdentity(**values)
