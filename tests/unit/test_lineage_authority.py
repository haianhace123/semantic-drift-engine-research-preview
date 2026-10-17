from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import uuid

import pytest

from scripts import lineage_authority


class FakeSession:
    def __init__(self) -> None:
        self.commit_called = False
        self.rollback_called = False
        self.close_called = False

    def commit(self) -> None:
        self.commit_called = True

    def rollback(self) -> None:
        self.rollback_called = True

    def close(self) -> None:
        self.close_called = True


@dataclass(frozen=True)
class FakePolicy:
    policy_external_id: str
    revision: int


def test_activate_policy_commits_and_reports_authority(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    organization_id = uuid.uuid4()
    session = FakeSession()
    captured: dict[str, object] = {}

    def fake_activate_policy(
        db,
        *,
        organization_id,
        policy,
    ):
        captured["db"] = db
        captured["organization_id"] = organization_id
        captured["policy"] = policy

        return SimpleNamespace(
            state=SimpleNamespace(
                organization_id=organization_id,
                policy=FakePolicy(
                    policy_external_id=policy.policy_external_id,
                    revision=policy.revision,
                ),
            ),
            outcome="activated",
        )

    monkeypatch.setattr(
        lineage_authority,
        "SessionLocal",
        lambda: session,
    )
    monkeypatch.setattr(
        lineage_authority,
        "activate_lineage_invalidation_policy",
        fake_activate_policy,
    )

    args = lineage_authority.build_parser().parse_args(
        [
            "activate-policy",
            "--organization-id",
            str(organization_id),
            "--policy-external-id",
            " rag-invalidation ",
            "--revision",
            "7",
        ]
    )

    result = args.handler(args)

    assert result == 0
    assert captured["db"] is session
    assert captured["organization_id"] == organization_id

    policy = captured["policy"]

    assert policy.policy_external_id == "rag-invalidation"
    assert policy.revision == 7

    assert session.commit_called is True
    assert session.rollback_called is False
    assert session.close_called is True

    output = capsys.readouterr().out

    assert f"organization_id={organization_id}" in output
    assert "policy_external_id=rag-invalidation" in output
    assert "revision=7" in output
    assert "outcome=activated" in output


def test_authorize_execution_commits_and_reports_created_intent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    organization_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    intent_id = uuid.uuid4()
    session = FakeSession()
    captured: dict[str, object] = {}

    def fake_authorize_execution(
        db,
        *,
        organization_id,
        decision_record_id,
    ):
        captured["db"] = db
        captured["organization_id"] = organization_id
        captured["decision_record_id"] = decision_record_id

        return SimpleNamespace(
            record=SimpleNamespace(
                organization_id=organization_id,
                decision_id=decision_record_id,
                id=intent_id,
            ),
            created=True,
        )

    monkeypatch.setattr(
        lineage_authority,
        "SessionLocal",
        lambda: session,
    )
    monkeypatch.setattr(
        lineage_authority,
        "authorize_lineage_invalidation_execution",
        fake_authorize_execution,
    )

    args = lineage_authority.build_parser().parse_args(
        [
            "authorize-execution",
            "--organization-id",
            str(organization_id),
            "--decision-id",
            str(decision_id),
        ]
    )

    result = args.handler(args)

    assert result == 0
    assert captured["db"] is session
    assert captured["organization_id"] == organization_id
    assert captured["decision_record_id"] == decision_id

    assert session.commit_called is True
    assert session.rollback_called is False
    assert session.close_called is True

    output = capsys.readouterr().out

    assert f"organization_id={organization_id}" in output
    assert f"decision_id={decision_id}" in output
    assert f"intent_id={intent_id}" in output
    assert "created=true" in output


def test_authorize_execution_reports_replay(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    organization_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    intent_id = uuid.uuid4()
    session = FakeSession()

    monkeypatch.setattr(
        lineage_authority,
        "SessionLocal",
        lambda: session,
    )
    monkeypatch.setattr(
        lineage_authority,
        "authorize_lineage_invalidation_execution",
        lambda db, *, organization_id, decision_record_id: (
            SimpleNamespace(
                record=SimpleNamespace(
                    organization_id=organization_id,
                    decision_id=decision_record_id,
                    id=intent_id,
                ),
                created=False,
            )
        ),
    )

    args = lineage_authority.build_parser().parse_args(
        [
            "authorize-execution",
            "--organization-id",
            str(organization_id),
            "--decision-id",
            str(decision_id),
        ]
    )

    result = args.handler(args)

    assert result == 0
    assert session.commit_called is True
    assert session.rollback_called is False
    assert session.close_called is True

    output = capsys.readouterr().out

    assert "created=false" in output


@pytest.mark.parametrize(
    "command",
    (
        "activate-policy",
        "authorize-execution",
    ),
)
def test_authority_failure_rolls_back_closes_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    organization_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    session = FakeSession()

    def fail(*args, **kwargs):
        del args
        del kwargs

        raise RuntimeError(
            "authority failed"
        )

    monkeypatch.setattr(
        lineage_authority,
        "SessionLocal",
        lambda: session,
    )

    if command == "activate-policy":
        monkeypatch.setattr(
            lineage_authority,
            "activate_lineage_invalidation_policy",
            fail,
        )
        argv = [
            command,
            "--organization-id",
            str(organization_id),
            "--policy-external-id",
            "rag-invalidation",
            "--revision",
            "7",
        ]
    else:
        monkeypatch.setattr(
            lineage_authority,
            "authorize_lineage_invalidation_execution",
            fail,
        )
        argv = [
            command,
            "--organization-id",
            str(organization_id),
            "--decision-id",
            str(decision_id),
        ]

    args = lineage_authority.build_parser().parse_args(
        argv
    )

    with pytest.raises(
        RuntimeError,
        match="authority failed",
    ):
        args.handler(args)

    assert session.commit_called is False
    assert session.rollback_called is True
    assert session.close_called is True


def test_parser_rejects_invalid_organization_id() -> None:
    parser = lineage_authority.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "authorize-execution",
                "--organization-id",
                "not-a-uuid",
                "--decision-id",
                str(uuid.uuid4()),
            ]
        )


def test_parser_rejects_negative_policy_revision() -> None:
    parser = lineage_authority.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "activate-policy",
                "--organization-id",
                str(uuid.uuid4()),
                "--policy-external-id",
                "rag-invalidation",
                "--revision",
                "-1",
            ]
        )