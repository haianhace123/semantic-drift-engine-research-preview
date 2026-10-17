import pytest

from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)


def _identity() -> LineageExecutionTargetIdentity:
    return LineageExecutionTargetIdentity(
        source_external_id="policy-source",
        asset_external_id="support-index",
    )


def test_identity_normalizes_external_ids() -> None:
    identity = LineageExecutionTargetIdentity(
        source_external_id=" policy-source ",
        asset_external_id=" support-index ",
    )

    assert identity.source_external_id == "policy-source"
    assert identity.asset_external_id == "support-index"


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("source_external_id", ""),
        ("source_external_id", "   "),
        ("asset_external_id", ""),
        ("asset_external_id", "   "),
    ),
)
def test_identity_rejects_empty_external_ids(
    field_name: str,
    value: str,
) -> None:
    values = {
        "source_external_id": "policy-source",
        "asset_external_id": "support-index",
    }
    values[field_name] = value

    with pytest.raises(ValueError):
        LineageExecutionTargetIdentity(**values)


def test_binding_normalizes_execution_coordinates() -> None:
    binding = LineageExecutionTargetBinding(
        identity=_identity(),
        binding_revision=1,
        provider=" pgvector ",
        target_kind=" collection ",
        configuration={
            " database ": " knowledge ",
            " collection ": " support ",
        },
    )

    assert binding.binding_revision == 1
    assert binding.provider == "pgvector"
    assert binding.target_kind == "collection"
    assert binding.configuration == {
        "database": "knowledge",
        "collection": "support",
    }


@pytest.mark.parametrize(
    "binding_revision",
    (0, -1),
)
def test_binding_requires_positive_revision(
    binding_revision: int,
) -> None:
    with pytest.raises(ValueError):
        LineageExecutionTargetBinding(
            identity=_identity(),
            binding_revision=binding_revision,
            provider="pgvector",
            target_kind="collection",
            configuration={
                "collection": "support",
            },
        )


@pytest.mark.parametrize(
    ("provider", "target_kind"),
    (
        ("", "collection"),
        ("   ", "collection"),
        ("pgvector", ""),
        ("pgvector", "   "),
    ),
)
def test_binding_requires_execution_classification(
    provider: str,
    target_kind: str,
) -> None:
    with pytest.raises(ValueError):
        LineageExecutionTargetBinding(
            identity=_identity(),
            binding_revision=1,
            provider=provider,
            target_kind=target_kind,
            configuration={
                "collection": "support",
            },
        )


@pytest.mark.parametrize(
    "configuration",
    (
        {"": "support"},
        {"   ": "support"},
        {"collection": ""},
        {"collection": "   "},
    ),
)
def test_binding_rejects_empty_configuration_entries(
    configuration: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        LineageExecutionTargetBinding(
            identity=_identity(),
            binding_revision=1,
            provider="pgvector",
            target_kind="collection",
            configuration=configuration,
        )


@pytest.mark.parametrize(
    "secret_key",
    (
        "api_key",
        "API_KEY",
        "password",
        "secret",
        "secret_key",
        "token",
        "access_token",
    ),
)
def test_binding_rejects_secret_configuration(
    secret_key: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="must not contain secret credentials",
    ):
        LineageExecutionTargetBinding(
            identity=_identity(),
            binding_revision=1,
            provider="pgvector",
            target_kind="collection",
            configuration={
                secret_key: "do-not-persist",
            },
        )