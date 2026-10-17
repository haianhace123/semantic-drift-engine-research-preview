import pytest

from src.services.lineage_downstream_connection_service import (
    LineageDownstreamConnectionConfigurationError,
    LineageDownstreamConnectionNotFoundError,
    resolve_lineage_downstream_connection,
)


def test_resolve_downstream_connection_by_reference():
    connection = resolve_lineage_downstream_connection(
        connection_ref="customer-primary",
        registry_json=(
            '{"customer-primary":'
            '"postgresql://user:pass@db.example/customer"}'
        ),
    )

    assert connection.connection_ref == "customer-primary"
    assert (
        connection.database_url
        == "postgresql://user:pass@db.example/customer"
    )


def test_downstream_connection_repr_does_not_expose_database_url():
    database_url = "postgresql://secret-user:secret-pass@db.example/customer"

    connection = resolve_lineage_downstream_connection(
        connection_ref="customer-primary",
        registry_json=(
            '{"customer-primary":'
            f'"{database_url}"'
            "}"
        ),
    )

    rendered = repr(connection)

    assert "customer-primary" in rendered
    assert database_url not in rendered
    assert "secret-user" not in rendered
    assert "secret-pass" not in rendered


def test_resolve_downstream_connection_normalizes_reference():
    connection = resolve_lineage_downstream_connection(
        connection_ref="  customer-primary  ",
        registry_json=(
            '{"customer-primary":'
            '"postgresql://user:pass@db.example/customer"}'
        ),
    )

    assert connection.connection_ref == "customer-primary"


def test_resolve_downstream_connection_rejects_unknown_reference():
    with pytest.raises(LineageDownstreamConnectionNotFoundError):
        resolve_lineage_downstream_connection(
            connection_ref="missing",
            registry_json=(
                '{"customer-primary":'
                '"postgresql://user:pass@db.example/customer"}'
            ),
        )


@pytest.mark.parametrize(
    "registry_json",
    [
        "",
        "not-json",
        "[]",
        '"database-url"',
        '{"customer-primary": ""}',
        '{"": "postgresql://db"}',
        '{"customer-primary": 123}',
    ],
)
def test_resolve_downstream_connection_rejects_invalid_registry(
    registry_json,
):
    with pytest.raises(
        LineageDownstreamConnectionConfigurationError
    ):
        resolve_lineage_downstream_connection(
            connection_ref="customer-primary",
            registry_json=registry_json,
        )


def test_resolve_downstream_connection_rejects_duplicate_normalized_refs():
    with pytest.raises(
        LineageDownstreamConnectionConfigurationError
    ):
        resolve_lineage_downstream_connection(
            connection_ref="customer-primary",
            registry_json=(
                "{"
                '"customer-primary":"postgresql://first",'
                '" customer-primary ":"postgresql://second"'
                "}"
            ),
        )