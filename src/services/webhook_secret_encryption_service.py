"""
Authenticated encryption for persisted webhook signing secrets.

Webhook signing secrets must remain recoverable because delivery workers use
them to produce HMAC signatures. This module keeps reversible encryption
separate from authentication secrets and persistence concerns.
"""

from __future__ import annotations

import base64
import binascii
import os
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


WEBHOOK_SECRET_ENCRYPTION_VERSION = 1
WEBHOOK_SECRET_KEY_BYTES = 32
WEBHOOK_SECRET_NONCE_BYTES = 12


class WebhookSecretEncryptionError(RuntimeError):
    """Raised when a webhook secret cannot be encrypted or decrypted safely."""


@dataclass(frozen=True, slots=True)
class EncryptedWebhookSecret:
    version: int
    nonce: bytes
    ciphertext: bytes


def _decode_encryption_key(
    encoded_key: str,
) -> bytes:
    candidate = str(
        encoded_key or ""
    ).strip()

    if not candidate:
        raise WebhookSecretEncryptionError(
            "Webhook secret encryption key is not configured."
        )

    try:
        key = base64.b64decode(
            candidate,
            altchars=b"-_",
            validate=True,
        )
    except (
        binascii.Error,
        ValueError,
    ) as exc:
        raise WebhookSecretEncryptionError(
            "Webhook secret encryption key must be valid URL-safe base64."
        ) from exc

    if len(key) != WEBHOOK_SECRET_KEY_BYTES:
        raise WebhookSecretEncryptionError(
            "Webhook secret encryption key must decode to exactly 32 bytes."
        )

    return key


def _build_aad(
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
    version: int,
) -> bytes:
    return (
        "semantic-drift-engine:webhook-secret:"
        f"v{version}:"
        f"{organization_id}:"
        f"{subscription_id}"
    ).encode("utf-8")


def encrypt_webhook_secret(
    secret_key: str,
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
    encoded_encryption_key: str,
) -> EncryptedWebhookSecret:
    plaintext = str(
        secret_key or ""
    ).strip()

    if not plaintext:
        raise WebhookSecretEncryptionError(
            "Webhook secret key cannot be empty."
        )

    key = _decode_encryption_key(
        encoded_encryption_key
    )

    nonce = os.urandom(
        WEBHOOK_SECRET_NONCE_BYTES
    )

    aad = _build_aad(
        organization_id=organization_id,
        subscription_id=subscription_id,
        version=WEBHOOK_SECRET_ENCRYPTION_VERSION,
    )

    ciphertext = AESGCM(
        key
    ).encrypt(
        nonce,
        plaintext.encode("utf-8"),
        aad,
    )

    return EncryptedWebhookSecret(
        version=WEBHOOK_SECRET_ENCRYPTION_VERSION,
        nonce=nonce,
        ciphertext=ciphertext,
    )


def decrypt_webhook_secret(
    encrypted: EncryptedWebhookSecret,
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
    encoded_encryption_key: str,
) -> str:
    if (
        encrypted.version
        != WEBHOOK_SECRET_ENCRYPTION_VERSION
    ):
        raise WebhookSecretEncryptionError(
            "Unsupported webhook secret encryption version."
        )

    if len(encrypted.nonce) != WEBHOOK_SECRET_NONCE_BYTES:
        raise WebhookSecretEncryptionError(
            "Webhook secret encryption nonce is invalid."
        )

    if not encrypted.ciphertext:
        raise WebhookSecretEncryptionError(
            "Webhook secret ciphertext is empty."
        )

    key = _decode_encryption_key(
        encoded_encryption_key
    )

    aad = _build_aad(
        organization_id=organization_id,
        subscription_id=subscription_id,
        version=encrypted.version,
    )

    try:
        plaintext = AESGCM(
            key
        ).decrypt(
            encrypted.nonce,
            encrypted.ciphertext,
            aad,
        )
    except (
        InvalidTag,
        ValueError,
    ) as exc:
        raise WebhookSecretEncryptionError(
            "Webhook secret ciphertext authentication failed."
        ) from exc

    try:
        secret_key = plaintext.decode(
            "utf-8"
        )
    except UnicodeDecodeError as exc:
        raise WebhookSecretEncryptionError(
            "Webhook secret plaintext is not valid UTF-8."
        ) from exc

    if not secret_key:
        raise WebhookSecretEncryptionError(
            "Webhook secret plaintext is empty."
        )

    return secret_key