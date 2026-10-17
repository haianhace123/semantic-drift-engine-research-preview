# Authentication & Security

This document describes the production authentication architecture used by the
Semantic Drift Engine.

The authentication layer is designed around:

- FastAPI
- SQLAlchemy
- PostgreSQL
- PyJWT
- bcrypt
- HTTP-only cookies
- CSRF protection
- OAuth 2.0
- Redis-based rate limiting and email jobs
- Resend email delivery

---

## 1. Authentication Architecture

The system uses a short-lived access token and a rotating refresh token.

```text
Browser
   |
   | HTTPS
   v
FastAPI
   |
   +--------------------+
   |                    |
   v                    v
PostgreSQL             Redis
   |                    |
   |                    +-- Rate limiting
   |                    +-- Email queue
   |
   +-- Users
   +-- Refresh token hashes
   +-- OAuth identities
   +-- Verification state
   +-- Password reset state
   +-- Consent records
```

### Token model

| Token | Lifetime | Storage | Purpose |
|---|---:|---|---|
| Access token | 15 minutes | HTTP-only cookie | Authenticated API access |
| Refresh token | 7 days | HTTP-only cookie | Access token rotation |
| CSRF token | Session scoped | Non-HTTP-only cookie | CSRF protection |

The application never stores authentication tokens in `localStorage`.

---

# 2. Cookie Security

Authentication cookies use the following attributes in production:

```text
HttpOnly
Secure
SameSite=Lax
```

The refresh cookie should also be scoped as narrowly as practical:

```text
Path=/api/auth
```

Recommended cookie names:

```text
sde_access
sde_refresh
sde_csrf
```

The server stores only a cryptographic hash of the refresh token.

The raw refresh token is never persisted in PostgreSQL.

---

# 3. Required Environment Variables

Production deployments must provide the following environment variables:

```env
DATABASE_URL=
SECRET_KEY=

COOKIE_SECURE=true
APP_BASE_URL=

REDIS_URL=

RESEND_API_KEY=
EMAIL_FROM=

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=

GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GITHUB_REDIRECT_URI=
```

Never commit these values to Git.

Use a secrets manager or environment-level secret store in production.

---

# 4. Password Security

Passwords are hashed with bcrypt using a cost factor of `12`.

```text
bcrypt
cost = 12
```

Plain-text passwords must never be:

- stored in the database
- written to logs
- returned by an API
- included in analytics
- included in exception messages

Password validation should enforce:

- minimum length
- upper-case character
- lower-case character
- numeric character
- special character
- confirmation password match

The exact validation policy should be centralized so signup and password reset
use the same rules.

---

# 5. Access Token

The access token is a JWT with a lifetime of approximately 15 minutes.

Recommended claims:

```json
{
  "sub": "<user-id>",
  "type": "access",
  "role": "user",
  "organization_id": "<organization-id>",
  "iat": 0,
  "exp": 0
}
```

The server must validate:

- signature
- token type
- expiration
- subject
- user existence
- account status
- organization membership

Expired or invalid tokens must never be accepted.

---

# 6. Refresh Token Rotation

Refresh tokens are rotated on every successful refresh operation.

Flow:

```text
Client
  |
  | POST /api/auth/refresh
  v
Server
  |
  +-- Validate refresh token
  |
  +-- Compare token hash
  |
  +-- Validate expiration
  |
  +-- Validate account state
  |
  +-- Revoke previous refresh token
  |
  +-- Generate new refresh token
  |
  +-- Store new token hash
  |
  +-- Set new HTTP-only cookies
  |
  v
Client
```

If an already-revoked refresh token is reused, the server should invalidate the
associated session and require the user to authenticate again.

This protects against refresh-token replay attacks.

---

# 7. CSRF Protection

Because authentication relies on cookies, state-changing requests require CSRF
protection.

The application uses the double-submit-cookie pattern.

The browser receives:

```text
sde_csrf=<random-token>
```

For state-changing requests, the frontend sends:

```http
X-CSRF-Token: <random-token>
```

The backend compares the request header against the CSRF cookie.

Protected methods include:

```text
POST
PUT
PATCH
DELETE
```

Safe methods such as `GET`, `HEAD`, and `OPTIONS` do not require the CSRF header.

---

# 8. Rate Limiting

Authentication endpoints are rate limited to reduce brute-force and abuse risk.

## Login

```text
5 failed attempts
=> lock account for 15 minutes
```

Successful login resets the failed-attempt counter.

## Signup

```text
10 requests per IP per hour
```

## Password reset

```text
3 requests per email per hour
```

Rate-limit state should be stored in Redis for multi-process deployments.

The server should return generic authentication messages so rate limiting does
not reveal whether an email address exists.

---

# 9. Account Locking

The `User` record contains:

```text
login_attempts
locked_until
```

When the maximum number of failed login attempts is reached:

```text
login_attempts >= 5
```

the account becomes temporarily locked.

The server must reject login attempts until:

```text
locked_until <= current_time
```

After a successful authentication:

```text
login_attempts = 0
locked_until = NULL
last_login = current_time
```

Administrative unlock functionality may be added later without changing the
authentication contract.

---

# 10. Email Verification

New accounts are created in an unverified state.

```text
email_verified = false
```

A verification token is generated during signup.

The database stores:

```text
verification_token
verification_token_expires
```

The actual verification URL should contain the token.

Example:

```text
https://app.example.com/verify-email?token=<token>
```

The user must verify the email before password-based authentication is fully
enabled.

Verification tokens must:

- be random
- be single-use
- have a short expiration
- be removed after successful verification

---

# 11. Password Reset

The password reset flow is intentionally stateless from the browser's
perspective.

## Step 1

The client submits:

```http
POST /api/auth/forgot-password
```

with:

```json
{
  "email": "user@example.com"
}
```

The server should return a generic response regardless of whether the email
exists.

Example:

```json
{
  "message": "If the account exists, a reset link has been sent."
}
```

This prevents account enumeration.

## Step 2

A reset token is generated.

The database stores:

```text
reset_token
reset_token_expires
```

## Step 3

The user opens:

```text
/reset-password?token=<token>
```

## Step 4

The frontend submits the new password.

The server:

1. validates the token
2. validates token expiration
3. validates password strength
4. hashes the new password
5. replaces the stored password hash
6. clears the reset token
7. revokes the current refresh session
8. requires a new login

---

# 12. OAuth

Supported OAuth providers:

```text
Google
GitHub
```

Both providers use the OAuth 2.0 authorization-code flow.

The backend must validate the OAuth `state` parameter to prevent CSRF attacks.

Provider client secrets remain server-side.

They must never be exposed to frontend JavaScript.

---

## Google OAuth

Start:

```http
GET /api/auth/google
```

Callback:

```http
GET /api/auth/google/callback
```

The server exchanges the authorization code for provider credentials and
retrieves the user's verified identity.

Recommended identity fields:

```text
provider
provider_user_id
email
name
avatar_url
```

---

## GitHub OAuth

Start:

```http
GET /api/auth/github
```

Callback:

```http
GET /api/auth/github/callback
```

GitHub identity should be mapped to:

```text
github_id
email
name
avatar_url
```

The server must handle cases where GitHub does not return a public email
address.

---

# 13. OAuth Account Linking

If an OAuth provider returns an email address that already belongs to an
existing account, the system must not silently merge identities.

Instead:

```text
Existing account
       |
       v
OAuth email matches
       |
       v
Require confirmation
       |
       +---- Confirm ----> Link provider
       |
       +---- Cancel -----> Keep accounts separate
```

This prevents accidental or malicious account takeover through provider
association.

---

# 14. User Profile

Authenticated clients may access:

```http
GET /api/auth/me
```

Example response:

```json
{
  "id": "user-id",
  "email": "user@example.com",
  "email_verified": true,
  "role": "user",
  "organization_id": "organization-id",
  "last_login": "2026-08-18T09:00:00Z"
}
```

Profile updates use:

```http
PUT /api/auth/me
```

Only explicitly supported fields should be writable.

Sensitive fields such as:

```text
role
organization_id
password_hash
refresh_token
login_attempts
locked_until
```

must never be writable through the normal profile endpoint.

---

# 15. Account Deletion

Account deletion uses soft deletion.

The database records:

```text
deleted_at
```

After deletion:

```text
deleted_at != NULL
```

the account must not be allowed to authenticate.

The deletion flow should:

1. revoke active refresh sessions
2. remove OAuth identifiers
3. invalidate authentication state
4. mark the account as deleted
5. preserve only data required for legal or audit purposes

A future data-retention job may permanently purge eligible records.

---

# 16. GDPR Data Export

Authenticated users may request a copy of their account data:

```http
GET /api/auth/me/export
```

The export should contain the user's own data that is legally exportable,
including:

- profile information
- consent records
- account metadata
- authentication-related timestamps

Secrets must never be included.

Examples:

```text
password_hash       -> excluded
refresh_token_hash  -> excluded
OAuth client secret -> excluded
```

---

# 17. Consent Management

The application records user consent for applicable data-processing purposes.

Example:

```json
{
  "consent_type": "privacy_policy",
  "granted": true,
  "version": "2026-08-18",
  "timestamp": "2026-08-18T09:00:00Z"
}
```

Consent records should be append-only from the application's perspective.

The system should retain:

```text
user_id
consent_type
version
granted
created_at
```

---

# 18. API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/auth/csrf` | Initialize CSRF cookie |
| POST | `/api/auth/signup` | Register a new account |
| POST | `/api/auth/login` | Authenticate using email and password |
| POST | `/api/auth/logout` | Revoke current session |
| POST | `/api/auth/refresh` | Rotate refresh token and issue a new access token |
| POST | `/api/auth/forgot-password` | Request password reset |
| POST | `/api/auth/reset-password` | Set a new password |
| GET | `/api/auth/verify-email` | Verify email address |
| GET | `/api/auth/me` | Retrieve current user |
| PUT | `/api/auth/me` | Update current user |
| DELETE | `/api/auth/me` | Delete current account |
| GET | `/api/auth/me/export` | Export personal data |
| POST | `/api/auth/consent` | Record consent |
| GET | `/api/auth/google` | Start Google OAuth |
| GET | `/api/auth/google/callback` | Handle Google OAuth callback |
| GET | `/api/auth/github` | Start GitHub OAuth |
| GET | `/api/auth/github/callback` | Handle GitHub OAuth callback |
| GET | `/api/auth/oauth/link-status` | Retrieve pending provider-link state |
| POST | `/api/auth/oauth/link/confirm` | Confirm or reject provider linking |

---

# 19. Standard Error Contract

Authentication endpoints return a consistent error structure.

```json
{
  "error": {
    "code": "AUTH_001",
    "message": "Email or password is incorrect.",
    "details": null
  }
}
```

Supported codes:

| Code | Meaning |
|---|---|
| `AUTH_001` | Invalid email or password |
| `AUTH_002` | Email address is not verified |
| `AUTH_003` | Account is temporarily locked |
| `AUTH_004` | Invalid authentication token |
| `AUTH_005` | Authentication token has expired |
| `AUTH_006` | Email address already exists |
| `AUTH_007` | Password does not meet security requirements |
| `AUTH_008` | Invalid email address |
| `AUTH_009` | Reset token is invalid or expired |
| `AUTH_010` | Verification token is invalid or expired |
| `AUTH_011` | OAuth provider error |
| `AUTH_012` | Account does not exist |

Error messages returned to users should be concise and user-friendly.

Internal exception details must remain in server-side logs.

---

# 20. Security Headers

Production responses should include appropriate security headers.

Recommended baseline:

```http
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: ...
```

The exact Content Security Policy must be derived from the actual application
assets and third-party integrations.

Do not use:

```http
Content-Security-Policy: *
```

in production.

---

# 21. Input Validation

All authentication input must be validated on the server.

Never rely exclusively on frontend validation.

Examples:

```text
Email
Password
Password confirmation
OAuth state
Reset token
Verification token
Profile fields
```

Database access must always use parameterized SQLAlchemy queries.

Raw string interpolation must never be used to construct SQL statements.

---

# 22. Email Delivery

Email delivery is asynchronous.

The API should enqueue email jobs rather than waiting for the provider response.

Recommended pipeline:

```text
API request
    |
    v
Create email job
    |
    v
Redis queue
    |
    v
Email worker
    |
    v
Resend
```

Supported templates:

```text
welcome
email_verification
password_reset
```

The API should not block authentication requests on external email-provider
latency.

---

# 23. Frontend Authentication Behaviour

Protected dashboard routes must require authentication.

Application flow:

```text
Open protected route
       |
       v
Check authenticated session
       |
       +---- Valid ----> Render dashboard
       |
       +---- Missing -> Attempt refresh
                            |
                            +---- Success -> Render dashboard
                            |
                            +---- Failure -> Redirect to login
```

For API responses returning:

```http
401 Unauthorized
```

the frontend should attempt exactly one refresh operation before redirecting
the user to `/login.html`.

This avoids infinite refresh loops.

---

# 24. Logout

Logout must perform both client-side and server-side cleanup.

Client:

```text
Clear application state
Clear cached sensitive data
Redirect to login
```

Server:

```text
Revoke refresh session
Clear authentication cookies
Invalidate associated authentication state
```

Authentication tokens must not remain accessible through JavaScript.

---

# 25. Testing Requirements

The authentication implementation should include automated coverage for:

## Signup

- valid registration
- invalid email
- weak password
- password mismatch
- duplicate email
- rate limiting

## Login

- valid credentials
- invalid credentials
- unverified email
- locked account
- failed-attempt counter
- successful login reset
- access-token expiration

## Refresh

- valid refresh token
- expired refresh token
- invalid token
- token rotation
- refresh-token replay
- revoked session

## Email verification

- valid token
- expired token
- invalid token
- token reuse
- already verified account

## Password reset

- valid reset request
- unknown email
- rate limiting
- invalid token
- expired token
- successful password change
- refresh-session invalidation

## OAuth

- Google success
- Google failure
- GitHub success
- GitHub failure
- invalid state
- existing-account linking
- rejected account linking

## Security

- CSRF rejection
- rate-limit enforcement
- SQL injection payload handling
- cookie security attributes
- protected route behaviour

---

# 26. Database Migration

The authentication migration adds the following fields to the existing user
model:

```text
email_verified
verification_token
verification_token_expires
reset_token
reset_token_expires
google_id
github_id
last_login
login_attempts
locked_until
refresh_token
refresh_token_expires
deleted_at
```

Recommended indexes:

```text
email
google_id
github_id
verification_token
reset_token
```

Provider identifiers should be unique where applicable.

The refresh token stored in the database must be a hash, not the raw token.

---

# 27. Migration Example

Run the authentication migration after backing up the database.

Example:

```bash
psql "$DATABASE_URL" -f scripts/migrations/001_auth_production.sql
```

Verify the migration before starting the application in production.

---

# 28. Email Worker

Start the email worker separately from the FastAPI process:

```bash
python scripts/email_worker.py
```

The worker is responsible for consuming queued email jobs and sending them
through Resend.

The worker must handle:

- retries
- provider failures
- malformed jobs
- duplicate delivery attempts
- structured logging

---

# 29. Local Development

Start the API:

```bash
uvicorn src.main:app --reload --port 8000
```

Start the email worker:

```bash
python scripts/email_worker.py
```

Run tests:

```bash
pytest
```

---

# 30. Production Checklist

Before deploying authentication to production:

- Use HTTPS everywhere.
- Set a strong randomly generated `SECRET_KEY`.
- Enable `COOKIE_SECURE=true`.
- Configure PostgreSQL.
- Configure Redis.
- Configure Resend.
- Configure Google OAuth.
- Configure GitHub OAuth.
- Run the database migration.
- Start the email worker.
- Configure secure CORS.
- Configure security headers.
- Verify CSRF protection.
- Verify rate limiting.
- Verify refresh-token rotation.
- Verify account locking.
- Verify email verification.
- Verify password reset.
- Verify OAuth state validation.
- Verify account deletion.
- Verify GDPR export.
- Remove all demo credentials.
- Review production logging.
- Ensure secrets are not committed to source control.
- Configure database backups.
- Configure application monitoring.

---

# 31. Operational Principle

Authentication must fail closed.

When the server cannot establish a trustworthy authentication state, the
request should be rejected rather than implicitly trusted.

Security-sensitive failures must be observable through server-side logs while
user-facing responses remain generic enough to avoid leaking account or
authentication information.