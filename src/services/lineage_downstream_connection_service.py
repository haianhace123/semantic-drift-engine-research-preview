from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import settings


class LineageDownstreamConnectionError(RuntimeError):
    """Base error for downstream connection resolution failures."""


class LineageDownstreamConnectionConfigurationError(
    LineageDownstreamConnectionError
):
    """Raised when the downstream connection registry is invalid."""


class LineageDownstreamConnectionNotFoundError(
    LineageDownstreamConnectionError
):
    """Raised when a requested downstream connection is not configured."""


@dataclass(frozen=True, slots=True)
class LineageDownstreamConnection:
    connection_ref: str
    database_url: str = field(repr=False)


def resolve_lineage_downstream_connection(
    *,
    connection_ref: str,
    registry_json: str | None = None,
) -> LineageDownstreamConnection:
    normalized_ref = _required_string(
        connection_ref,
        field_name="connection_ref",
    )

    raw_registry = (
        settings.DOWNSTREAM_DATABASE_URLS
        if registry_json is None
        else registry_json
    )

    registry = _parse_registry(raw_registry)
    database_url = registry.get(normalized_ref)

    if database_url is None:
        raise LineageDownstreamConnectionNotFoundError(
            "Downstream connection reference is not configured."
        )

    return LineageDownstreamConnection(
        connection_ref=normalized_ref,
        database_url=database_url,
    )


def create_lineage_downstream_session(
    connection: LineageDownstreamConnection,
) -> tuple[Session, Engine]:
    engine = create_engine(
        connection.database_url,
        pool_pre_ping=True,
    )
    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )

    return session_factory(), engine


def _parse_registry(
    registry_json: str,
) -> dict[str, str]:
    if not isinstance(registry_json, str):
        raise LineageDownstreamConnectionConfigurationError(
            "Downstream database registry must be a JSON string."
        )

    try:
        raw_registry = json.loads(registry_json)
    except json.JSONDecodeError as exc:
        raise LineageDownstreamConnectionConfigurationError(
            "Downstream database registry is not valid JSON."
        ) from exc

    if not isinstance(raw_registry, Mapping):
        raise LineageDownstreamConnectionConfigurationError(
            "Downstream database registry must be a JSON object."
        )

    normalized_registry: dict[str, str] = {}

    for raw_ref, raw_url in raw_registry.items():
        try:
            connection_ref = _required_string(
                raw_ref,
                field_name="connection reference",
            )
            database_url = _required_string(
                raw_url,
                field_name=f"database URL for {connection_ref!r}",
            )
        except (TypeError, ValueError) as exc:
            raise LineageDownstreamConnectionConfigurationError(
                "Downstream database registry contains an invalid entry."
            ) from exc

        if connection_ref in normalized_registry:
            raise LineageDownstreamConnectionConfigurationError(
                "Downstream database registry contains duplicate "
                "normalized connection references."
            )

        normalized_registry[connection_ref] = database_url

    return normalized_registry


def _required_string(
    value: object,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a string."
        )

    normalized = value.strip()

    if not normalized:
        raise ValueError(
            f"{field_name} must not be empty."
        )

    return normalized