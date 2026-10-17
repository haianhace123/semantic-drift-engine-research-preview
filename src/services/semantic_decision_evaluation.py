"""Read-only, labeled evaluation of the production semantic decision path.

This is a diagnostic corpus, not a calibration or production acceptance set.
Provider access is owned by the CLI. This module performs no external I/O
other than reading the explicitly pinned, repository-owned fixture.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


CORPUS_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "semantic_decision_v1.json"
)
_ALLOWED_DOMAINS = frozenset(
    {"pricing", "eligibility", "privacy", "security", "sla", "policy"}
)
_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,79}\Z")


@dataclass(frozen=True)
class DecisionCase:
    case_id: str
    domain: str
    expected_material: bool
    old_text: str
    new_text: str


def load_cases(path: Path = CORPUS_PATH) -> tuple[tuple[DecisionCase, ...], str]:
    """Validate the entire fixed corpus before the first provider request."""
    raw_bytes = path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    document = json.loads(raw_bytes)
    if not isinstance(document, dict) or set(document) != {"schema_version", "cases"}:
        raise ValueError("Corpus must have exactly schema_version and cases.")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1 or not isinstance(document["cases"], list):
        raise ValueError("Unsupported decision corpus schema.")

    cases: list[DecisionCase] = []
    ids: set[str] = set()
    for item in document["cases"]:
        if not isinstance(item, dict) or set(item) != {
            "case_id", "domain", "expected_material", "old_text", "new_text"
        }:
            raise ValueError("Invalid decision case fields.")
        case_id = item["case_id"]
        if not isinstance(case_id, str) or not _ID_PATTERN.fullmatch(case_id):
            raise ValueError("Invalid decision case ID.")
        if case_id in ids:
            raise ValueError("Duplicate decision case ID.")
        if item["domain"] not in _ALLOWED_DOMAINS:
            raise ValueError("Invalid decision case domain.")
        if type(item["expected_material"]) is not bool:
            raise ValueError("Expected material label must be boolean.")
        old_text, new_text = item["old_text"], item["new_text"]
        if (
            not isinstance(old_text, str)
            or not isinstance(new_text, str)
            or not old_text.strip()
            or not new_text.strip()
            or old_text == new_text
        ):
            raise ValueError("Both document versions must be nonempty and distinct.")
        ids.add(case_id)
        cases.append(DecisionCase(**item))

    if not cases or len(cases) > 40:
        raise ValueError("Decision corpus must contain between 1 and 40 cases.")
    if not any(c.expected_material for c in cases) or all(c.expected_material for c in cases):
        raise ValueError("Decision corpus must have material and equivalent controls.")
    return tuple(cases), digest


def plan(cases: Sequence[DecisionCase], digest: str) -> dict[str, Any]:
    """No provider setup, credentials, database access, or snapshot reads."""
    return {
        "schema_version": 1,
        "mode": "plan",
        "benchmark": "semantic_decision_v1",
        "corpus_sha256": digest,
        "case_count": len(cases),
        "material_count": sum(c.expected_material for c in cases),
        "equivalent_count": sum(not c.expected_material for c in cases),
        "case_ids": [c.case_id for c in cases],
        "maximum_provider_requests": len(cases),
        "provider_requests": 0,
        "limitations": [
            "Synthetic, author-labeled examples are not independent customer ground truth.",
            "No production sensitivity, specificity, prevalence or calibration claim is supported.",
            "No snapshots, drift events, risk assessments or downstream actions are written.",
        ],
    }


def analyze_via_production(case: DecisionCase) -> dict[str, Any]:
    """Run the real detector evidence and event-field path without persistence."""
    # Import lazily: planning and injected unit tests need no provider client.
    from types import SimpleNamespace
    from src.detector.change_detector import (
        _process_semantic_analysis,
        _semantic_event_fields,
    )

    old_hash = hashlib.sha256(case.old_text.encode("utf-8")).hexdigest()
    new_hash = hashlib.sha256(case.new_text.encode("utf-8")).hexdigest()
    delta, summary = _process_semantic_analysis(
        page_id=f"evaluation:{case.case_id}",
        old_snapshot=SimpleNamespace(content_hash=old_hash),
        new_snapshot=SimpleNamespace(content_hash=new_hash),
        old_text=case.old_text,
        new_text=case.new_text,
    )
    fields = _semantic_event_fields(delta, summary)
    return {
        "analysis_status": fields["analysis_status"],
        "changes_count": fields["changes_count"],
        "risk_level": fields["risk_level"],
    }


def evaluate_decisions(
    cases: Sequence[DecisionCase],
    *,
    digest: str,
    analyzer: Callable[[DecisionCase], dict[str, Any]] = analyze_via_production,
    model: str,
) -> dict[str, Any]:
    """Make failures explicit; never fold an abstention into a true negative."""
    if not cases:
        raise ValueError("At least one case is required.")

    tp = fp = tn = fn = 0
    abstained_material = abstained_equivalent = 0
    rows: list[dict[str, Any]] = []
    for case in cases:
        row: dict[str, Any] = {
            "case_id": case.case_id,
            "domain": case.domain,
            "expected_material": case.expected_material,
        }
        try:
            result = analyzer(case)
            if not isinstance(result, dict):
                raise ValueError("Semantic analysis result must be an object.")
            count = result.get("changes_count")
            risk = result.get("risk_level")
            if (
                result.get("analysis_status") != "completed"
                or type(count) is not int
                or count < 0
                or risk not in {"low", "medium", "high"}
                or (count == 0 and risk != "low")
            ):
                raise ValueError("Inconsistent or incomplete semantic decision.")
            predicted = count > 0
            row.update({
                "outcome": "material" if predicted else "equivalent",
                "reported_risk": risk,
                "change_count": count,
                "matched_label": predicted == case.expected_material,
            })
            if predicted and case.expected_material:
                tp += 1
            elif predicted:
                fp += 1
            elif case.expected_material:
                fn += 1
            else:
                tn += 1
        except Exception as exc:
            # Error messages can contain document text or provider responses.
            # Never persist or print them in an evaluation report.
            row.update({"outcome": "abstained", "error_type": type(exc).__name__})
            if case.expected_material:
                abstained_material += 1
            else:
                abstained_equivalent += 1
        rows.append(row)

    labeled_positives = tp + fn + abstained_material
    labeled_negatives = tn + fp + abstained_equivalent
    completed = tp + tn + fp + fn
    failures = abstained_material + abstained_equivalent
    diagnostic_passed = fn == 0 and fp == 0 and failures == 0
    return {
        "schema_version": 1,
        "benchmark": "semantic_decision_v1",
        "basis": "production_evidence_extraction_and_deepseek_decision_before_persistence",
        "corpus_sha256": digest,
        "model": model,
        "case_count": len(cases),
        "completed_count": completed,
        "abstained_count": failures,
        "confusion_matrix": {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
            "abstained_material": abstained_material,
            "abstained_equivalent": abstained_equivalent,
        },
        "precision_on_completed": round(tp / (tp + fp), 6) if tp + fp else None,
        "recall_on_completed": round(tp / (tp + fn), 6) if tp + fn else None,
        "material_detection_yield": round(tp / labeled_positives, 6),
        "equivalent_clear_yield": round(tn / labeled_negatives, 6),
        "coverage": round(completed / len(cases), 6),
        "diagnostic_passed": diagnostic_passed,
        "cases": rows,
        "limitations": [
            "These cases are synthetic and were authored with their labels; no independent annotation.",
            "This is a small diagnostic set, not production precision/recall or a release threshold.",
            "DeepSeek outputs may vary by request and model version even with temperature 0.",
            "No database events, vector analysis, downstream scoring or actions are exercised.",
            "An abstention is never counted as a correct negative or a successful decision.",
        ],
    }
