"""Plan or explicitly execute the pinned, read-only semantic decision evaluation."""

from __future__ import annotations

import argparse
import json
import os

from src.services.semantic_decision_evaluation import (
    evaluate_decisions,
    load_cases,
    plan,
)


def validate_execution(*, execute: bool, environ: dict[str, str]) -> None:
    if not execute or environ.get("SDE_SEMANTIC_DECISION_EVAL_ALLOW_PROVIDER_CALLS") != "1":
        raise ValueError("Live DeepSeek calls require --execute and explicit opt-in.")
    if environ.get("ENVIRONMENT", "").lower() not in {"development", "test"}:
        raise ValueError("Decision evaluation is restricted to development/test.")
    if not environ.get("DEEPSEEK_API_KEY", "").strip():
        raise ValueError("DEEPSEEK_API_KEY is required; no provider calls were made.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true",
        help="Run one DeepSeek semantic request per case; may incur cost",
    )
    args = parser.parse_args(argv)
    cases, digest = load_cases()
    if not args.execute:
        print(json.dumps(plan(cases, digest), indent=2, sort_keys=True))
        return 0

    validate_execution(execute=args.execute, environ=dict(os.environ))
    from src.core.config import settings
    report = evaluate_decisions(
        cases, digest=digest, model=settings.DEEPSEEK_MODEL,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    # Exit 2 records a diagnostic quality failure, not a provider/setup success.
    return 0 if report["diagnostic_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
