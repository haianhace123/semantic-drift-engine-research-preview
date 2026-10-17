from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.services.lineage_pgvector_invalidation_service import (
    LineagePgVectorInvalidationConfigurationError,
    LineagePgVectorInvalidationPhysicalConfirmationError,
    LineagePgVectorInvalidationProvenanceConflictError,
    confirm_lineage_pgvector_causal_chunk_absent,
)


def _configuration() -> dict[str, str]:
    return {
        "organization_id": "tenant-a",
        "index_external_id": "index-a",
        "collection_name": "tenant-a-index-a",
        "document_id": "chunk-1",
        "document_revision_external_id": "revision-1",
        "source_external_id": "source-a",
    }


def _session(collection, document):
    db = Mock()
    db.execute.side_effect = [
        Mock(one_or_none=Mock(return_value=collection)),
        Mock(one_or_none=Mock(return_value=document)),
    ]
    return db


@pytest.mark.parametrize("document", [None, ("source-a", "revision-2")])
def test_absent_old_revision_is_confirmed(document) -> None:
    db = _session(("collection-id", "tenant-a", "source-a", "index-a"), document)
    confirm_lineage_pgvector_causal_chunk_absent(db, configuration=_configuration())
    assert db.execute.call_count == 2
    assert db.execute.call_args_list[1].args[1] == {
        "collection_id": "collection-id", "document_id": "chunk-1"
    }


def test_stale_revision_still_present_fails_closed() -> None:
    db = _session(
        ("collection-id", "tenant-a", "source-a", "index-a"),
        ("source-a", "revision-1"),
    )
    with pytest.raises(LineagePgVectorInvalidationPhysicalConfirmationError):
        confirm_lineage_pgvector_causal_chunk_absent(db, configuration=_configuration())


@pytest.mark.parametrize(
    "collection",
    [None, ("c", "tenant-b", "source-a", "index-a"),
     ("c", "tenant-a", "source-b", "index-a"),
     ("c", "tenant-a", "source-a", "index-b")],
)
def test_collection_mismatch_fails_before_document_read(collection) -> None:
    db = _session(collection, None)
    with pytest.raises(LineagePgVectorInvalidationProvenanceConflictError):
        confirm_lineage_pgvector_causal_chunk_absent(db, configuration=_configuration())
    assert db.execute.call_count == 1


@pytest.mark.parametrize("bad_revision", [None, "", "  ", "revision-2 "])
def test_untrusted_replacement_revision_metadata_fails_closed(bad_revision) -> None:
    db = _session(
        ("collection-id", "tenant-a", "source-a", "index-a"),
        ("source-a", bad_revision),
    )
    with pytest.raises(LineagePgVectorInvalidationProvenanceConflictError):
        confirm_lineage_pgvector_causal_chunk_absent(db, configuration=_configuration())


def test_foreign_source_row_fails_even_if_revision_differs() -> None:
    db = _session(
        ("collection-id", "tenant-a", "source-a", "index-a"),
        ("source-b", "revision-2"),
    )
    with pytest.raises(LineagePgVectorInvalidationProvenanceConflictError):
        confirm_lineage_pgvector_causal_chunk_absent(db, configuration=_configuration())


@pytest.mark.parametrize("bad", [{"extra": "x"}, {"document_id": " "}])
def test_confirmation_requires_exact_nonblank_pinned_configuration(bad) -> None:
    configuration = _configuration()
    configuration.update(bad)
    db = Mock()
    with pytest.raises(LineagePgVectorInvalidationConfigurationError):
        confirm_lineage_pgvector_causal_chunk_absent(db, configuration=configuration)
    db.execute.assert_not_called()
