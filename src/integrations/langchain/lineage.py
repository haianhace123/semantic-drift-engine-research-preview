from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence

from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)


SOURCE_EXTERNAL_ID_KEY = "source_external_id"
SNAPSHOT_EXTERNAL_ID_KEY = "snapshot_external_id"
CHUNK_REVISION_EXTERNAL_ID_KEY = "chunk_revision_external_id"

LangChainConsumerAssetKind = Literal[
    "agent",
    "workflow",
]


class LangChainDocumentLike(Protocol):
    id: str | None
    metadata: Mapping[str, Any]
    page_content: str


@dataclass(frozen=True)
class LangChainIndexMaterialization:
    index_external_id: str
    index_revision_external_id: str
    observations: tuple[LineageObservation, ...]


@dataclass(frozen=True)
class LangChainConsumerBinding:
    consumer_external_id: str
    consumer_revision_external_id: str
    observation: LineageObservation


@dataclass(frozen=True)
class LangChainChunkProvenance:
    asset_external_id: str
    revision_external_id: str
    identity_source: str


@dataclass(frozen=True)
class LangChainIndexPreparation:
    documents: tuple[LangChainDocumentLike, ...]
    provenance: tuple[LangChainChunkProvenance, ...]


@dataclass(frozen=True)
class LangChainContextRevision:
    source_external_id: str
    asset_external_id: str
    revision_external_id: str


def prepare_langchain_documents_for_indexing(
    *,
    documents: Sequence[LangChainDocumentLike],
    previous_documents: Sequence[LangChainDocumentLike] = (),
) -> LangChainIndexPreparation:
    provenance = acquire_langchain_chunk_provenance(
        documents=documents,
        previous_documents=previous_documents,
    )

    original_identity: list[
        tuple[
            LangChainDocumentLike,
            str | None,
            Mapping[str, Any],
        ]
    ] = []

    try:
        for document, chunk in zip(
            documents,
            provenance,
            strict=True,
        ):
            original_identity.append(
                (document, document.id, document.metadata)
            )
            metadata = dict(document.metadata)
            metadata[CHUNK_REVISION_EXTERNAL_ID_KEY] = (
                chunk.revision_external_id
            )

            document.id = chunk.asset_external_id
            document.metadata = metadata
    except (AttributeError, TypeError) as exc:
        for document, document_id, metadata in reversed(
            original_identity
        ):
            try:
                document.id = document_id
                document.metadata = metadata
            except (AttributeError, TypeError):
                pass

        raise ValueError(
            "LangChain document identity must be writable before indexing."
        ) from exc

    return LangChainIndexPreparation(
        documents=tuple(documents),
        provenance=provenance,
    )


def capture_langchain_context_revisions(
    *,
    retrieved_documents: Sequence[LangChainDocumentLike],
) -> tuple[LangChainContextRevision, ...]:
    if not retrieved_documents:
        raise ValueError(
            "Context integrity capture must contain at least one retrieved document."
        )

    revisions: list[LangChainContextRevision] = []
    observed_revisions: set[tuple[str, str, str]] = set()

    for document in retrieved_documents:
        source_external_id = _required_metadata_string(
            document.metadata,
            SOURCE_EXTERNAL_ID_KEY,
        )
        asset_external_id = _required_document_id(document)
        revision_external_id = _required_metadata_string(
            document.metadata,
            CHUNK_REVISION_EXTERNAL_ID_KEY,
        )
        _verify_content_addressed_chunk_revision(
            document=document,
            asset_external_id=asset_external_id,
            revision_external_id=revision_external_id,
        )
        revision_key = (
            source_external_id,
            asset_external_id,
            revision_external_id,
        )

        if revision_key in observed_revisions:
            continue

        observed_revisions.add(revision_key)
        revisions.append(
            LangChainContextRevision(
                source_external_id=source_external_id,
                asset_external_id=asset_external_id,
                revision_external_id=revision_external_id,
            )
        )

    return tuple(revisions)



def _verify_content_addressed_chunk_revision(
    *,
    document: LangChainDocumentLike,
    asset_external_id: str,
    revision_external_id: str,
) -> None:
    """Reject altered bytes for SDE's ``asset:sha256[:16]`` revisions.

    Opaque revisions from third-party ingestion remain identity-only. This
    local check does not attest that the revision is trusted, current, or
    present in an independent authority: HTTP admission still supplies that
    decision, and unknown revisions must remain blocked by the consumer.
    """
    revision_asset, separator, digest = revision_external_id.rpartition(":")
    if not separator or re.fullmatch(r"[0-9a-f]{16}", digest) is None:
        return
    if (
        revision_asset != asset_external_id
        or not isinstance(document.page_content, str)
        or _content_digest(document.page_content) != digest
    ):
        raise ValueError(
            "Retrieved document content does not match its "
            "content-addressed revision."
        )



def acquire_langchain_chunk_provenance(
    *,
    documents: Sequence[LangChainDocumentLike],
    previous_documents: Sequence[LangChainDocumentLike] = (),
) -> tuple[LangChainChunkProvenance, ...]:
    previous_asset_ids_by_text: dict[str, list[str]] = {}

    for document in previous_documents:
        previous_asset_id = _required_document_id(document)
        previous_asset_ids_by_text.setdefault(
            document.page_content,
            [],
        ).append(previous_asset_id)

    acquired: list[LangChainChunkProvenance] = []
    observed_asset_ids: set[str] = set()

    for ordinal, document in enumerate(documents):
        metadata = document.metadata
        source_external_id = _required_metadata_string(
            metadata,
            SOURCE_EXTERNAL_ID_KEY,
        )
        snapshot_external_id = _required_metadata_string(
            metadata,
            SNAPSHOT_EXTERNAL_ID_KEY,
        )

        explicit_asset_id = _optional_document_id(document)

        if explicit_asset_id is not None:
            asset_external_id = explicit_asset_id
            identity_source = "upstream"
        else:
            previous_asset_ids = previous_asset_ids_by_text.get(
                document.page_content,
                [],
            )

            if previous_asset_ids:
                asset_external_id = previous_asset_ids.pop(0)
                identity_source = "exact_content"
            else:
                asset_external_id = (
                    f"{source_external_id}:"
                    f"{snapshot_external_id}:"
                    f"chunk:new:{ordinal}"
                )
                identity_source = "new"

        if asset_external_id in observed_asset_ids:
            raise ValueError(
                "Chunk provenance acquisition produced duplicate logical identity."
            )
        observed_asset_ids.add(asset_external_id)

        revision_external_id = (
            f"{asset_external_id}:"
            f"{_content_digest(document.page_content)}"
        )

        acquired.append(
            LangChainChunkProvenance(
                asset_external_id=asset_external_id,
                revision_external_id=revision_external_id,
                identity_source=identity_source,
            )
        )

    return tuple(acquired)


def capture_langchain_consumer_binding(
    *,
    retrieved_documents: Sequence[LangChainDocumentLike],
    consumer_external_id: str,
    consumer_revision_external_id: str,
    consumer_asset_kind: LangChainConsumerAssetKind,
    binding_snapshot_external_id: str,
    sequence: int,
) -> LangChainConsumerBinding:
    if not retrieved_documents:
        raise ValueError(
            "Consumer binding must contain at least one retrieved document."
        )

    if consumer_asset_kind not in {
        "agent",
        "workflow",
    }:
        raise ValueError(
            "Consumer asset kind must be 'agent' or 'workflow'."
        )

    binding_snapshot_external_id = (
        binding_snapshot_external_id.strip()
    )
    if not binding_snapshot_external_id:
        raise ValueError(
            "Consumer binding snapshot identity must not be empty."
        )

    source_external_id: str | None = None
    dependencies: list[LineageRevisionReference] = []
    observed_revisions: set[tuple[str, str]] = set()

    for document in retrieved_documents:
        chunk_external_id = _required_document_id(document)
        metadata = document.metadata

        document_source_external_id = _required_metadata_string(
            metadata,
            SOURCE_EXTERNAL_ID_KEY,
        )
        _required_metadata_string(
            metadata,
            SNAPSHOT_EXTERNAL_ID_KEY,
        )
        chunk_revision_external_id = _required_metadata_string(
            metadata,
            CHUNK_REVISION_EXTERNAL_ID_KEY,
        )

        if source_external_id is None:
            source_external_id = document_source_external_id
        elif source_external_id != document_source_external_id:
            raise ValueError(
                "Consumer binding spans multiple source identities."
            )

        revision_key = (
            chunk_external_id,
            chunk_revision_external_id,
        )

        if revision_key in observed_revisions:
            continue

        observed_revisions.add(revision_key)
        dependencies.append(
            LineageRevisionReference(
                asset_external_id=chunk_external_id,
                revision_external_id=chunk_revision_external_id,
                dependency_type="consumed",
            )
        )

    assert source_external_id is not None

    observation = LineageObservation(
        observation_kind="consumption",
        source_external_id=source_external_id,
        snapshot_external_id=binding_snapshot_external_id,
        asset_external_id=consumer_external_id,
        asset_kind=consumer_asset_kind,
        revision_external_id=consumer_revision_external_id,
        sequence=sequence,
        dependencies=tuple(dependencies),
    )

    return LangChainConsumerBinding(
        consumer_external_id=consumer_external_id,
        consumer_revision_external_id=consumer_revision_external_id,
        observation=observation,
    )


def capture_langchain_index_materialization(
    *,
    documents: Sequence[LangChainDocumentLike],
    index_external_id: str,
    index_revision_external_id: str,
    sequence: int,
) -> LangChainIndexMaterialization:
    if not documents:
        raise ValueError(
            "Index materialization must contain at least one document."
        )

    source_external_id: str | None = None
    snapshot_external_id: str | None = None
    chunk_observations: list[LineageObservation] = []
    chunk_dependencies: list[LineageRevisionReference] = []
    observed_chunk_ids: set[str] = set()

    for document in documents:
        chunk_external_id = _required_document_id(document)
        metadata = document.metadata

        document_source_external_id = _required_metadata_string(
            metadata,
            SOURCE_EXTERNAL_ID_KEY,
        )
        document_snapshot_external_id = _required_metadata_string(
            metadata,
            SNAPSHOT_EXTERNAL_ID_KEY,
        )
        chunk_revision_external_id = _required_metadata_string(
            metadata,
            CHUNK_REVISION_EXTERNAL_ID_KEY,
        )

        if chunk_external_id in observed_chunk_ids:
            raise ValueError(
                "Index materialization contains duplicate chunk identity."
            )
        observed_chunk_ids.add(chunk_external_id)

        if source_external_id is None:
            source_external_id = document_source_external_id
        elif source_external_id != document_source_external_id:
            raise ValueError(
                "Index materialization spans multiple source identities."
            )

        if snapshot_external_id is None:
            snapshot_external_id = document_snapshot_external_id
        elif snapshot_external_id != document_snapshot_external_id:
            raise ValueError(
                "Index materialization spans multiple snapshot revisions."
            )

        chunk_observations.append(
            LineageObservation(
                observation_kind="materialization",
                source_external_id=document_source_external_id,
                snapshot_external_id=document_snapshot_external_id,
                asset_external_id=chunk_external_id,
                asset_kind="chunk",
                revision_external_id=chunk_revision_external_id,
                sequence=sequence,
            )
        )
        chunk_dependencies.append(
            LineageRevisionReference(
                asset_external_id=chunk_external_id,
                revision_external_id=chunk_revision_external_id,
                dependency_type="indexed_from",
            )
        )

    assert source_external_id is not None
    assert snapshot_external_id is not None

    index_observation = LineageObservation(
        observation_kind="materialization",
        source_external_id=source_external_id,
        snapshot_external_id=snapshot_external_id,
        asset_external_id=index_external_id,
        asset_kind="index",
        revision_external_id=index_revision_external_id,
        sequence=sequence,
        dependencies=tuple(chunk_dependencies),
    )

    return LangChainIndexMaterialization(
        index_external_id=index_external_id,
        index_revision_external_id=index_revision_external_id,
        observations=tuple(
            (*chunk_observations, index_observation)
        ),
    )


def _optional_document_id(
    document: LangChainDocumentLike,
) -> str | None:
    document_id = document.id

    if document_id is None:
        return None

    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError(
            "LangChain document identity must be a non-empty string."
        )

    return document_id


def _content_digest(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:16]


def _required_document_id(
    document: LangChainDocumentLike,
) -> str:
    document_id = document.id

    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError(
            "LangChain document must provide stable logical chunk identity."
        )

    return document_id


def _required_metadata_string(
    metadata: Mapping[str, Any],
    key: str,
) -> str:
    value = metadata.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"LangChain document metadata must provide {key!r}."
        )

    return value