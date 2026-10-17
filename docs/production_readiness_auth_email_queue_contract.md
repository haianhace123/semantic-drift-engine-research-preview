# PR.7: Authentication Email Queue Admission Contract

## Problem

`signup` committed a new account before publishing its verification email, then
suppressed any email queue failure and claimed the email had been sent.
`resend-verification` returned an HTTP 503 only for eligible, existing accounts
when the queue was unavailable, creating an account-enumeration side channel.
`forgot-password` persisted a new reset token before attempting queue admission;
on failure, a previously usable reset link was silently invalidated.

## Contract

- Signup persists the account before enqueue. On verification enqueue failure,
  it returns `503 / AUTH_019` with `details.account_created=true`. The signup
  frontend moves to the verification panel with an immediately available
  **Resend code** action; users must not retry account creation. A welcome email
  is optional and cannot convert verification enqueue success into an error.
- Resend-verification returns the **same generic HTTP 200 response** regardless
  of whether the account is missing, already verified, or queue admission
  fails. On enqueue failure, the database transaction rolls back and the prior
  verification code remains valid. Internal logs identify the queue operation
  but do not contain email addresses, codes, reset URLs, or raw credentials.
- Forgot-password also uses a uniform HTTP 200 response and never promises
  successful sending. It does not persist a replacement reset token if the
  email could not be enqueued; the prior reset token remains usable. The UI
  uses the same conditional language as the API.
- A successful `enqueue_email` proves Redis accepted a job, **not** that the
  email provider has delivered it. The API avoids the word "sent" in success
  responses. Neither endpoint reveals account existence through queue errors.

## Limits and future work

The Redis queue and PostgreSQL user table do not participate in a shared
transaction. A failure committing an auth-token update *after* the queue
accepts an email can leave an email containing a token which was never
persisted. This patch does not pretend to provide cross-store atomicity. If
this risk is material in production, adopt a transactional outbox with a
bounded publisher and clear retention/recovery rules rather than introducing
another non-atomic pair of writes.

Likewise, success means **queued**, not delivered; provider retry and
idempotency have their own contracts in the email worker.

## Validation

Run `tests/integration/test_auth_verification.py` with
`RUN_AUTH_INTEGRATION_TESTS=1` against an isolated test PostgreSQL database.
Tests stub email admission (no real email is sent) and cover recovered signup,
optional welcome failures, uniform public responses, token rollback and reset
queue success. Run `node --check src/static/js/auth-pages.js`, relevant unit
tests, and full unit regression.
