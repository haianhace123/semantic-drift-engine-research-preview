"""Backup access is confined to an explicit maintenance workload."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_api_and_workers_cannot_read_entire_database_backups():
    compose = _compose()
    services = compose["services"]
    for name in (
        "api",
        "vector_worker",
        "crawler_scheduler",
        "semantic_recovery_worker",
        "alert_delivery_worker",
        "lineage_invalidation_executor",
        "email_worker",
    ):
        mounts = services[name].get("volumes", [])
        assert not any("backup_data:" in mount for mount in mounts), name
    assert "snapshot_data:/app/data" in services["api"]["volumes"]


def test_backup_job_is_explicit_ephemeral_and_has_minimum_secrets():
    compose = _compose()
    services = compose["services"]
    job = services["backup_maintenance"]

    assert job["profiles"] == ["maintenance"]
    assert job["restart"] == "no"
    assert job["command"] == ["python", "-m", "scripts.backup_database"]
    assert job["volumes"] == ["backup_data:/app/backups"]
    assert "env_file" not in job
    assert "ports" not in job
    assert "expose" not in job
    assert set(job["environment"]) == {
        "DATABASE_URL", "BACKUP_DIR", "BACKUP_RETENTION_DAYS"
    }
    assert job["environment"]["BACKUP_DIR"] == "/app/backups"
    # Running maintenance must never trigger runtime_volume_init or start db.
    assert "depends_on" not in job
    assert "build" not in job
    assert job["pull_policy"] == "never"
    assert "SDE_MAINTENANCE_IMAGE" in job["image"]
    assert "depends_on" not in services["knowledge_backup_maintenance"]
    assert "build" not in services["knowledge_backup_maintenance"]
    assert services["knowledge_backup_maintenance"]["pull_policy"] == "never"
    assert "backup_data:/app/backups" in services["runtime_volume_init"]["volumes"]
    assert "backup_data" in compose["volumes"]


def test_production_maintenance_has_database_network():
    prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    section = prod.split("  backup_maintenance:\n", 1)[1].split(
        "\n  knowledge_backup_maintenance:", 1
    )[0]
    assert "    networks:\n      - backend\n" in section
