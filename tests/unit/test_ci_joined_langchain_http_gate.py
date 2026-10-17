"""Fail-closed runner contract; no Docker or database required."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_joined_langchain_http_gate as gate


ENV = {
    "RUN_DB_INTEGRATION_TESTS": "1",
    "ENVIRONMENT": "test",
    "DATABASE_URL": "postgresql://sde_integration:sde_local_test_only@db:5432/sde_integration",
    "REDIS_URL": "redis://redis:6379/0",
    "SDE_LINEAGE_ACCEPTANCE_API_URL": "http://api:8000",
    "SDE_JOINED_DOWNSTREAM_DATABASE_URL": (
        "postgresql://downstream_test:downstream_test@"
        "integration_downstream_db:5432/downstream_test"
    ),
}


def _env(monkeypatch, **changes):
    for key, value in {**ENV, **changes}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


@pytest.mark.parametrize("changes", [
    {"ENVIRONMENT": "production"},
    {"RUN_DB_INTEGRATION_TESTS": "0"},
    {"DATABASE_URL": "postgresql://prod:secret@db:5432/production"},
    {"SDE_JOINED_DOWNSTREAM_DATABASE_URL": None},
    {"SDE_JOINED_DOWNSTREAM_DATABASE_URL": "postgresql://x:y@some-production-host:5432/downstream_test"},
    {"SDE_JOINED_DOWNSTREAM_DATABASE_URL": ENV["SDE_JOINED_DOWNSTREAM_DATABASE_URL"] + "?sslmode=disable"},
    {"SDE_LINEAGE_ACCEPTANCE_API_URL": "http://host.docker.internal:8000"},
])
def test_bad_identity_refused(monkeypatch, changes):
    _env(monkeypatch, **changes)
    with pytest.raises(ValueError):
        gate.require_joined_disposable_environment()


def test_precise_disposable_target_allowed(monkeypatch):
    _env(monkeypatch)
    gate.require_joined_disposable_environment()


@pytest.mark.parametrize("kind,expected", [
    ("passed", 0),
    ("skipped", 1),
    ("failure", 1),
    ("missing", 1),
])
def test_gate_refuses_skip_fail_missing_report(monkeypatch, kind, expected):
    _env(monkeypatch)

    def fake_run(command, check=False):
        assert check is False
        assert "test_joined_langchain_http_admission.py" in " ".join(command)
        report = Path(command[-1].split("=", 1)[1])
        if kind != "missing":
            body = "" if kind == "passed" else (
                '<skipped message="missing sdk" />' if kind == "skipped" else
                '<failure message="wrong admission" />'
            )
            report.write_text(
                '<testsuite tests="1"><testcase name="joined" classname="acceptance">'
                f'{body}</testcase></testsuite>', encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate.main() == expected


def test_gate_refuses_pytest_failure_even_with_valid_report(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(
        gate.subprocess, "run",
        lambda command, check=False: subprocess.CompletedProcess(command, 1),
    )
    assert gate.main() == 1
