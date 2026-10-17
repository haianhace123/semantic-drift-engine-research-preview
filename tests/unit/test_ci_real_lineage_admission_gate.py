"""Fail-closed identity contract for disposable authority acceptance."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ci_real_lineage_admission_gate import (
    ACCEPTANCE_MODULES,
    EXPECTED_CASES,
    EXPECTED_MATERIALITY_CASES,
    MATERIALITY_MODULES,
    main,
    require_disposable_lineage_databases,
)


def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("RUN_DB_INTEGRATION_TESTS", "1")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://sde_integration:sde_local_test_only@db:5432/sde_integration",
    )
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SDE_LINEAGE_ACCEPTANCE_API_URL", "http://api:8000")


def test_expected_acceptance_cases_cover_repair_and_temporal_enforcement() -> None:
    assert EXPECTED_CASES == 3
    assert len(ACCEPTANCE_MODULES) == 2
    assert ACCEPTANCE_MODULES[0].endswith("test_lineage_physical_recovery_acceptance.py")
    assert ACCEPTANCE_MODULES[1].endswith("test_temporal_event_http_admission_acceptance.py")


def test_disposable_database_identity_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_env(monkeypatch)
    require_disposable_lineage_databases()


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "postgresql://sde_integration:sde_local_test_only@db:5432/production",
        "postgresql://sde_integration:sde_local_test_only@other:5432/sde_integration",
        "postgresql://admin:secret@db:5432/sde_integration",
        "postgresql://sde_integration:sde_local_test_only@db:5432/sde_integration?x=1",
        "",
    ],
)
def test_rejects_nonisolated_database(
    monkeypatch: pytest.MonkeyPatch, unsafe_url: str
) -> None:
    _isolated_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", unsafe_url)
    with pytest.raises(ValueError, match="disposable"):
        require_disposable_lineage_databases()


def test_rejects_wrong_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://prod-redis:6379/0")
    with pytest.raises(ValueError, match="disposable"):
        require_disposable_lineage_databases()


def test_rejects_disabled_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_env(monkeypatch)
    monkeypatch.setenv("RUN_DB_INTEGRATION_TESTS", "0")
    with pytest.raises(ValueError, match="disposable"):
        require_disposable_lineage_databases()


@pytest.mark.parametrize("unsafe_url", [
    "http://localhost:8000", "http://host.docker.internal:8000",
    "https://api:8000", "http://api:8001", "http://api:8000/path",
    "http://api:8000?token=secret", "http://user:pw@api:8000", "",
])
def test_rejects_nonisolated_http_api(monkeypatch: pytest.MonkeyPatch, unsafe_url: str) -> None:
    _isolated_env(monkeypatch)
    monkeypatch.setenv("SDE_LINEAGE_ACCEPTANCE_API_URL", unsafe_url)
    with pytest.raises(ValueError, match="disposable"):
        require_disposable_lineage_databases()


def _stub_gate_pytest(monkeypatch: pytest.MonkeyPatch, *, cases: int, skipped: int = 0):
    recorded: list[list[str]] = []

    def run(command, *, check: bool):
        assert check is False
        recorded.append(command)
        report = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        suite = ET.Element("testsuite")
        for idx in range(cases):
            case = ET.SubElement(suite, "testcase", name=f"case_{idx}")
            if idx < skipped:
                ET.SubElement(case, "skipped")
        ET.ElementTree(suite).write(report, encoding="utf-8", xml_declaration=True)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("scripts.ci_real_lineage_admission_gate.subprocess.run", run)
    return recorded


def test_materiality_cli_runs_two_non_skipped_cases(monkeypatch: pytest.MonkeyPatch, capsys):
    _isolated_env(monkeypatch)
    calls = _stub_gate_pytest(monkeypatch, cases=EXPECTED_MATERIALITY_CASES)
    assert main(["--materiality-only"]) == 0
    assert len(calls) == 1
    assert all(module in calls[0] for module in MATERIALITY_MODULES)
    assert all(module not in calls[0] for module in ACCEPTANCE_MODULES)
    assert "PASS: Isolated materiality: 2 passed, 0 skipped." in capsys.readouterr().out


@pytest.mark.parametrize("cases,skipped", [(1, 0), (3, 0), (2, 1)])
def test_materiality_cli_fails_incomplete_or_skipped_tests(
    monkeypatch: pytest.MonkeyPatch, cases: int, skipped: int
):
    _isolated_env(monkeypatch)
    _stub_gate_pytest(monkeypatch, cases=cases, skipped=skipped)
    assert main(["--materiality-only"]) == 1


def test_default_gate_keeps_existing_three_cases(monkeypatch: pytest.MonkeyPatch):
    _isolated_env(monkeypatch)
    calls = _stub_gate_pytest(monkeypatch, cases=EXPECTED_CASES)
    assert main([]) == 0
    assert all(module in calls[0] for module in ACCEPTANCE_MODULES)
