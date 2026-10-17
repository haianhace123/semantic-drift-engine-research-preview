from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_API_KEY_HEADER = "X-API-Key"


class ExternalLineageClientError(RuntimeError):
    pass


def _post_json(
    *,
    url: str,
    api_key_header: str,
    api_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            api_key_header: api_key,
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=10,
        ) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )
        raise ExternalLineageClientError(
            f"HTTP {exc.code} from {url}: {response_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ExternalLineageClientError(
            f"Unable to reach {url}: {exc.reason}"
        ) from exc

    if not response_body:
        return {}

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ExternalLineageClientError(
            f"Non-JSON response from {url}: {response_body}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ExternalLineageClientError(
            f"Unexpected response from {url}: {parsed!r}"
        )

    return parsed


def _observations() -> tuple[dict[str, Any], ...]:
    return (
        {
            "observation_kind": "materialization",
            "source_external_id": "external-policy-source",
            "snapshot_external_id": "external-snapshot-v1",
            "asset_external_id": "chunk-refund",
            "asset_kind": "chunk",
            "revision_external_id": "chunk-refund@v1",
            "sequence": 1,
            "dependencies": [],
        },
        {
            "observation_kind": "materialization",
            "source_external_id": "external-policy-source",
            "snapshot_external_id": "external-snapshot-v1",
            "asset_external_id": "index-support",
            "asset_kind": "index",
            "revision_external_id": "index-support@v1",
            "sequence": 1,
            "dependencies": [
                {
                    "asset_external_id": "chunk-refund",
                    "revision_external_id": "chunk-refund@v1",
                    "dependency_type": "indexed_from",
                }
            ],
        },
        {
            "observation_kind": "consumption",
            "source_external_id": "external-policy-source",
            "snapshot_external_id": "external-snapshot-v1",
            "asset_external_id": "agent-support",
            "asset_kind": "agent",
            "revision_external_id": "agent-support@run-1",
            "sequence": 1,
            "dependencies": [
                {
                    "asset_external_id": "index-support",
                    "revision_external_id": "index-support@v1",
                    "dependency_type": "consumed",
                }
            ],
        },
    )


def ingest_observations(
    *,
    base_url: str,
    api_key_header: str,
    api_key: str,
) -> tuple[dict[str, Any], ...]:
    endpoint = (
        f"{base_url.rstrip('/')}"
        "/api/lineage/observations"
    )

    responses: list[dict[str, Any]] = []

    for observation in _observations():
        responses.append(
            _post_json(
                url=endpoint,
                api_key_header=api_key_header,
                api_key=api_key,
                payload=observation,
            )
        )

    return tuple(responses)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Submit a lineage observation chain to a live "
            "Semantic Drift Engine API."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv(
            "SDE_BASE_URL",
            DEFAULT_BASE_URL,
        ),
    )
    parser.add_argument(
        "--api-key-header",
        default=os.getenv(
            "SDE_API_KEY_HEADER",
            DEFAULT_API_KEY_HEADER,
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    api_key = os.getenv("SDE_API_KEY")

    if not api_key:
        print(
            "SDE_API_KEY is required.",
            file=sys.stderr,
        )
        return 2

    try:
        responses = ingest_observations(
            base_url=args.base_url,
            api_key_header=args.api_key_header,
            api_key=api_key,
        )
    except ExternalLineageClientError as exc:
        print(
            str(exc),
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "submitted": len(responses),
                "responses": responses,
            },
            indent=2,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())