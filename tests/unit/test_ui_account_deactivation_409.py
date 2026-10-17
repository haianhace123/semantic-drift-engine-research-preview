"""Account deactivation ownership-refusal UI contract.

Source-level guardrails. A browser/HTTP test is still required before release.
"""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[2] /
          'frontend/src/account/AccountSecurity.tsx')


def handler() -> str:
    source = SOURCE.read_text(encoding='utf8')
    return source.split('  async function handleDeleteAccount() {', 1)[1].split('\n  return (', 1)[0]


def test_conflict_is_explicitly_keyed_to_http_409():
    body = handler()
    assert 'caught instanceof ApiError && caught.status === 409' in body
    assert 'organization ownership' in body
    assert 'Your account remains active' in body


def test_refusal_reveals_existing_accessible_error():
    body = handler()
    assert 'setPendingAction(null);' in body.split('} catch (caught) {', 1)[1]
    assert 'setError(' in body.split('} catch (caught) {', 1)[1]
    assert 'setBusyId(null);' in body.split('} catch (caught) {', 1)[1]
    assert 'role="alert"' in SOURCE.read_text(encoding='utf8')


def test_success_only_redirects_after_server_confirmation():
    body = handler()
    successful, failure = body.split('} catch (caught) {', 1)
    assert successful.index('await deleteAccount();') < successful.index('window.location.replace("/login.html")')
    assert 'window.location.replace' not in failure


def test_unrelated_errors_retain_server_message():
    assert ': messageFrom(caught),' in handler()
