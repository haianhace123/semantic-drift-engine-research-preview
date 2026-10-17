# Authentication reliability and capability boundary

## Decision

The public authentication UI must reflect deployment capabilities, not only
which code paths exist in the repository. A successfully queued email job is not
proof that a password reset can reach a mailbox. OAuth controls must not be
interactive without configured provider credentials. Authentication throttling
must not silently switch off when Redis becomes unavailable.

This is a bounded security/product-truth change. It does not turn a local preview
into a public service or certify mail deliverability.

## Contract

- `GET /api/auth/capabilities` is unauthenticated, account-independent and
  returns only booleans: `google`, `github`, `email_recovery`.
- OAuth is advertised when its corresponding client ID and secret are configured.
  An actual OAuth callback acceptance is still required before launch.
- Email recovery is advertised only when `SDE_EMAIL_WORKER_EXPECTED=true`, a
  provider API key and a non-placeholder sender are configured in the API environment, and the
  authoritative operational worker overview reports a healthy email worker.
  Fresh heartbeats alone are not delivery guarantees.
- When email recovery is unavailable, forgot-password returns the same
  `AUTH_021` / HTTP 503 regardless of the supplied account; it cannot create
  a reset token or enqueue an undeliverable email.
- The browser treats capability fetch failures as unavailable and disables
  unconfigured options. A subsequent worker failure is handled by the endpoint,
  not by a stale capability snapshot.
- IP rate limiting uses the ASGI `request.client.host` identity only. Uvicorn
  interprets forwarded headers solely from trusted proxy peers according to
  `FORWARDED_ALLOW_IPS`; auth routes must not read raw forwarding headers.
  Operators must restrict direct API ingress and configure the allowlist to
  the actual ingress topology before exposing public login. Incorrect proxy
  trust settings can still permit spoofing or collapse rate-limit buckets.
- The currently pinned Uvicorn 0.30.x does not accept CIDR ranges for proxy
  trust. Production must supply `FORWARDED_ALLOW_IPS` with the exact, verified,
  stable Caddy peer IP (or a comma-separated set of exact proxy IPs), never `*`
  or a CIDR. The production Compose override refuses to start without an
  explicit value. This is a necessary configuration condition, not proof that
  Caddy uses that address. Confirm the live peer identity, ingress isolation,
  header sanitization, restart stability and distinct-client rate limits before
  enabling public authentication. A changed Caddy IP requires revalidation.
- Authentication rate limiting is one atomic Redis Lua command including
  `INCR`, `EXPIRE` and `TTL`. The command repairs legacy stranded no-TTL keys.
  Redis failures yield `AUTH_022` / HTTP 503 rather than bypassing the limiter.
  This prioritizes resistance to account takeover over sign-in availability
  during a limiter outage; it requires monitoring and an operator response.

## Review and release requirements

1. Run targeted Python and JS regressions against the exact Git commit.
2. Test Lua under a *disposable isolated Redis*, including concurrent requests,
   expiry, threshold crossing and a pre-existing key with TTL -1. Source-level
   mock tests alone do not prove Redis execution. Exercise fake X-Forwarded-For
   against untrusted ASGI peers and review the trusted ingress on a release
   server before launch.
3. Test `GET /api/auth/capabilities` and the login/forgot-password UI on an
   isolated release image with the email worker disabled and without OAuth keys.
   Assert reset endpoint rejects both known and unknown emails uniformly and
   creates no token/email job.
4. With a dedicated test provider, configured sender and healthy worker, verify
   request -> stream -> provider -> inbox -> one-time reset -> old session
   revocation, plus retries, failures and dead-letter handling before turning
   on customer self-service recovery.
5. Keep Terms and Privacy explicitly blocked for public launch until factual
   operator identity, contacts, retention and processing arrangements have been
   supplied and reviewed. Do not publish invented policy commitments.
6. Re-run tenant-isolation and SSRF negative controls and capture off-host
   encrypted backup + restore receipts before any production cutover.

## Comparison evidence

- Auth0's password recovery lifecycle treats email-provider configuration,
  templates and delivery as separate operational dependencies; generating a
  reset ticket alone is not sending mail.
- Stripe's published API guidance uses idempotency to make retried mutations
  safe. SDE should apply the same principle to source publication, invalidation
  and recovery, with durable results rather than optimistic UX.
- LaunchDarkly makes historical changes inspectable and rollbackable. SDE should
  center evidence, event timestamps, affected consumers and recovery decisions.
- Vanta distinguishes continuously monitored risks from one-off assessments.
  SDE should distinguish actionable semantic risk from mere text churn.

This is design comparison, not an assertion that SDE has the same feature
coverage or operational maturity as those vendors.
