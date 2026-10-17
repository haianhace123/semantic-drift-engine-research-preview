from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest

from src.integrations.langchain.recovery_context_admission import (
    PilotRecoveryContextNotAdmittedError,
    admit_pilot_recovery_document,
)

SOURCE = uuid.uuid4()
SNAPSHOT = uuid.uuid4()
CERTIFICATE = uuid.uuid4()
CONTENT = "Refunds accepted within seven days."
DIGEST = hashlib.sha256(CONTENT.encode("utf-8")).hexdigest()
CHUNK = f"sde-pilot-chunk:{SOURCE}:0"
REVISION = f"{CHUNK}:{DIGEST[:16]}"


def document():
    return SimpleNamespace(
        id=CHUNK,
        page_content=CONTENT,
        metadata={
            "source_external_id": f"sde-pilot-source:{SOURCE}",
            "snapshot_external_id": str(SNAPSHOT),
            "chunk_revision_external_id": REVISION,
        },
    )


class Client:
    def __init__(self, *, status=200, result=None, error=None):
        self.status = status
        self.result = result
        self.error = error
        self.calls = []

    def post(self, route, *, json):
        self.calls.append((route, json))
        if self.error:
            raise self.error
        return SimpleNamespace(
            status_code=self.status,
            headers={"cache-control": "no-store, private"},
            json=lambda: self.result,
        )


def success():
    return {
        "outcome": "proceed",
        "reason": "verified_successor",
        "snapshot_id": str(SNAPSHOT),
        "certificate_id": str(CERTIFICATE),
        "chunk_content_sha256": DIGEST,
        "request_scoped": True,
    }


def admit(client, doc=None):
    return admit_pilot_recovery_document(
        http_client=client,
        source_id=SOURCE,
        snapshot_id=SNAPSHOT,
        retrieved_document=doc if doc is not None else document(),
    )


def test_valid_response_returns_only_frozen_content():
    client = Client(result=success())
    doc = document()

    admitted = admit(client, doc)

    assert admitted.content == CONTENT
    assert admitted.content_sha256 == DIGEST
    assert admitted.certificate_id == CERTIFICATE
    assert len(client.calls) == 1
    route, request = client.calls[0]
    assert route.endswith("/recovery-context-admission/query")
    assert request["frozen_content_sha256"] == DIGEST

    doc.page_content = "Content changed after admission."
    assert admitted.content == CONTENT


@pytest.mark.parametrize("change", [
    {"id": "wrong-chunk"},
    {"page_content": "Different content"},
    {"metadata": {}},
])
def test_changed_or_untrusted_document_blocked_before_http(change):
    doc = document()
    for key, value in change.items():
        setattr(doc, key, value)

    client = Client(result=success())
    with pytest.raises(PilotRecoveryContextNotAdmittedError):
        admit(client, doc)

    assert client.calls == []


def test_old_snapshot_is_not_accepted():
    doc = document()
    doc.metadata["snapshot_external_id"] = str(uuid.uuid4())

    client = Client(result=success())
    with pytest.raises(PilotRecoveryContextNotAdmittedError):
        admit(client, doc)

    assert client.calls == []


def test_server_block_never_returns_content():
    client = Client(result={
        "outcome": "block",
        "reason": "evidence_unavailable",
        "request_scoped": True,
    })
    with pytest.raises(
        PilotRecoveryContextNotAdmittedError,
        match="evidence_unavailable",
    ):
        admit(client)


def test_forged_proceed_digest_is_rejected():
    forged = success()
    forged["chunk_content_sha256"] = "0" * 64

    with pytest.raises(
        PilotRecoveryContextNotAdmittedError,
        match="verification_failed",
    ):
        admit(Client(result=forged))


def test_wrong_snapshot_in_response_is_rejected():
    forged = success()
    forged["snapshot_id"] = str(uuid.uuid4())

    with pytest.raises(PilotRecoveryContextNotAdmittedError):
        admit(Client(result=forged))


def test_unauthorized_http_is_denied():
    with pytest.raises(
        PilotRecoveryContextNotAdmittedError,
        match="denied",
    ):
        admit(Client(status=401))


def test_transport_failure_has_no_fallback_or_secret_leak():
    client = Client(error=RuntimeError("private database password"))

    with pytest.raises(PilotRecoveryContextNotAdmittedError) as error:
        admit(client)

    assert error.value.reason == "verification_failed"
    assert "password" not in str(error.value)


def test_invalid_certificate_identity_is_rejected():
    forged = success()
    forged["certificate_id"] = "not-a-uuid"

    with pytest.raises(PilotRecoveryContextNotAdmittedError):
        admit(Client(result=forged))
