"""Safely plan or explicitly run external human-labeled semantic holdout evaluation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.services.semantic_holdout_evaluation import (
    evaluate_holdout,
    load_holdout,
    plan_holdout,
)


def validate_execution(*, execute: bool, max_requests: int | None,
                       count: int, environ: dict[str, str]) -> None:
    if not execute or environ.get("SDE_SEMANTIC_HOLDOUT_EVAL_ALLOW_PROVIDER_CALLS") != "1":
        raise ValueError("Live evaluation requires --execute and provider opt-in.")
    if environ.get("SDE_SEMANTIC_HOLDOUT_DATA_TRANSFER_APPROVED") != "1":
        raise ValueError("Explicit approval to transfer corpus text is required.")
    if environ.get("ENVIRONMENT", "").lower() not in {"development", "test"}:
        raise ValueError("Holdout evaluation is restricted to development/test.")
    if not environ.get("DEEPSEEK_API_KEY", "").strip():
        raise ValueError("DeepSeek API key required.")
    if type(max_requests) is not int or max_requests != count:
        raise ValueError("--max-provider-requests must match the full corpus size.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path,
                        help="Explicit external holdout JSON path, never commit customer data")
    parser.add_argument("--execute", action="store_true",
                        help="Send corpus text to DeepSeek; may incur cost")
    parser.add_argument("--max-provider-requests", type=int,
                        help="Explicit call budget; must equal corpus count")
    args = parser.parse_args(argv)
    cases, digest, metadata = load_holdout(args.corpus)
    if not args.execute:
        print(json.dumps(plan_holdout(cases, digest, metadata), indent=2, sort_keys=True))
        return 0
    validate_execution(execute=True, max_requests=args.max_provider_requests,
                       count=len(cases), environ=dict(os.environ))
    from src.core.config import settings
    report = evaluate_holdout(cases, digest=digest, metadata=metadata,
                              model=settings.DEEPSEEK_MODEL)
    print(json.dumps(report, indent=2, sort_keys=True))
    # No synthetic "all green" gate. Evaluation is a measurement, not a release test.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
