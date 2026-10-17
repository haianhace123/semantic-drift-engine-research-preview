"""Unit contracts for a read-only maintenance preflight."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import check_knowledge_backup_connections_readonly as gate


def _fake_engine(monkeypatch, *, other=0, invisible=0, visibility=True):
    engine = MagicMock()
    con = engine.connect.return_value.__enter__.return_value
    result = MagicMock()
    result.mappings.return_value.one.return_value = {
        "others": other, "invisible": invisible, "visibility": visibility
    }
    con.execute.side_effect = [None, result]
    monkeypatch.setattr(gate, 'create_engine', lambda *a, **kw: engine)
    return engine, con


def test_gate_passes_only_empty_visible_database(monkeypatch):
    engine, con = _fake_engine(monkeypatch)
    report = gate.inspect('postgresql://user:test@localhost/db')
    assert report['connection_gate_pass'] is True
    assert report['database_writes'] is False
    assert report['backup_writes'] is False
    assert report['other_database_connections'] == 0
    assert con.execute.call_count == 2
    engine.dispose.assert_called_once()


@pytest.mark.parametrize('other,invisible,visibility', [(1, 0, True), (0, 1, True), (0, 0, False), (5, 5, False)])
def test_gate_fails_closed(monkeypatch, other, invisible, visibility):
    _fake_engine(monkeypatch, other=other, invisible=invisible, visibility=visibility)
    assert gate.inspect('postgresql://user:test@localhost/db')['connection_gate_pass'] is False


def test_empty_url_refused_and_main_does_not_echo_secrets(monkeypatch, capsys):
    with pytest.raises(ValueError, match='explicitly provided'):
        gate.inspect('')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:secretpassword@localhost/db')
    monkeypatch.setattr(gate, 'inspect', lambda _: (_ for _ in ()).throw(ValueError('secretpassword')))
    assert gate.main(['--require-quiescent']) == 1
    assert 'secretpassword' not in capsys.readouterr().err


def test_status_guard_covers_all_writer_services():
    text = (Path(__file__).resolve().parents[2] / 'scripts/check_knowledge_backup_window.ps1').read_text()
    for name in ('api', 'crawler_scheduler', 'vector_worker', 'semantic_recovery_worker',
                 'alert_delivery_worker', 'lineage_invalidation_executor', 'email_worker',
                 'knowledge_backup_maintenance', 'backup_maintenance'):
        assert "'" + name + "'" in text
    assert 'docker compose ps --services --status running' in text
    assert '--no-deps --pull never' in text
    assert '--build' not in text
    assert 'down -v' not in text
