import base64
import os
import uuid

import pytest

from src.services.webhook_secret_encryption_service import (
    WEBHOOK_SECRET_ENCRYPTION_VERSION,
    WEBHOOK_SECRET_NONCE_BYTES,
    EncryptedWebhookSecret,
    WebhookSecretEncryptionError,
    decrypt_webhook_secret,
    encrypt_webhook_secret,
)


def _encoded_key() -> str:
    return base64.urlsafe_b64encode(
        os.urandom(32)
    ).decode("ascii")


def test_webhook_secret_round_trip() -> None:
    organization_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    encryption_key = _encoded_key()

    encrypted = encrypt_webhook_secret(
        "webhook-signing-secret",
        organization_id=organization_id,
        subscription_id=subscription_id,
        encoded_encryption_key=encryption_key,
    )

    assert (
        encrypted.version
        == WEBHOOK_SECRET_ENCRYPTION_VERSION
    )
    assert (
        len(encrypted.nonce)
        == WEBHOOK_SECRET_NONCE_BYTES
    )
    assert (
        b"webhook-signing-secret"
        not in encrypted.ciphertext
    )

    decrypted = decrypt_webhook_secret(
        encrypted,
        organization_id=organization_id,
        subscription_id=subscription_id,
        encoded_encryption_key=encryption_key,
    )

    assert decrypted == "webhook-signing-secret"


def test_webhook_secret_encryption_uses_fresh_nonce() -> None:
    organization_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    encryption_key = _encoded_key()

    first = encrypt_webhook_secret(
        "same-secret",
        organization_id=organization_id,
        subscription_id=subscription_id,
        encoded_encryption_key=encryption_key,
    )
    second = encrypt_webhook_secret(
        "same-secret",
        organization_id=organization_id,
        subscription_id=subscription_id,
        encoded_encryption_key=encryption_key,
    )

    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


@pytest.mark.parametrize(
    "encoded_key",
    [
        "",
        "not-base64!",
        base64.urlsafe_b64encode(
            b"too-short"
        ).decode("ascii"),
    ],
)
def test_webhook_secret_rejects_invalid_encryption_key(
    encoded_key: str,
) -> None:
    with pytest.raises(
        WebhookSecretEncryptionError
    ):
        encrypt_webhook_secret(
            "secret",
            organization_id=uuid.uuid4(),
            subscription_id=uuid.uuid4(),
            encoded_encryption_key=encoded_key,
        )


def test_webhook_secret_rejects_wrong_encryption_key() -> None:
    organization_id = uuid.uuid4()
    subscription_id = uuid.uuid4()

    encrypted = encrypt_webhook_secret(
        "secret",
        organization_id=organization_id,
        subscription_id=subscription_id,
        encoded_encryption_key=_encoded_key(),
    )

    with pytest.raises(
        WebhookSecretEncryptionError,
        match="authentication failed",
    ):
        decrypt_webhook_secret(
            encrypted,
            organization_id=organization_id,
            subscription_id=subscription_id,
            encoded_encryption_key=_encoded_key(),
        )


@pytest.mark.parametrize(
    (
        "organization_changed",
        "subscription_changed",
    ),
    [
        (True, False),
        (False, True),
    ],
)
def test_webhook_secret_identity_is_authenticated(
    organization_changed: bool,
    subscription_changed: bool,
) -> None:
    organization_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    encryption_key = _encoded_key()

    encrypted = encrypt_webhook_secret(
        "secret",
        organization_id=organization_id,
        subscription_id=subscription_id,
        encoded_encryption_key=encryption_key,
    )

    with pytest.raises(
        WebhookSecretEncryptionError,
        match="authentication failed",
    ):
        decrypt_webhook_secret(
            encrypted,
            organization_id=(
                uuid.uuid4()
                if organization_changed
                else organization_id
            ),
            subscription_id=(
                uuid.uuid4()
                if subscription_changed
                else subscription_id
            ),
            encoded_encryption_key=encryption_key,
        )


def test_webhook_secret_rejects_tampered_ciphertext() -> None:
    organization_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    encryption_key = _encoded_key()

    encrypted = encrypt_webhook_secret(
        "secret",
        organization_id=organization_id,
        subscription_id=subscription_id,
        encoded_encryption_key=encryption_key,
    )

    tampered = EncryptedWebhookSecret(
        version=encrypted.version,
        nonce=encrypted.nonce,
        ciphertext=(
            encrypted.ciphertext[:-1]
            + bytes(
                [
                    encrypted.ciphertext[-1]
                    ^ 1
                ]
            )
        ),
    )

    with pytest.raises(
        WebhookSecretEncryptionError,
        match="authentication failed",
    ):
        decrypt_webhook_secret(
            tampered,
            organization_id=organization_id,
            subscription_id=subscription_id,
            encoded_encryption_key=encryption_key,
        )


def test_webhook_secret_rejects_unknown_version() -> None:
    encrypted = EncryptedWebhookSecret(
        version=999,
        nonce=os.urandom(
            WEBHOOK_SECRET_NONCE_BYTES
        ),
        ciphertext=b"ciphertext",
    )

    with pytest.raises(
        WebhookSecretEncryptionError,
        match="Unsupported",
    ):
        decrypt_webhook_secret(
            encrypted,
            organization_id=uuid.uuid4(),
            subscription_id=uuid.uuid4(),
            encoded_encryption_key=_encoded_key(),
        )


def test_webhook_secret_rejects_invalid_nonce_length() -> None:
    encrypted = EncryptedWebhookSecret(
        version=WEBHOOK_SECRET_ENCRYPTION_VERSION,
        nonce=b"short",
        ciphertext=b"ciphertext",
    )

    with pytest.raises(
        WebhookSecretEncryptionError,
        match="nonce is invalid",
    ):
        decrypt_webhook_secret(
            encrypted,
            organization_id=uuid.uuid4(),
            subscription_id=uuid.uuid4(),
            encoded_encryption_key=_encoded_key(),
        )