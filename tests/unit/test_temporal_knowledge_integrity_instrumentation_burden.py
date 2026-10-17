from benchmarks.temporal_knowledge_integrity.instrumentation_burden import (
    MetadataOwnership,
    build_rag_instrumentation_burden,
)


def test_rag_instrumentation_burden_is_explicit() -> None:
    burden = build_rag_instrumentation_burden()

    assert burden.total_fields == 11
    assert burden.customer_required_fields == 3
    assert burden.customer_or_adapter_fields == 4
    assert burden.sde_derived_fields == 4


def test_customer_required_metadata_is_limited_to_causal_identity() -> None:
    burden = build_rag_instrumentation_burden()

    required_fields = {
        (boundary.name, field.name)
        for boundary in burden.boundaries
        for field in boundary.fields
        if field.ownership
        is MetadataOwnership.CUSTOMER_REQUIRED
    }

    assert required_fields == {
        ("chunk", "asset_external_id"),
        ("index", "consumed_chunk_revisions"),
        ("agent", "consumed_index_revision"),
    }


def test_sde_derived_metadata_does_not_require_customer_instrumentation() -> None:
    burden = build_rag_instrumentation_burden()

    derived_fields = {
        (boundary.name, field.name)
        for boundary in burden.boundaries
        for field in boundary.fields
        if field.ownership
        is MetadataOwnership.SDE_DERIVED
    }

    assert derived_fields == {
        ("chunk", "snapshot_external_id"),
        ("chunk", "revision_external_id"),
        ("index", "revision_external_id"),
        ("agent", "revision_external_id"),
    }
