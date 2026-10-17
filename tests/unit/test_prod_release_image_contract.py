"""Contract for the production image-only Compose override.

This is a structural assertion, not a substitute for `docker compose config`.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_IMAGE = "${SDE_RELEASE_IMAGE:?SDE_RELEASE_IMAGE is required}"


class ComposeLoader(yaml.SafeLoader):
    pass


def _reset(_loader, _node):
    return "!reset"


def _override(loader, node):
    # Keep the replacement marker visible so the tests verify merge semantics.
    return {"!override": loader.construct_mapping(node, deep=True)}


ComposeLoader.add_constructor("!reset", _reset)
ComposeLoader.add_constructor("!override", _override)


def _load(name):
    return yaml.load((ROOT / name).read_text(encoding="utf-8"), Loader=ComposeLoader)


def test_every_app_build_role_uses_one_prebuilt_image_in_production():
    base = _load("docker-compose.yml")["services"]
    prod = _load("docker-compose.prod.yml")["services"]
    build_roles = {name for name, spec in base.items() if "build" in spec}
    assert build_roles == {
        "api", "runtime_volume_init", "vector_worker", "semantic_recovery_worker",
        "alert_delivery_worker", "crawler_scheduler", "lineage_invalidation_executor",
        "email_worker",
    }
    for role in sorted(build_roles):
        spec = prod[role]
        assert spec["build"] == "!reset", role
        assert spec["image"] == REQUIRED_IMAGE, role
        assert spec["pull_policy"] == "never", role


def test_maintenance_jobs_use_the_same_prebuilt_image():
    prod = _load("docker-compose.prod.yml")["services"]
    for job in ("backup_maintenance", "knowledge_backup_maintenance"):
        assert prod[job]["image"] == REQUIRED_IMAGE
        assert prod[job]["pull_policy"] == "never"
        assert "build" not in prod[job]


def test_production_protection_boundaries_are_retained():
    prod = _load("docker-compose.prod.yml")["services"]
    api = prod["api"]
    assert api["ports"] == "!reset"
    assert api["environment"]["ENVIRONMENT"] == "production"
    assert "/ready" in " ".join(api["healthcheck"]["test"])
    assert api["entrypoint"] == ["python", "-m", "scripts.schema_preflight", "--"]
    for role in (
        "vector_worker", "crawler_scheduler", "alert_delivery_worker",
        "lineage_invalidation_executor", "semantic_recovery_worker",
    ):
        assert prod[role]["entrypoint"] == api["entrypoint"]
    assert prod["caddy"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert prod["db"]["networks"] == ["backend"]
    assert prod["redis"]["networks"] == ["backend"]
    assert prod["runtime_volume_init"]["image"] == REQUIRED_IMAGE
    assert "command" not in prod["runtime_volume_init"]
    assert "volumes" not in prod["runtime_volume_init"]


def test_release_validation_covers_optional_service_profiles():
    base = _load("docker-compose.yml")["services"]
    all_roles = {
        "api", "runtime_volume_init", "vector_worker", "semantic_recovery_worker",
        "alert_delivery_worker", "crawler_scheduler", "lineage_invalidation_executor",
        "email_worker", "backup_maintenance", "knowledge_backup_maintenance",
    }
    prod = _load("docker-compose.prod.yml")["services"]
    declared_profiles = {
        profile for role in all_roles
        for profile in (prod[role].get("profiles") or base[role].get("profiles", []))
    }
    assert declared_profiles == {
        "semantic-recovery", "email", "maintenance", "volume-bootstrap",
    }
    guidance = (ROOT / "docs/production_release_image_contract.md").read_text(encoding="utf-8")
    for profile in declared_profiles:
        assert f"--profile {profile}" in guidance
    assert "validation-only command" in guidance


def test_production_never_implicitly_runs_recursive_chown():
    base = _load("docker-compose.yml")["services"]
    prod = _load("docker-compose.prod.yml")["services"]
    initializer = prod["runtime_volume_init"]
    assert initializer["profiles"] == ["volume-bootstrap"]
    assert base["runtime_volume_init"]["user"] == "0:0"
    assert "chown -R" in " ".join(base["runtime_volume_init"]["command"])
    # The production override must REPLACE, not merge, inherited dependencies.
    expected_dependencies = {
        "api": ("db", "redis"),
        "vector_worker": ("db", "redis"),
        "crawler_scheduler": ("db",),
        "semantic_recovery_worker": ("db",),
    }
    for name, dependencies in expected_dependencies.items():
        assert "runtime_volume_init" in base[name]["depends_on"], name
        assert prod[name]["depends_on"] == {
            "!override": {
                dep: {"condition": "service_healthy"}
                for dep in dependencies
            }
        }, name
    # All other production services have no dependency on initializer.
    for name, spec in prod.items():
        if name not in expected_dependencies:
            assert "runtime_volume_init" not in spec.get("depends_on", {}), name


def test_production_bootstrap_is_not_a_release_prerequisite():
    guidance = (ROOT / "docs/production_release_image_contract.md").read_text(encoding="utf-8")
    assert "Docker Compose 2.24.4" in guidance
    assert "bootstrap requires separate operator approval" in guidance
    assert "Do not enable the volume-bootstrap profile" in guidance
