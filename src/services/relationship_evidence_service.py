from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from src.models.entity import Entity


@dataclass(frozen=True)
class EntityCandidate:
    """
    Minimal entity representation required by the deterministic
    relationship evidence extractor.
    """

    id: uuid.UUID
    name: str
    domain: str


def _normalize_text(value: str) -> str:
    """
    Normalize text for deterministic matching.

    Case-insensitive matching is intentional. Whitespace is collapsed
    so evidence originating from JSON or generated summaries behaves
    consistently.
    """
    return " ".join(
        str(value or "")
        .casefold()
        .split()
    )


def _serialize_evidence(
    semantic_delta_json: Any,
    summary_text: str | None,
) -> str:
    """
    Convert event-level semantic evidence into searchable text.

    JSON serialization keeps this function independent from the exact
    shape of semantic_delta_json while still limiting matching to
    evidence belonging to the drift event.
    """
    parts: list[str] = []

    if summary_text:
        parts.append(
            str(summary_text)
        )

    if semantic_delta_json is not None:
        try:
            serialized = json.dumps(
                semantic_delta_json,
                ensure_ascii=False,
                sort_keys=True,
            )
        except (
            TypeError,
            ValueError,
        ):
            serialized = str(
                semantic_delta_json
            )

        parts.append(
            serialized
        )

    return _normalize_text(
        " ".join(parts)
    )


def _normalized_domain(
    domain: str,
) -> str:
    value = _normalize_text(
        domain
    )

    value = re.sub(
        r"^https?://",
        "",
        value,
    )

    value = value.removeprefix(
        "www."
    )

    return value.rstrip("/")


def _contains_name(
    evidence: str,
    name: str,
) -> bool:
    normalized_name = _normalize_text(
        name
    )

    if not normalized_name:
        return False

    # Short/common names should not be matched as arbitrary
    # substrings. Word boundaries also prevent "slack" from
    # matching an unrelated larger token.
    pattern = (
        r"(?<![\w])"
        + re.escape(normalized_name)
        + r"(?![\w])"
    )

    return (
        re.search(
            pattern,
            evidence,
            flags=re.IGNORECASE,
        )
        is not None
    )


def _contains_domain(
    evidence: str,
    domain: str,
) -> bool:
    normalized_domain = _normalized_domain(
        domain
    )

    if not normalized_domain:
        return False

    escaped = re.escape(
        normalized_domain
    )

    # Domain boundaries deliberately differ from ordinary word
    # boundaries because dots and hyphens are valid domain
    # characters.
    pattern = (
        r"(?<![a-z0-9.-])"
        + escaped
        + r"(?![a-z0-9.-])"
    )

    return (
        re.search(
            pattern,
            evidence,
            flags=re.IGNORECASE,
        )
        is not None
    )


def entity_is_mentioned(
    evidence: str,
    entity: EntityCandidate,
) -> bool:
    """
    Return True when an entity has explicit deterministic evidence.

    Either the entity name or its domain is sufficient.
    """
    if not evidence:
        return False

    return (
        _contains_name(
            evidence,
            entity.name,
        )
        or _contains_domain(
            evidence,
            entity.domain,
        )
    )


def extract_entity_ids(
    *,
    semantic_delta_json: Any,
    summary_text: str | None,
    source_entity_id: uuid.UUID | str,
    entities: Iterable[EntityCandidate | Entity],
) -> list[str]:
    """
    Extract explicitly mentioned entity IDs from semantic evidence.

    Properties:
    - deterministic;
    - no external API calls;
    - source entity is excluded;
    - duplicate IDs are removed;
    - output ordering is stable.
    """
    evidence = _serialize_evidence(
        semantic_delta_json,
        summary_text,
    )

    if not evidence:
        return []

    source_id = str(
        source_entity_id
    )

    matched_ids: set[str] = set()

    for entity in entities:
        entity_id = str(
            entity.id
        )

        if entity_id == source_id:
            continue

        if entity_is_mentioned(
            evidence,
            EntityCandidate(
                id=entity.id,
                name=str(
                    entity.name
                    or ""
                ),
                domain=str(
                    entity.domain
                    or ""
                ),
            ),
        ):
            matched_ids.add(
                entity_id
            )

    return sorted(
        matched_ids
    )


def extract_relationship_evidence(
    *,
    semantic_delta_json: Any,
    summary_text: str | None,
    source_entity_id: uuid.UUID | str,
    entities: Sequence[EntityCandidate | Entity],
) -> dict[str, Any]:
    """
    Produce event-level relationship evidence.

    The returned structure is intentionally small. The event currently
    persists only `entities_affected`; richer edge metadata belongs to
    the future graph persistence layer.
    """
    entity_ids = extract_entity_ids(
        semantic_delta_json=semantic_delta_json,
        summary_text=summary_text,
        source_entity_id=source_entity_id,
        entities=entities,
    )

    return {
        "entities_affected": entity_ids,
        "matched_count": len(
            entity_ids
        ),
        "method": "deterministic_name_domain_v1",
    }