"""Translate the application PostgreSQL URL for libpq command-line tools.

pg_dump and pg_restore do not use SQLAlchemy's connection handling.  Keep
credential decoding and connection-policy translation in one place so backup
and restore cannot silently diverge from the application's configured URL.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


_SUPPORTED_SCHEMES = {
    "postgres",
    "postgresql",
    "postgresql+psycopg2",
    "postgresql+psycopg",
}

# libpq's environment variable equivalents for URL query parameters.
# Unknown parameters are rejected instead of silently dropping connection
# requirements (especially TLS and authentication requirements).
_URL_QUERY_ENV = {
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslcrl": "PGSSLCRL",
    "sslcrldir": "PGSSLCRLDIR",
    "sslpassword": "PGSSLPASSWORD",
    "channel_binding": "PGCHANNELBINDING",
    "gssencmode": "PGGSSENCMODE",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
    "application_name": "PGAPPNAME",
    "options": "PGOPTIONS",
}


def _validated_url(database_url: str) -> URL:
    try:
        url = make_url(database_url)
    except (ArgumentError, TypeError, ValueError) as exc:
        raise RuntimeError("DATABASE_URL is not a valid PostgreSQL URL.") from exc

    if url.drivername not in _SUPPORTED_SCHEMES:
        raise RuntimeError("DATABASE_URL must use PostgreSQL.")

    if not url.database:
        raise RuntimeError("DATABASE_URL does not contain a database name.")

    # SQLAlchemy's URL parser drops blank query values. Check the raw
    # query too so ?sslmode= cannot silently revert to libpq defaults.
    for name, value in parse_qsl(urlsplit(database_url).query, keep_blank_values=True):
        if not value:
            raise RuntimeError(
                f"DATABASE_URL parameter {name} must have one nonempty value."
            )

    unsupported = set(url.query) - set(_URL_QUERY_ENV)
    if unsupported:
        raise RuntimeError(
            "DATABASE_URL has unsupported PostgreSQL CLI parameters: "
            + ", ".join(sorted(unsupported))
        )

    for name, value in url.query.items():
        if not isinstance(value, str) or not value:
            raise RuntimeError(
                f"DATABASE_URL parameter {name} must have one nonempty value."
            )

    return url


def parse_database_url(database_url: str) -> dict[str, str]:
    """Decode URL-escaped identities without exposing credentials in argv."""
    url = _validated_url(database_url)

    return {
        "host": url.host or "localhost",
        "port": str(url.port or 5432),
        "user": url.username or "",
        "password": url.password or "",
        "database": url.database or "",
    }


def postgres_cli_environment(
    database_url: str,
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Pass credentials and connection policy through libpq environment."""
    url = _validated_url(database_url)
    result = dict(environment)

    if url.password:
        result["PGPASSWORD"] = url.password
    else:
        result.pop("PGPASSWORD", None)

    for name, env_key in _URL_QUERY_ENV.items():
        if name in url.query:
            result[env_key] = url.query[name]

    return result
