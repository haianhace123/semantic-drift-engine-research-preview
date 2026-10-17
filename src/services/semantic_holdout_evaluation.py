"""Opt-in, read-only evaluation on externally prepared, labeled document pairs.

The dataset provenance is self-declared; no code can establish annotation
independence from a manifest alone. Reports never contain source text.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from src.services.semantic_decision_evaluation import (
    DecisionCase,
    analyze_via_production,
    evaluate_decisions,
)

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{2,79}\Z")
_MAX_CASES = 100
_MAX_TEXT_CHARS = 12000
_MAX_BYTES = 5_000_000


def _identifier(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"Invalid {field}; use anonymous lowercase identifiers.")
    return value


def load_holdout(path: Path) -> tuple[tuple[DecisionCase, ...], str, dict[str, Any]]:
    """Reject invalid or duplicate examples before any provider calls.

    No source text or user-controlled corpus content is included in exceptions.
    """
    with path.open("rb") as handle:
        data = handle.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise ValueError("Corpus exceeds the size limit.")
    digest = hashlib.sha256(data).hexdigest()
    def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field in corpus.")
            result[key] = value
        return result

    try:
        document = json.loads(data, object_pairs_hook=unique_fields)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Invalid corpus JSON.") from None
    if not isinstance(document, dict) or set(document) != {
        "schema_version", "dataset_id", "provenance", "cases"
    } or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("Unsupported holdout corpus schema.")

    dataset_id = _identifier(document["dataset_id"], field="dataset_id")
    provenance = document["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {
        "source_type", "annotation_protocol_id", "annotator_count",
        "adjudicated", "held_out_before_model_selection"
    }:
        raise ValueError("Invalid provenance declaration.")
    if provenance["source_type"] not in {"public", "customer_authorized", "internal"}:
        raise ValueError("Invalid source type.")
    _identifier(provenance["annotation_protocol_id"], field="annotation_protocol_id")
    if (type(provenance["annotator_count"]) is not int
            or not 1 <= provenance["annotator_count"] <= 100):
        raise ValueError("Invalid annotator count.")
    if (type(provenance["adjudicated"]) is not bool
            or type(provenance["held_out_before_model_selection"]) is not bool):
        raise ValueError("Invalid provenance booleans.")

    raw_cases = document["cases"]
    if not isinstance(raw_cases, list) or not 2 <= len(raw_cases) <= _MAX_CASES:
        raise ValueError("A holdout corpus must contain 2 to 100 cases.")
    ids: set[str] = set()
    fingerprints: set[str] = set()
    cases: list[DecisionCase] = []
    for item in raw_cases:
        if not isinstance(item, dict) or set(item) != {
            "case_id", "domain", "expected_material", "old_text", "new_text"
        }:
            raise ValueError("Invalid holdout case fields.")
        case_id = _identifier(item["case_id"], field="case_id")
        domain = _identifier(item["domain"], field="domain")
        label = item["expected_material"]
        old, new = item["old_text"], item["new_text"]
        if type(label) is not bool:
            raise ValueError("Expected materiality must be boolean.")
        if (not isinstance(old, str) or not isinstance(new, str)
                or not old.strip() or not new.strip() or old == new
                or len(old) > _MAX_TEXT_CHARS or len(new) > _MAX_TEXT_CHARS):
            raise ValueError("Invalid or oversized document pair.")
        # Order-independent fingerprint prevents accidental duplicate/swapped
        # evidence being counted twice, even with different case IDs.
        identity = json.dumps(sorted((old, new)), ensure_ascii=False)
        fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if case_id in ids or fingerprint in fingerprints:
            raise ValueError("Duplicate holdout ID or document pair.")
        ids.add(case_id)
        fingerprints.add(fingerprint)
        cases.append(DecisionCase(case_id, domain, label, old, new))
    if not any(case.expected_material for case in cases) or all(
        case.expected_material for case in cases
    ):
        raise ValueError("Both material and equivalent labels are required.")
    # Only non-sensitive metadata is returned to report-building callers.
    return tuple(cases), digest, {
        "dataset_id": dataset_id,
        "source_type": provenance["source_type"],
        "annotation_protocol_id": provenance["annotation_protocol_id"],
        "annotator_count": provenance["annotator_count"],
        "adjudicated_declared": provenance["adjudicated"],
        "holdout_declared": provenance["held_out_before_model_selection"],
    }


def plan_holdout(
    cases: tuple[DecisionCase, ...], digest: str, metadata: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "benchmark": "external_semantic_holdout",
        "mode": "plan",
        "corpus_sha256": digest,
        "dataset_id": metadata["dataset_id"],
        "case_count": len(cases),
        "material_count": sum(c.expected_material for c in cases),
        "equivalent_count": sum(not c.expected_material for c in cases),
        "maximum_provider_requests": len(cases),
        "provider_requests": 0,
        "provenance_status": "self_declared_not_verified",
        "limitations": [
            "A manifest does not verify that labels are independent, representative or held out.",
            "Plan mode never calls the provider or writes database state.",
        ],
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fp = tn = fn = abstained_material = abstained_equivalent = 0
    for row in rows:
        if row["outcome"] == "abstained":
            if row["expected_material"]:
                abstained_material += 1
            else:
                abstained_equivalent += 1
        elif row["outcome"] == "material":
            if row["expected_material"]:
                tp += 1
            else:
                fp += 1
        elif row["expected_material"]:
            fn += 1
        else:
            tn += 1
    size = len(rows)
    return {
        "cases": size,
        "true_positive": tp, "false_positive": fp,
        "true_negative": tn, "false_negative": fn,
        "abstained_material": abstained_material,
        "abstained_equivalent": abstained_equivalent,
        "coverage": round((tp + fp + tn + fn) / size, 6),
        "precision_on_completed": round(tp / (tp + fp), 6) if tp + fp else None,
        "recall_on_completed": round(tp / (tp + fn), 6) if tp + fn else None,
        "f1_on_completed": round(2 * tp / (2 * tp + fp + fn), 6)
        if 2 * tp + fp + fn else None,
        "material_detection_yield_including_abstentions": round(
            tp / (tp + fn + abstained_material), 6
        ) if tp + fn + abstained_material else None,
        "equivalent_clear_yield_including_abstentions": round(
            tn / (tn + fp + abstained_equivalent), 6
        ) if tn + fp + abstained_equivalent else None,
    }


def evaluate_holdout(
    cases: tuple[DecisionCase, ...], *, digest: str,
    metadata: dict[str, Any], model: str,
    analyzer: Callable[[DecisionCase], dict[str, Any]] = analyze_via_production,
) -> dict[str, Any]:
    """Score each case exactly once, then compute pooled and domain slices."""
    original = evaluate_decisions(cases, digest=digest, analyzer=analyzer, model=model)
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in original["cases"]:
        by_domain[row["domain"]].append(row)
    positives = sum(c.expected_material for c in cases)
    negatives = len(cases) - positives
    # A raw text-diff baseline flags every non-identical pair as changed.
    # All pairs are non-identical by corpus contract.
    return {
        "schema_version": 1,
        "benchmark": "external_semantic_holdout",
        "basis": original["basis"],
        "corpus_sha256": digest,
        "dataset_id": metadata["dataset_id"],
        "provenance_status": "self_declared_not_verified",
        "provenance_declaration": metadata,
        "model": model,
        "case_count": len(cases),
        "metrics": _metrics(original["cases"]),
        "by_domain": {
            domain: _metrics(rows) for domain, rows in sorted(by_domain.items())
        },
        "exact_text_diff_baseline": {
            "description": "All non-identical text pairs are flagged material.",
            "true_positive": positives,
            "false_positive": negatives,
            "precision": round(positives / len(cases), 6),
            "recall": 1.0,
            "f1": round(2 * positives / (2 * positives + negatives), 6),
        },
        "cases": original["cases"],
        "limitations": [
            "Labels, annotation independence and held-out status are declarative, not verified.",
            "No population precision/recall, confidence intervals or production release gate is established.",
            "Per-domain metrics on few cases have high sampling uncertainty.",
            "The exact text-diff control is deliberately naive, not an optimized lexical baseline.",
            "Reports omit documents, provider responses and exception messages.",
            "Only the pre-persistence semantic decision path is exercised; no downstream actions.",
        ],
    }
