"""
Application configuration loader.
"""

import os

from dotenv import load_dotenv
from pydantic import model_validator
from pydantic_settings import BaseSettings


load_dotenv()


class Settings(BaseSettings):
    """Application settings with explicit production safety checks."""

    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").lower()
    TRUST_PROXY_HEADERS: bool = (
        os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"
    )

    # ========================================================
    # DATABASE
    # ========================================================

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://user:pass@localhost:5432/driftdb",
    )

    DOWNSTREAM_DATABASE_URLS: str = os.getenv(
        "SDE_DOWNSTREAM_DATABASE_URLS",
        "{}",
    )

    # ========================================================
    # RECOVERY CONTEXT ADMISSION (BOUNDED, DEFAULT OFF)
    # An operator-owned, absolute raw-snapshot root is required to enable.
    SDE_RECOVERY_CONTEXT_ADMISSION_ENABLED: bool = (
        os.getenv('SDE_RECOVERY_CONTEXT_ADMISSION_ENABLED', 'false').lower() == 'true'
    )
    SDE_RECOVERY_SNAPSHOT_ROOT: str = os.getenv('SDE_RECOVERY_SNAPSHOT_ROOT', '')
    # Separate admin-session write gate. Never implied by read admission.
    SDE_RECOVERY_CERTIFICATE_ISSUANCE_ENABLED: bool = (
        os.getenv('SDE_RECOVERY_CERTIFICATE_ISSUANCE_ENABLED', 'false').lower() == 'true'
    )

    # REDIS
    # ========================================================

    REDIS_URL: str = os.getenv(
        "REDIS_URL",
        "redis://localhost:6379",
    )

    # ========================================================
    # AI / INTEGRATIONS
    # ========================================================

    EMBEDDING_PROVIDER: str = os.getenv(
        "EMBEDDING_PROVIDER",
        "openai",
    )

    OPENAI_API_KEY: str = os.getenv(
        "OPENAI_API_KEY",
        "",
    )

    OPENAI_EMBEDDING_BASE_URL: str = os.getenv(
        "OPENAI_EMBEDDING_BASE_URL",
        "",
    )

    OPENAI_EMBEDDING_MODEL: str = os.getenv(
        "OPENAI_EMBEDDING_MODEL",
        "text-embedding-3-small",
    )

    OPENAI_EMBEDDING_DIMENSIONS: int = int(
        os.getenv(
            "OPENAI_EMBEDDING_DIMENSIONS",
            "1536",
        )
    )

    OPENAI_EMBEDDING_MAX_CHARS: int = int(
        os.getenv(
            "OPENAI_EMBEDDING_MAX_CHARS",
            "24000",
        )
    )

    OPENAI_EMBEDDING_TIMEOUT_SECONDS: float = float(
        os.getenv(
            "OPENAI_EMBEDDING_TIMEOUT_SECONDS",
            "60",
        )
    )

    GEMINI_API_KEY: str = os.getenv(
        "GEMINI_API_KEY",
        "",
    )

    GEMINI_EMBEDDING_MODEL: str = os.getenv(
        "GEMINI_EMBEDDING_MODEL",
        "gemini-embedding-2",
    )

    DEEPSEEK_API_KEY: str = os.getenv(
        "DEEPSEEK_API_KEY",
        "",
    )

    DEEPSEEK_MODEL: str = os.getenv(
        "DEEPSEEK_MODEL",
        "deepseek-v4-flash",
    )

    DEEPSEEK_MAX_INPUT_CHARS: int = int(
        os.getenv(
            "DEEPSEEK_MAX_INPUT_CHARS",
            "30000",
        )
    )

    DEEPSEEK_TIMEOUT_SECONDS: float = float(
        os.getenv(
            "DEEPSEEK_TIMEOUT_SECONDS",
            "60",
        )
    )

    # ========================================================
    # SECURITY
    # ========================================================

    SECRET_KEY: str = os.getenv(
        "SECRET_KEY",
        "default-secret-key-change-in-production",
    )

    WEBHOOK_SECRET_ENCRYPTION_KEY: str = os.getenv(
        "SDE_WEBHOOK_SECRET_ENCRYPTION_KEY",
        "",
    )

    API_KEY_HEADER: str = os.getenv(
        "API_KEY_HEADER",
        "X-API-Key",
    )

    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv(
            "ACCESS_TOKEN_EXPIRE_MINUTES",
            "15",
        )
    )

    REFRESH_TOKEN_EXPIRE_DAYS: int = int(
        os.getenv(
            "REFRESH_TOKEN_EXPIRE_DAYS",
            "7",
        )
    )

    COOKIE_SECURE: bool = (
        os.getenv(
            "COOKIE_SECURE",
            "false",
        ).lower()
        == "true"
    )

    COOKIE_DOMAIN: str = os.getenv(
        "COOKIE_DOMAIN",
        "",
    )

    COOKIE_SAMESITE: str = os.getenv(
        "COOKIE_SAMESITE",
        "lax",
    )

    AUTH_COOKIE_PREFIX: str = os.getenv(
        "AUTH_COOKIE_PREFIX",
        "sde_",
    )

    APP_BASE_URL: str = os.getenv(
        "APP_BASE_URL",
        "http://localhost:8000",
    )

    # ========================================================
    # GOOGLE OAUTH
    # ========================================================

    GOOGLE_CLIENT_ID: str = os.getenv(
        "GOOGLE_CLIENT_ID",
        "",
    )

    GOOGLE_CLIENT_SECRET: str = os.getenv(
        "GOOGLE_CLIENT_SECRET",
        "",
    )

    GOOGLE_REDIRECT_URI: str = os.getenv(
        "GOOGLE_REDIRECT_URI",
        "",
    )

    # ========================================================
    # GITHUB OAUTH
    # ========================================================

    GITHUB_CLIENT_ID: str = os.getenv(
        "GITHUB_CLIENT_ID",
        "",
    )

    GITHUB_CLIENT_SECRET: str = os.getenv(
        "GITHUB_CLIENT_SECRET",
        "",
    )

    GITHUB_REDIRECT_URI: str = os.getenv(
        "GITHUB_REDIRECT_URI",
        "",
    )

    # ========================================================
    # EMAIL / QUEUE
    # ========================================================

    RESEND_API_KEY: str = os.getenv(
        "RESEND_API_KEY",
        "",
    )

    EMAIL_FROM: str = os.getenv(
        "EMAIL_FROM",
        "Semantic Drift Engine <no-reply@example.com>",
    )

    EMAIL_STREAM_KEY: str = os.getenv(
        "EMAIL_STREAM_KEY",
        "sde:email:stream",
    )

    EMAIL_STREAM_GROUP: str = os.getenv(
        "EMAIL_STREAM_GROUP",
        "sde-email-workers",
    )

    EMAIL_STREAM_MAXLEN: int = int(
        os.getenv(
            "EMAIL_STREAM_MAXLEN",
            "10000",
        )
    )

    EMAIL_WORKER_MAX_ATTEMPTS: int = int(
        os.getenv(
            "EMAIL_WORKER_MAX_ATTEMPTS",
            "5",
        )
    )

    EMAIL_WORKER_RETRY_BASE_SECONDS: int = int(
        os.getenv(
            "EMAIL_WORKER_RETRY_BASE_SECONDS",
            "5",
        )
    )

    EMAIL_WORKER_RETRY_MAX_SECONDS: int = int(
        os.getenv(
            "EMAIL_WORKER_RETRY_MAX_SECONDS",
            "300",
        )
    )

    EMAIL_WORKER_CLAIM_IDLE_MS: int = int(
        os.getenv(
            "EMAIL_WORKER_CLAIM_IDLE_MS",
            "120000",
        )
    )

    # ========================================================
    # PRIVACY
    # ========================================================

    PRIVACY_POLICY_VERSION: str = os.getenv(
        "PRIVACY_POLICY_VERSION",
        "1.0",
    )

    TERMS_VERSION: str = os.getenv(
        "TERMS_VERSION",
        "1.0",
    )

    # ========================================================
    # CRAWLER
    # ========================================================

    USER_AGENT: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36",
    )

    REQUEST_TIMEOUT: int = int(
        os.getenv(
            "REQUEST_TIMEOUT",
            "30",
        )
    )

    MAX_RETRIES: int = int(
        os.getenv(
            "MAX_RETRIES",
            "3",
        )
    )

    CRAWLER_SCHEDULER_POLL_SECONDS: float = float(
        os.getenv(
            "CRAWLER_SCHEDULER_POLL_SECONDS",
            "5",
        )
    )

    LINEAGE_INVALIDATION_EXECUTOR_POLL_SECONDS: float = float(
        os.getenv(
            "LINEAGE_INVALIDATION_EXECUTOR_POLL_SECONDS",
            "5",
        )
    )

    @model_validator(mode="after")
    def validate_runtime_security(self):
        if self.COOKIE_SAMESITE not in {"lax", "strict", "none"}:
            raise ValueError(
                "COOKIE_SAMESITE must be one of: lax, strict, none"
            )

        if self.COOKIE_SAMESITE == "none" and not self.COOKIE_SECURE:
            raise ValueError(
                "SameSite=None requires COOKIE_SECURE=true"
            )

        if self.ENVIRONMENT == "production":
            if (
                self.SECRET_KEY
                == "default-secret-key-change-in-production"
                or len(self.SECRET_KEY) < 32
            ):
                raise ValueError(
                    "Production SECRET_KEY must be a strong value "
                    "with at least 32 characters"
                )

            if not self.WEBHOOK_SECRET_ENCRYPTION_KEY:
                raise ValueError(
                    "SDE_WEBHOOK_SECRET_ENCRYPTION_KEY is required "
                    "in production"
                )

            if not self.COOKIE_SECURE:
                raise ValueError(
                    "COOKIE_SECURE=true is required in production"
                )

            if not self.APP_BASE_URL.startswith("https://"):
                raise ValueError(
                    "APP_BASE_URL must use https:// in production"
                )

        return self


settings = Settings()