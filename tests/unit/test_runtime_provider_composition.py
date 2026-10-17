"""Production pipeline admission must pass provider config to its actual worker."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_crawler_gets_only_required_deepseek_provider_environment():
    with (REPO_ROOT / "docker-compose.yml").open(encoding="utf-8") as stream:
        compose = yaml.safe_load(stream)
    crawler = compose["services"]["crawler_scheduler"]
    env = crawler["environment"]

    assert "env_file" not in crawler
    for name in (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_MAX_INPUT_CHARS",
        "DEEPSEEK_TIMEOUT_SECONDS",
    ):
        assert name in env
        assert str(env[name]).startswith("${"), name
    assert "OPENAI_API_KEY" not in env
    assert "GEMINI_API_KEY" not in env


def test_no_second_dashboard_source_or_misleading_auth_monitoring_signal():
    static = REPO_ROOT / "src/static"
    for path in (
        "index.html",
        "css/temporal.css",
        "js/temporal-workspace.js",
        "js/auth-client.js",
        "pages/settings.html",
    ):
        assert not (static / path).exists(), path
    for page in ("login.html", "signup.html"):
        html = (static / page).read_text(encoding="utf-8")
        assert 'class="auth-capability-list"' in html
        assert 'class="auth-signal"' not in html
        assert 'LIVE TEMPORAL SIGNAL' not in html
        assert 'class="auth-signal-status"' not in html


def test_scheduler_and_snapshot_consumers_share_durable_snapshot_volume():
    """DB snapshot paths are not durable without a shared filesystem mount."""
    with (REPO_ROOT / "docker-compose.yml").open(encoding="utf-8") as stream:
        compose = yaml.safe_load(stream)

    services = compose["services"]
    for service_name in ("crawler_scheduler", "api", "vector_worker", "semantic_recovery_worker"):
        assert "snapshot_data:/app/data" in services[service_name]["volumes"], service_name

    # The initializer fixes volume permissions for the non-root runtime user.
    scheduler_deps = services["crawler_scheduler"]["depends_on"]
    assert scheduler_deps["runtime_volume_init"]["condition"] == "service_completed_successfully"
    assert "snapshot_data" in compose["volumes"]

    # Production overlays inherit the base service's volumes; they must not
    # accidentally replace this durable volume or add a different /app/data.
    with (REPO_ROOT / "docker-compose.prod.yml").open(encoding="utf-8") as stream:
        prod = stream.read()
    scheduler_section = prod.split("  crawler_scheduler:\n", 1)[1].split("\n  lineage_invalidation_executor:", 1)[0]
    assert "    volumes:" not in scheduler_section
