from __future__ import annotations

import uuid

from src.services.relationship_evidence_service import (
    EntityCandidate,
    entity_is_mentioned,
    extract_entity_ids,
    extract_relationship_evidence,
)


OPENAI_ID = uuid.UUID(
    "00000000-0000-0000-0000-000000000001"
)

MICROSOFT_ID = uuid.UUID(
    "00000000-0000-0000-0000-000000000002"
)

GITHUB_ID = uuid.UUID(
    "00000000-0000-0000-0000-000000000003"
)

NVIDIA_ID = uuid.UUID(
    "00000000-0000-0000-0000-000000000004"
)

GOOGLE_ID = uuid.UUID(
    "00000000-0000-0000-0000-000000000005"
)


def _entity(
    entity_id: uuid.UUID,
    name: str,
    domain: str,
) -> EntityCandidate:
    return EntityCandidate(
        id=entity_id,
        name=name,
        domain=domain,
    )


def _entities():
    return [
        _entity(
            OPENAI_ID,
            "OpenAI",
            "openai.com",
        ),
        _entity(
            MICROSOFT_ID,
            "Microsoft",
            "microsoft.com",
        ),
        _entity(
            GITHUB_ID,
            "GitHub",
            "github.com",
        ),
        _entity(
            NVIDIA_ID,
            "NVIDIA",
            "nvidia.com",
        ),
        _entity(
            GOOGLE_ID,
            "Google",
            "google.com",
        ),
    ]


def test_matches_entity_name():
    result = extract_entity_ids(
        semantic_delta_json={
            "changes": [
                {
                    "description": (
                        "Microsoft Azure support changed."
                    ),
                },
            ],
        },
        summary_text=None,
        source_entity_id=OPENAI_ID,
        entities=_entities(),
    )

    assert result == [
        str(MICROSOFT_ID),
    ]


def test_matches_entity_domain():
    result = extract_entity_ids(
        semantic_delta_json={
            "changes": [
                {
                    "url": (
                        "https://github.com/example/project"
                    ),
                },
            ],
        },
        summary_text=None,
        source_entity_id=OPENAI_ID,
        entities=_entities(),
    )

    assert result == [
        str(GITHUB_ID),
    ]


def test_matches_multiple_entities():
    result = extract_entity_ids(
        semantic_delta_json={
            "changes": [
                {
                    "description": (
                        "NVIDIA integration now supports Google."
                    ),
                },
            ],
        },
        summary_text=None,
        source_entity_id=OPENAI_ID,
        entities=_entities(),
    )

    assert set(result) == {
        str(NVIDIA_ID),
        str(GOOGLE_ID),
    }


def test_source_entity_is_excluded():
    result = extract_entity_ids(
        semantic_delta_json={
            "changes": [
                {
                    "description": (
                        "OpenAI updated the OpenAI API."
                    ),
                },
            ],
        },
        summary_text=None,
        source_entity_id=OPENAI_ID,
        entities=_entities(),
    )

    assert result == []


def test_duplicate_mentions_are_deduplicated():
    result = extract_entity_ids(
        semantic_delta_json={
            "changes": [
                {
                    "description": (
                        "Microsoft changed Microsoft integration."
                    ),
                },
            ],
        },
        summary_text=(
            "Microsoft documentation changed."
        ),
        source_entity_id=OPENAI_ID,
        entities=_entities(),
    )

    assert result == [
        str(MICROSOFT_ID),
    ]


def test_unknown_entity_is_ignored():
    result = extract_entity_ids(
        semantic_delta_json={
            "changes": [
                {
                    "description": (
                        "UnknownVendor changed its API."
                    ),
                },
            ],
        },
        summary_text=None,
        source_entity_id=OPENAI_ID,
        entities=_entities(),
    )

    assert result == []


def test_summary_is_used_as_evidence():
    result = extract_entity_ids(
        semantic_delta_json={
            "changes": [],
        },
        summary_text=(
            "GitHub authentication behavior changed."
        ),
        source_entity_id=OPENAI_ID,
        entities=_entities(),
    )

    assert result == [
        str(GITHUB_ID),
    ]


def test_name_matching_respects_token_boundaries():
    entity = _entity(
        uuid.UUID(
            "00000000-0000-0000-0000-000000000006"
        ),
        "Slack",
        "slack.com",
    )

    assert (
        entity_is_mentioned(
            "slack integration changed",
            entity,
        )
        is True
    )

    assert (
        entity_is_mentioned(
            "slackware package changed",
            entity,
        )
        is False
    )


def test_relationship_evidence_metadata():
    result = extract_relationship_evidence(
        semantic_delta_json={
            "changes": [
                {
                    "description": (
                        "Microsoft integration changed."
                    ),
                },
            ],
        },
        summary_text=None,
        source_entity_id=OPENAI_ID,
        entities=_entities(),
    )

    assert result == {
        "entities_affected": [
            str(MICROSOFT_ID),
        ],
        "matched_count": 1,
        "method": "deterministic_name_domain_v1",
    }