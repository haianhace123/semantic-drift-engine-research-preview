"""Readiness contract for browser acceptance after Compose startup."""

from unittest.mock import patch

import pytest

from scripts.smoke_auth_browser import wait_for_api


class _HealthyResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_browser_waits_until_api_health_is_200():
    with patch("scripts.smoke_auth_browser.urlopen", return_value=_HealthyResponse()) as mock:
        wait_for_api("http://127.0.0.1:8000", timeout_seconds=0.1)
    mock.assert_called_once_with("http://127.0.0.1:8000/health", timeout=2.0)


def test_browser_preflight_fails_if_api_never_listens():
    from urllib.error import URLError

    with patch("scripts.smoke_auth_browser.urlopen", side_effect=URLError("refused")):
        with pytest.raises(RuntimeError, match=r"API readiness failed.*refused"):
            wait_for_api("http://127.0.0.1:8000", timeout_seconds=0)
