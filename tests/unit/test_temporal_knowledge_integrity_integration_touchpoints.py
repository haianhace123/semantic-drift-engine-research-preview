from benchmarks.temporal_knowledge_integrity.integration_touchpoints import (
    TouchpointBurden,
    build_rag_touchpoint_report,
)


def test_rag_integration_touchpoint_hypothesis_is_explicit() -> None:
    report = build_rag_touchpoint_report()

    assert report.customer_code_changes == 1
    assert report.adapter_hooks == 2
    assert report.untouched_boundaries == 1


def test_stable_chunk_identity_is_the_explicit_identity_boundary() -> None:
    report = build_rag_touchpoint_report()

    customer_touchpoints = {
        touchpoint.name
        for touchpoint in report.touchpoints
        if touchpoint.burden
        is TouchpointBurden.CUSTOMER_CODE_CHANGE
    }

    assert customer_touchpoints == {
        "chunk_identity",
    }


def test_materialization_hook_precedes_lossy_serialization() -> None:
    report = build_rag_touchpoint_report()

    materialization_touchpoint = next(
        touchpoint
        for touchpoint in report.touchpoints
        if touchpoint.name == "pre_lossy_materialization"
    )

    assert (
        materialization_touchpoint.burden
        is TouchpointBurden.ADAPTER_HOOK
    )
    assert materialization_touchpoint.captures == (
        "index_asset_external_id",
        "consumed_chunk_revisions",
    )


def test_consumer_binding_requires_exact_consumed_revision() -> None:
    report = build_rag_touchpoint_report()

    consumer_touchpoint = next(
        touchpoint
        for touchpoint in report.touchpoints
        if touchpoint.name == "consumer_revision_binding"
    )

    assert (
        consumer_touchpoint.burden
        is TouchpointBurden.ADAPTER_HOOK
    )
    assert consumer_touchpoint.captures == (
        "agent_asset_external_id",
        "consumed_index_revision",
        "critical",
    )
