from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.config import Settings


def production_settings(**overrides) -> Settings:
    values = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "s" * 32,
        "WEBHOOK_SECRET_ENCRYPTION_KEY": "webhook-key",
        "COOKIE_SECURE": True,
        "APP_BASE_URL": "https://drift.example.com",
    }
    values.update(overrides)

    return Settings(**values)


def test_production_requires_webhook_secret_encryption_key() -> None:
    with pytest.raises(
        ValidationError,
        match="SDE_WEBHOOK_SECRET_ENCRYPTION_KEY is required in production",
    ):
        production_settings(
            WEBHOOK_SECRET_ENCRYPTION_KEY="",
        )


def test_production_accepts_configured_webhook_secret_encryption_key() -> None:
    settings = production_settings()

    assert settings.ENVIRONMENT == "production"
    assert settings.WEBHOOK_SECRET_ENCRYPTION_KEY == "webhook-key"
