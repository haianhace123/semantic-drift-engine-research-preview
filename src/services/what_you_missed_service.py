"""
Application/composition service for What You Missed v1.

This module intentionally contains very little business logic.

Responsibilities:
- validate/load persisted evidence through the evidence service;
- pass that immutable evidence to the deterministic explanation engine;
- return the final read model.

It performs no commit, flush, mutation, LLM call, or external API call.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from src.services.what_you_missed_evidence_service import (
    load_what_you_missed_evidence,
)
from src.services.what_you_missed_explanation_service import (
    WhatYouMissedExplanation,
    explain_what_you_missed,
)


def get_what_you_missed(
    db: Session,
    *,
    organization_id,
    since: datetime,
    until: datetime,
    limit: int | None = None,
) -> WhatYouMissedExplanation:
    """
    Build the deterministic What You Missed read model.

    Event-window semantics are delegated to the evidence loader:

        since < event.detected_at <= until

    Knowledge-time filtering for derived evidence is also owned by the
    evidence layer.

    Ranking and explanation generation are delegated to the pure
    explanation engine.

    The caller owns the SQLAlchemy Session lifecycle.
    """

    evidence = load_what_you_missed_evidence(
        db,
        organization_id=organization_id,
        since=since,
        until=until,
    )

    return explain_what_you_missed(
        evidence,
        limit=limit,
    )