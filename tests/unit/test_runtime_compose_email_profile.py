from pathlib import Path


def test_email_worker_requires_explicit_compose_profile() -> None:
    compose_file = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    content = compose_file.read_text(encoding="utf-8")
    email_section = content.split("  email_worker:\n", 1)[1].split("\nvolumes:", 1)[0]

    assert "    profiles:\n      - email\n" in email_section
    assert "    restart: unless-stopped" in email_section
    assert "      - scripts.email_worker" in email_section
