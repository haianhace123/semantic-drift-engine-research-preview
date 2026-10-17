"""Read-only semantic sensitivity benchmark using the production vector metric.

This module does not classify drift events, calculate risk, or authorize actions.
The benchmark probes whether an embedding model ranks known material policy
changes farther from a baseline than meaning-preserving paraphrases.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class SemanticChangeCase:
    case_id: str
    baseline: str
    paraphrase: str
    material_change: str


# Manually authored policy contrasts. None is a learned threshold, benchmark
# calibration set, or production customer document.
CASES: tuple[SemanticChangeCase, ...] = (
    SemanticChangeCase(
        case_id="data_retention_and_sharing",
        baseline=(
            "Customer records are deleted 30 days after account closure. "
            "They are not shared with advertisers."
        ),
        paraphrase=(
            "Thirty days after an account is closed, its customer records "
            "are removed and are never provided to advertising partners."
        ),
        material_change=(
            "Customer records are retained indefinitely after account "
            "closure and may be sold to advertisers."
        ),
    ),
    SemanticChangeCase(
        case_id="refund_eligibility",
        baseline=(
            "Customers may receive a full refund if they cancel "
            "within 30 days of purchase."
        ),
        paraphrase=(
            "Purchases cancelled during the first 30 days "
            "qualify for a full refund."
        ),
        material_change=(
            "All purchases are final. Customers are not eligible "
            "for refunds for any cancellation."
        ),
    ),
    SemanticChangeCase(
        case_id="uptime_commitment",
        baseline=(
            "The service guarantees 99.9% monthly uptime and grants "
            "service credits when availability falls below this level."
        ),
        paraphrase=(
            "A 99.9% monthly availability commitment applies, with credits "
            "issued if the service misses that target."
        ),
        material_change=(
            "The service is provided on a best-effort basis without "
            "uptime guarantees or service credits."
        ),
    ),
)


def _vector(values: Sequence[float]) -> tuple[float, ...]:
    try:
        result = tuple(float(value) for value in values)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Embedding contains invalid numeric values.") from exc
    if not result or not all(math.isfinite(value) for value in result):
        raise ValueError("Embedding is empty or contains non-finite values.")
    if not any(value != 0.0 for value in result):
        raise ValueError("Embedding must not be the zero vector.")
    return result


def _distance(metrics: dict[str, float]) -> float:
    distance = float(metrics["semantic_distance"])
    if not math.isfinite(distance) or not 0.0 <= distance <= 2.0:
        raise ValueError("Production metric returned invalid semantic_distance.")
    return distance


def evaluate_semantic_sensitivity(
    *,
    embed_text: Callable[[str], Sequence[float]],
    provider: str,
    model: str,
    metric_fn: Callable[[Sequence[float], Sequence[float]], dict[str, float]] | None = None,
    cases: Sequence[SemanticChangeCase] = CASES,
) -> dict:
    """Evaluate ranking, without tuning a detection threshold.

    Exactly three distinct texts per case are sent to the embedder. The identity
    control reuses the baseline vector and costs no additional provider request.
    The output intentionally excludes source texts and embedding vectors.
    """
    if not cases:
        raise ValueError("At least one benchmark case is required.")
    if metric_fn is None:
        # Use the same computation as VectorAnalysisService in production.
        from src.services.embedding_service import calculate_vector_metrics

        metric_fn = calculate_vector_metrics

    # Validate the whole fixed corpus before making any provider requests.
    seen_ids: set[str] = set()
    for case in cases:
        if not case.case_id or case.case_id in seen_ids:
            raise ValueError("Benchmark case IDs must be unique and nonempty.")
        seen_ids.add(case.case_id)
        texts = (case.baseline, case.paraphrase, case.material_change)
        if any(not text.strip() for text in texts) or len(set(texts)) != 3:
            raise ValueError("Each case requires three distinct nonempty texts.")

    results: list[dict] = []
    first_dimensions: int | None = None
    for case in cases:
        texts = (case.baseline, case.paraphrase, case.material_change)
        baseline, paraphrase, material = (_vector(embed_text(text)) for text in texts)
        if len({len(baseline), len(paraphrase), len(material)}) != 1:
            raise ValueError("Embedding dimensions do not match in a case.")
        if first_dimensions is None:
            first_dimensions = len(baseline)
        elif len(baseline) != first_dimensions:
            raise ValueError("Embedding dimensions differ between cases.")

        identity_distance = _distance(metric_fn(baseline, baseline))
        paraphrase_distance = _distance(metric_fn(baseline, paraphrase))
        material_distance = _distance(metric_fn(baseline, material))
        margin = material_distance - paraphrase_distance
        passed = identity_distance <= 1e-8 and margin > 1e-8
        results.append({
            "case_id": case.case_id,
            "dimensions": len(baseline),
            "identity_distance": round(identity_distance, 8),
            "paraphrase_distance": round(paraphrase_distance, 8),
            "material_change_distance": round(material_distance, 8),
            "separation_margin": round(margin, 8),
            "separation_passed": passed,
        })

    successes = sum(row["separation_passed"] for row in results)
    return {
        "schema_version": 1,
        "benchmark": "controlled_semantic_change_sensitivity",
        "basis": "production_embedding_provider_and_vector_metric",
        "provider": provider,
        "model": model,
        "embedding_dimensions": first_dimensions,
        "provider_requests": len(results) * 3,
        "case_count": len(results),
        "passed_count": successes,
        "strict_separation_passed": successes == len(results),
        "cases": results,
        "limitations": [
            "This probes embedding sensitivity, not production drift classification.",
            "Three curated examples do not establish population-level precision or recall.",
            "Distance ordering does not establish a safe detection threshold.",
            "No snapshots, risk assessments, or downstream actions are written.",
        ],
    }
