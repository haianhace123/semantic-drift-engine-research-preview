# Auth browser acceptance

Run after building/restarting the API image with the current code:

```powershell
docker compose up -d --build api
docker compose exec -T api python -m scripts.smoke_auth_browser
docker compose cp api:/tmp/sde-auth-smoke .\sde-auth-smoke
```

The browser runs *inside the API container*: `127.0.0.1:8000` is the local
Uvicorn listener in that container. The script waits up to 45 seconds for
`GET /health` to return HTTP 200 before visiting the pages; override via
`--readiness-timeout-seconds` if needed. A failed preflight exits nonzero and
records the reason in `summary.json` instead of reporting ten unrelated page
failures. If readiness fails, inspect `docker compose ps api` and
`docker compose logs --tail=100 api`; do not change the host/port merely to
work around an app startup error.

The script checks login, signup, forgot/reset password, and OAuth linking in
1440px desktop and 390px mobile Chromium viewports. It captures screenshots,
page script errors, failed local static assets, missing styling or landmarks,
horizontal overflow, and the no-pending-link disabled-confirmation guard.
It does not enter credentials, perform OAuth handshakes, or mutate users.

Review all ten screenshots manually for hierarchy, contrast, visual rhythm,
focus indications, copy consistency, and appropriate empty/error states.
Functional acceptance still requires isolated auth DB tests plus an actual
account-linking round trip and mobile keyboard interaction. Screenshots should
not be treated as evidence of backend authorization correctness.
