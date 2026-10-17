from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from langchain_core.documents import Document
from langchain_postgres import PGVector

from experiments.external_langchain_rag import (
    DEFAULT_PGVECTOR_URL,
    DeterministicEmbeddings,
)


REFUND_DOCUMENT_ID = "external-rag-chunk-refund"
SHIPPING_DOCUMENT_ID = "external-rag-chunk-shipping"

SOURCE_EXTERNAL_ID = "external-rag-policy-source"

SNAPSHOT_V1_EXTERNAL_ID = "external-rag-snapshot-v1"
SNAPSHOT_V2_EXTERNAL_ID = "external-rag-snapshot-v2"

REFUND_V1_REVISION_EXTERNAL_ID = (
    "external-rag-chunk-refund@v1"
)
REFUND_V2_REVISION_EXTERNAL_ID = (
    "external-rag-chunk-refund@v2"
)
SHIPPING_V1_REVISION_EXTERNAL_ID = (
    "external-rag-chunk-shipping@v1"
)


class ExternalRagRepairError(RuntimeError):
    pass


def _required_string(
    value: Any,
    *,
    label: str,
) -> str:
    if not isinstance(value, str):
        raise ExternalRagRepairError(
            f"{label} must be a string."
        )

    normalized = value.strip()

    if not normalized:
        raise ExternalRagRepairError(
            f"{label} must not be empty."
        )

    return normalized


def _refund_v1_document() -> Document:
    return Document(
        id=REFUND_DOCUMENT_ID,
        page_content=(
            "Refund requests are accepted within "
            "30 days of purchase."
        ),
        metadata={
            "source_external_id": SOURCE_EXTERNAL_ID,
            "snapshot_external_id": (
                SNAPSHOT_V1_EXTERNAL_ID
            ),
            "chunk_revision_external_id": (
                REFUND_V1_REVISION_EXTERNAL_ID
            ),
        },
    )


def _refund_v2_document() -> Document:
    return Document(
        id=REFUND_DOCUMENT_ID,
        page_content=(
            "Refund requests are accepted within "
            "14 days of purchase."
        ),
        metadata={
            "source_external_id": SOURCE_EXTERNAL_ID,
            "snapshot_external_id": (
                SNAPSHOT_V2_EXTERNAL_ID
            ),
            "chunk_revision_external_id": (
                REFUND_V2_REVISION_EXTERNAL_ID
            ),
        },
    )


def _shipping_document() -> Document:
    return Document(
        id=SHIPPING_DOCUMENT_ID,
        page_content=(
            "Standard shipping usually arrives "
            "within five business days."
        ),
        metadata={
            "source_external_id": SOURCE_EXTERNAL_ID,
            "snapshot_external_id": (
                SNAPSHOT_V1_EXTERNAL_ID
            ),
            "chunk_revision_external_id": (
                SHIPPING_V1_REVISION_EXTERNAL_ID
            ),
        },
    )


def _vector_store(
    *,
    pgvector_url: str,
    collection_name: str,
) -> PGVector:
    return PGVector(
        embeddings=DeterministicEmbeddings(),
        connection=pgvector_url,
        embedding_length=(
            DeterministicEmbeddings.dimensions
        ),
        collection_name=collection_name,
        use_jsonb=True,
        create_extension=False,
    )


def _retrieve(
    vector_store: PGVector,
    *,
    query: str,
) -> Document | None:
    retrieved = vector_store.similarity_search(
        query,
        k=1,
    )

    if not retrieved:
        return None

    return retrieved[0]


def _document_summary(
    document: Document | None,
) -> dict[str, Any] | None:
    if document is None:
        return None

    return {
        "document_id": _required_string(
            document.id,
            label="Retrieved document id",
        ),
        "page_content": document.page_content,
        "snapshot_external_id": _required_string(
            document.metadata.get(
                "snapshot_external_id"
            ),
            label="Retrieved snapshot identity",
        ),
        "revision_external_id": _required_string(
            document.metadata.get(
                "chunk_revision_external_id"
            ),
            label="Retrieved chunk revision",
        ),
    }


def setup_v1(
    *,
    pgvector_url: str,
    collection_name: str,
) -> dict[str, Any]:
    vector_store = _vector_store(
        pgvector_url=pgvector_url,
        collection_name=collection_name,
    )

    documents = [
        _refund_v1_document(),
        _shipping_document(),
    ]

    inserted_ids = vector_store.add_documents(
        documents
    )

    expected_ids = [
        REFUND_DOCUMENT_ID,
        SHIPPING_DOCUMENT_ID,
    ]

    if inserted_ids != expected_ids:
        raise ExternalRagRepairError(
            "PGVector returned unexpected document "
            f"ids: {inserted_ids!r}"
        )

    refund = _retrieve(
        vector_store,
        query="What is the refund window?",
    )

    summary = _document_summary(refund)

    if (
        summary is None
        or summary["document_id"]
        != REFUND_DOCUMENT_ID
        or summary["revision_external_id"]
        != REFUND_V1_REVISION_EXTERNAL_ID
        or "30 days" not in summary["page_content"]
    ):
        raise ExternalRagRepairError(
            "Initial retrieval did not serve exact "
            "refund v1 knowledge."
        )

    return {
        "phase": "setup-v1",
        "collection_name": collection_name,
        "inserted_ids": inserted_ids,
        "refund": summary,
    }


def verify_stale(
    *,
    pgvector_url: str,
    collection_name: str,
) -> dict[str, Any]:
    vector_store = _vector_store(
        pgvector_url=pgvector_url,
        collection_name=collection_name,
    )

    refund = _document_summary(
        _retrieve(
            vector_store,
            query="What is the refund window?",
        )
    )

    if (
        refund is None
        or refund["document_id"]
        != REFUND_DOCUMENT_ID
        or refund["revision_external_id"]
        != REFUND_V1_REVISION_EXTERNAL_ID
        or "30 days" not in refund["page_content"]
    ):
        raise ExternalRagRepairError(
            "Expected downstream RAG to remain stale "
            "on refund v1 before repair."
        )

    return {
        "phase": "verify-stale",
        "collection_name": collection_name,
        "refund": refund,
    }


def verify_invalidated(
    *,
    pgvector_url: str,
    collection_name: str,
) -> dict[str, Any]:
    vector_store = _vector_store(
        pgvector_url=pgvector_url,
        collection_name=collection_name,
    )

    refund_results = vector_store.similarity_search(
        "What is the refund window?",
        k=10,
    )

    refund_v1_survives = any(
        document.id == REFUND_DOCUMENT_ID
        for document in refund_results
    )

    if refund_v1_survives:
        raise ExternalRagRepairError(
            "Refund v1 still exists after exact "
            "invalidation."
        )

    shipping = _document_summary(
        _retrieve(
            vector_store,
            query="How long does standard shipping take?",
        )
    )

    if (
        shipping is None
        or shipping["document_id"]
        != SHIPPING_DOCUMENT_ID
        or shipping["revision_external_id"]
        != SHIPPING_V1_REVISION_EXTERNAL_ID
        or "five business days"
        not in shipping["page_content"]
    ):
        raise ExternalRagRepairError(
            "Unrelated shipping knowledge did not "
            "survive targeted invalidation."
        )

    return {
        "phase": "verify-invalidated",
        "collection_name": collection_name,
        "refund_v1_present": False,
        "shipping": shipping,
    }


def materialize_v2(
    *,
    pgvector_url: str,
    collection_name: str,
) -> dict[str, Any]:
    vector_store = _vector_store(
        pgvector_url=pgvector_url,
        collection_name=collection_name,
    )

    inserted_ids = vector_store.add_documents(
        [_refund_v2_document()]
    )

    if inserted_ids != [REFUND_DOCUMENT_ID]:
        raise ExternalRagRepairError(
            "PGVector returned unexpected refund v2 "
            f"document ids: {inserted_ids!r}"
        )

    refund = _document_summary(
        _retrieve(
            vector_store,
            query="What is the refund window?",
        )
    )

    if (
        refund is None
        or refund["document_id"]
        != REFUND_DOCUMENT_ID
        or refund["revision_external_id"]
        != REFUND_V2_REVISION_EXTERNAL_ID
        or "14 days" not in refund["page_content"]
    ):
        raise ExternalRagRepairError(
            "Refund v2 was not restored after "
            "materialization."
        )

    return {
        "phase": "materialize-v2",
        "collection_name": collection_name,
        "refund": refund,
    }


def cleanup(
    *,
    pgvector_url: str,
    collection_name: str,
) -> dict[str, Any]:
    vector_store = _vector_store(
        pgvector_url=pgvector_url,
        collection_name=collection_name,
    )
    vector_store.delete_collection()

    return {
        "phase": "cleanup",
        "collection_name": collection_name,
        "deleted": True,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise external LangChain PGVector "
            "knowledge repair phases."
        )
    )
    parser.add_argument(
        "phase",
        choices=(
            "setup-v1",
            "verify-stale",
            "verify-invalidated",
            "materialize-v2",
            "cleanup",
        ),
    )
    parser.add_argument(
        "--collection-name",
        required=True,
    )
    parser.add_argument(
        "--pgvector-url",
        default=os.getenv(
            "EXTERNAL_RAG_PGVECTOR_URL",
            DEFAULT_PGVECTOR_URL,
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    actions = {
        "setup-v1": setup_v1,
        "verify-stale": verify_stale,
        "verify-invalidated": verify_invalidated,
        "materialize-v2": materialize_v2,
        "cleanup": cleanup,
    }

    try:
        result = actions[args.phase](
            pgvector_url=args.pgvector_url,
            collection_name=args.collection_name,
        )
    except ExternalRagRepairError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"External RAG repair experiment failed: {exc}",
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