"""Live Gemini semantic sensitivity benchmark; read-only and explicitly opt-in.

Run inside the API Docker environment with --execute and
SDE_SEMANTIC_BENCHMARK_ALLOW_PROVIDER_CALLS=1.
"""

from __future__ import annotations

import argparse
import json
import os

from src.services.semantic_change_sensitivity import evaluate_semantic_sensitivity


def validate_runtime(*, execute: bool, environ: dict[str, str]) -> None:
    if not execute or environ.get("SDE_SEMANTIC_BENCHMARK_ALLOW_PROVIDER_CALLS") != "1":
        raise ValueError("Provider requests require --execute and explicit opt-in.")
    if environ.get("ENVIRONMENT", "development").lower() not in ("development", "test"):
        raise ValueError("Live benchmark is restricted to development/test environments.")
    if environ.get("EMBEDDING_PROVIDER", "").lower() != "gemini":
        raise ValueError("Live benchmark requires EMBEDDING_PROVIDER=gemini (no OpenAI fallback).")
    if not environ.get("GEMINI_API_KEY", "").strip():
        raise ValueError("GEMINI_API_KEY is missing; no provider calls were made.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Enable nine Gemini embedding requests")
    args = parser.parse_args(argv)
    # Validate before constructing a provider client or performing external IO.
    validate_runtime(execute=args.execute, environ=dict(os.environ))

    from src.services.embedding_service import EmbeddingService

    service = EmbeddingService()
    report = evaluate_semantic_sensitivity(
        embed_text=service.generate_embedding,
        provider=service.provider,
        model=service.model,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["strict_separation_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
