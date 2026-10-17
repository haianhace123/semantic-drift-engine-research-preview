from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from typing import Any
from uuid import uuid4

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_postgres import PGVector

from src.integrations.integrity_admission import IntegrityAdmissionPolicy
from src.integrations.integrity_client import IntegrityRevision, SDEIntegrityClient
from src.integrations.langchain import (
    LangChainContextNotAdmittedError,
    admit_langchain_context,
    capture_langchain_consumer_binding,
    capture_langchain_index_materialization,
)


DEFAULT_SDE_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_API_KEY_HEADER = "X-API-Key"
DEFAULT_PGVECTOR_URL = (
    "postgresql+psycopg://external_rag:"
    "external_rag_dev@127.0.0.1:55432/external_rag"
)

SOURCE_EXTERNAL_ID = "external-rag-policy-source"
SNAPSHOT_EXTERNAL_ID = "external-rag-snapshot-v1"
INDEX_EXTERNAL_ID = "external-rag-support-index"
INDEX_REVISION_EXTERNAL_ID = "external-rag-support-index@v1"
CONSUMER_EXTERNAL_ID = "external-rag-support-agent"
CONSUMER_REVISION_EXTERNAL_ID = "external-rag-support-agent@run-1"


class ExternalRagExperimentError(RuntimeError):
    pass


class DeterministicEmbeddings(Embeddings):
    dimensions = 8

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        return [
            self._embed(text)
            for text in texts
        ]

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        return self._embed(text)

    @classmethod
    def _embed(
        cls,
        text: str,
    ) -> list[float]:
        vector = [0.0] * cls.dimensions

        for index, byte in enumerate(
            text.encode("utf-8")
        ):
            vector[index % cls.dimensions] += float(byte)

        magnitude = math.sqrt(
            sum(
                value * value
                for value in vector
            )
        )

        if magnitude == 0.0:
            return vector

        return [
            value / magnitude
            for value in vector
        ]


def _source_documents() -> list[Document]:
    return [
        Document(
            id="external-rag-chunk-refund",
            page_content=(
                "Refund requests are accepted within "
                "30 days of purchase."
            ),
            metadata={
                "source_external_id": SOURCE_EXTERNAL_ID,
                "snapshot_external_id": SNAPSHOT_EXTERNAL_ID,
                "chunk_revision_external_id": (
                    "external-rag-chunk-refund@v1"
                ),
            },
        ),
        Document(
            id="external-rag-chunk-shipping",
            page_content=(
                "Standard shipping usually arrives "
                "within five business days."
            ),
            metadata={
                "source_external_id": SOURCE_EXTERNAL_ID,
                "snapshot_external_id": SNAPSHOT_EXTERNAL_ID,
                "chunk_revision_external_id": (
                    "external-rag-chunk-shipping@v1"
                ),
            },
        ),
    ]


def _required_string(
    value: Any,
    *,
    label: str,
) -> str:
    if not isinstance(value, str):
        raise ExternalRagExperimentError(
            f"{label} must be a string."
        )

    normalized = value.strip()

    if not normalized:
        raise ExternalRagExperimentError(
            f"{label} must not be empty."
        )

    return normalized


def _post_json(
    *,
    url: str,
    api_key_header: str,
    api_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
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
            response_body = response.read().decode(
                "utf-8"
            )
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )
        raise ExternalRagExperimentError(
            f"HTTP {exc.code} from {url}: "
            f"{response_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ExternalRagExperimentError(
            f"Unable to reach {url}: {exc.reason}"
        ) from exc

    if not response_body:
        return {}

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ExternalRagExperimentError(
            f"Non-JSON response from {url}: "
            f"{response_body}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ExternalRagExperimentError(
            f"Unexpected response from {url}: "
            f"{parsed!r}"
        )

    return parsed


def _query_impact(
    *,
    base_url: str,
    api_key_header: str,
    api_key: str,
    source_external_id: str,
    asset_external_id: str,
    revision_external_id: str,
) -> dict[str, Any]:
    endpoint = (
        f"{base_url.rstrip('/')}"
        "/api/lineage/impact"
    )

    return _post_json(
        url=endpoint,
        api_key_header=api_key_header,
        api_key=api_key,
        payload={
            "source_external_id": source_external_id,
            "asset_external_id": asset_external_id,
            "revision_external_id": revision_external_id,
        },
    )


def _proof_admission_policy(
    *,
    allow_unknown_for_lineage_proof: bool,
) -> IntegrityAdmissionPolicy:
    """An unknown revision is never silently accepted as trusted context.

    An explicit exception exists only for the historical lineage experiment,
    which does not call an LLM or claim production knowledge validity.
    """
    if type(allow_unknown_for_lineage_proof) is not bool:
        raise ValueError("Lineage proof policy must be a boolean.")
    return IntegrityAdmissionPolicy(
        unknown="proceed" if allow_unknown_for_lineage_proof else "block"
    )


def run_experiment(
    *,
    base_url: str,
    api_key_header: str,
    api_key: str,
    pgvector_url: str,
    allow_unknown_for_lineage_proof: bool = False,
) -> dict[str, Any]:
    collection_name = (
        f"sde_external_rag_{uuid4().hex}"
    )
    vector_store = PGVector(
        embeddings=DeterministicEmbeddings(),
        connection=pgvector_url,
        embedding_length=(
            DeterministicEmbeddings.dimensions
        ),
        collection_name=collection_name,
        use_jsonb=True,
        create_extension=False,
    )

    documents = _source_documents()

    try:
        inserted_ids = vector_store.add_documents(
            documents
        )

        expected_ids = [
            _required_string(
                document.id,
                label="Source document id",
            )
            for document in documents
        ]

        if inserted_ids != expected_ids:
            raise ExternalRagExperimentError(
                "PGVector returned unexpected "
                f"document ids: {inserted_ids!r}"
            )

        index_materialization = (
            capture_langchain_index_materialization(
                documents=documents,
                index_external_id=INDEX_EXTERNAL_ID,
                index_revision_external_id=(
                    INDEX_REVISION_EXTERNAL_ID
                ),
                sequence=1,
            )
        )
        if api_key_header != DEFAULT_API_KEY_HEADER:
            raise ExternalRagExperimentError(
                "Runtime integrity checks require the supported X-API-Key header."
            )

        with SDEIntegrityClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=10.0,
        ) as integrity_client:
            materialization_publications = [
                integrity_client.publish_lineage_observation(observation)
                for observation in index_materialization.observations
            ]

        retrieved = vector_store.similarity_search(
            "What is the refund window?",
            k=1,
        )

        if len(retrieved) != 1:
            raise ExternalRagExperimentError(
                "Expected exactly one retrieved "
                f"document, got {len(retrieved)}."
            )

        retrieved_document = retrieved[0]
        retrieved_asset_id = _required_string(
            retrieved_document.id,
            label="Retrieved document id",
        )
        retrieved_source_id = _required_string(
            retrieved_document.metadata.get(
                "source_external_id"
            ),
            label="Retrieved source identity",
        )
        retrieved_snapshot_id = _required_string(
            retrieved_document.metadata.get(
                "snapshot_external_id"
            ),
            label="Retrieved snapshot identity",
        )
        retrieved_revision_id = _required_string(
            retrieved_document.metadata.get(
                "chunk_revision_external_id"
            ),
            label="Retrieved chunk revision",
        )

        if (
            retrieved_asset_id
            != "external-rag-chunk-refund"
        ):
            raise ExternalRagExperimentError(
                "Retrieval did not return the expected "
                "refund chunk."
            )

        if (
            retrieved_source_id
            != SOURCE_EXTERNAL_ID
            or retrieved_snapshot_id
            != SNAPSHOT_EXTERNAL_ID
            or retrieved_revision_id
            != "external-rag-chunk-refund@v1"
        ):
            raise ExternalRagExperimentError(
                "Exact causal provenance did not "
                "survive the PGVector round trip."
            )

        with SDEIntegrityClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=10.0,
        ) as integrity_client:
            admitted = admit_langchain_context(
                client=integrity_client,
                serving_revision=IntegrityRevision(
                    source_external_id=SOURCE_EXTERNAL_ID,
                    asset_external_id=INDEX_EXTERNAL_ID,
                    revision_external_id=(
                        INDEX_REVISION_EXTERNAL_ID
                    ),
                ),
                retrieved_documents=retrieved,
                policy=_proof_admission_policy(
                    allow_unknown_for_lineage_proof=allow_unknown_for_lineage_proof,
                ),
            )

        # Provenance is recorded only for a context explicitly admitted by
        # the selected experimental policy, never after a failed guard.
        integrity = admitted.assessment
        admission = admitted.admission
        consumer_binding = (
            capture_langchain_consumer_binding(
                retrieved_documents=admitted.documents,
                consumer_external_id=CONSUMER_EXTERNAL_ID,
                consumer_revision_external_id=(
                    CONSUMER_REVISION_EXTERNAL_ID
                ),
                consumer_asset_kind="agent",
                binding_snapshot_external_id=(
                    SNAPSHOT_EXTERNAL_ID
                ),
                sequence=1,
            )
        )

        with SDEIntegrityClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=10.0,
        ) as integrity_client:
            consumer_publication = (
                integrity_client.publish_lineage_observation(
                    consumer_binding.observation
                )
            )

        observations_submitted = (
            len(index_materialization.observations) + 1
        )
        publications = [
            *materialization_publications,
            consumer_publication,
        ]

        impact = _query_impact(
            base_url=base_url,
            api_key_header=api_key_header,
            api_key=api_key,
            source_external_id=SOURCE_EXTERNAL_ID,
            asset_external_id=(
                "external-rag-chunk-refund"
            ),
            revision_external_id=(
                "external-rag-chunk-refund@v1"
            ),
        )

        expected_root = {
            "source_external_id": SOURCE_EXTERNAL_ID,
            "asset_external_id": (
                "external-rag-chunk-refund"
            ),
            "revision_external_id": (
                "external-rag-chunk-refund@v1"
            ),
        }

        if impact.get("root") != expected_root:
            raise ExternalRagExperimentError(
                "Impact analysis returned an "
                f"unexpected root: {impact.get('root')!r}"
            )

        impacted_revisions = impact.get(
            "impacted_revisions"
        )

        if not isinstance(
            impacted_revisions,
            list,
        ):
            raise ExternalRagExperimentError(
                "Impact analysis did not return "
                "impacted_revisions as a list."
            )

        expected_impacted = [
            {
                "source_external_id": SOURCE_EXTERNAL_ID,
                "asset_external_id": (
                    CONSUMER_EXTERNAL_ID
                ),
                "revision_external_id": (
                    CONSUMER_REVISION_EXTERNAL_ID
                ),
            },
            {
                "source_external_id": SOURCE_EXTERNAL_ID,
                "asset_external_id": INDEX_EXTERNAL_ID,
                "revision_external_id": (
                    INDEX_REVISION_EXTERNAL_ID
                ),
            },
        ]

        if impacted_revisions != expected_impacted:
            raise ExternalRagExperimentError(
                "Impact analysis returned an "
                "unexpected blast radius: "
                f"{impacted_revisions!r}"
            )

        paths = impact.get("paths")

        if not isinstance(paths, list):
            raise ExternalRagExperimentError(
                "Impact analysis did not return "
                "paths as a list."
            )

        paths_by_target = {
            _required_string(
                path.get("target", {}).get(
                    "asset_external_id"
                ),
                label="Impact path target",
            ): path
            for path in paths
            if isinstance(path, dict)
            and isinstance(
                path.get("target"),
                dict,
            )
        }

        if set(paths_by_target) != {
            INDEX_EXTERNAL_ID,
            CONSUMER_EXTERNAL_ID,
        }:
            raise ExternalRagExperimentError(
                "Impact analysis returned unexpected "
                "causal path targets."
            )

        expected_paths = {
            INDEX_EXTERNAL_ID: [
                "external-rag-chunk-refund",
                INDEX_EXTERNAL_ID,
            ],
            CONSUMER_EXTERNAL_ID: [
                "external-rag-chunk-refund",
                CONSUMER_EXTERNAL_ID,
            ],
        }

        for (
            target_external_id,
            expected_path,
        ) in expected_paths.items():
            revisions = paths_by_target[
                target_external_id
            ].get("revisions")

            if not isinstance(revisions, list):
                raise ExternalRagExperimentError(
                    "Impact causal path revisions "
                    "must be a list."
                )

            actual_path = [
                _required_string(
                    revision.get(
                        "asset_external_id"
                    ),
                    label=(
                        "Impact path revision "
                        "asset identity"
                    ),
                )
                for revision in revisions
                if isinstance(revision, dict)
            ]

            if actual_path != expected_path:
                raise ExternalRagExperimentError(
                    "Impact analysis returned an "
                    "unexpected causal path for "
                    f"{target_external_id}: "
                    f"{actual_path!r}"
                )

        unrelated_asset_external_id = (
            "external-rag-chunk-shipping"
        )

        if any(
            revision.get("asset_external_id")
            == unrelated_asset_external_id
            for revision in impacted_revisions
            if isinstance(revision, dict)
        ):
            raise ExternalRagExperimentError(
                "Impact analysis incorrectly included "
                "the unrelated shipping chunk."
            )

        return {
            "collection_name": collection_name,
            "inserted_ids": inserted_ids,
            "retrieved": {
                "asset_external_id": (
                    retrieved_asset_id
                ),
                "revision_external_id": (
                    retrieved_revision_id
                ),
            },
            "observations_submitted": observations_submitted,
            "publications": [
                {
                    "id": str(publication.id),
                    "created": publication.created,
                }
                for publication in publications
            ],
            "integrity": {
                "statuses": [
                    assessment.status
                    for assessment in integrity.assessments
                ],
                "has_unknown": integrity.summary.has_unknown,
                "has_unresolved_impact": (
                    integrity.summary.has_unresolved_impact
                ),
                "admission": {
                    "outcome": admission.outcome,
                    "reason": admission.reason,
                    "unknown_policy": (
                        "lineage_proof_opt_in"
                        if allow_unknown_for_lineage_proof
                        else "block"
                    ),
                    "not_a_global_validity_attestation": True,
                },
            },
            "impact": impact,
        }
    finally:
        vector_store.delete_collection()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run an external LangChain PGVector RAG "
            "lineage acquisition experiment against "
            "a live Semantic Drift Engine API."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv(
            "SDE_BASE_URL",
            DEFAULT_SDE_BASE_URL,
        ),
    )
    parser.add_argument(
        "--api-key-header",
        default=os.getenv(
            "SDE_API_KEY_HEADER",
            DEFAULT_API_KEY_HEADER,
        ),
    )
    parser.add_argument(
        "--pgvector-url",
        default=os.getenv(
            "EXTERNAL_RAG_PGVECTOR_URL",
            DEFAULT_PGVECTOR_URL,
        ),
    )

    parser.add_argument(
        "--allow-unknown-for-lineage-proof",
        action="store_true",
        help=(
            "Diagnostics only: accept unknown revisions to observe lineage "
            "and impact. NOT a trusted-context or production-RAG proof."
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
        result = run_experiment(
            base_url=args.base_url,
            api_key_header=args.api_key_header,
            api_key=api_key,
            pgvector_url=args.pgvector_url,
            allow_unknown_for_lineage_proof=(
                args.allow_unknown_for_lineage_proof
            ),
        )
    except LangChainContextNotAdmittedError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ExternalRagExperimentError as exc:
        print(
            str(exc),
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            f"External RAG experiment failed: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())