# Isolated integration-test execution

Database integration tests must not share PostgreSQL or Redis with running
application workers. The crawler scheduler can claim newly created sources
between a test's transaction commit and its assertion. Integration tests
also create PostgreSQL/Redis records, so the normal app stack is unsafe.
The previous autouse Redis fixture that deleted every `sde:rl:*` key has
been removed; never restore it as a workaround for test interference.

## Execution

1. Build the API image from current code: `docker compose build api`.
2. Run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_isolated_integration.ps1` from the repository on Windows.
3. To narrow the selection, specify `-Tests @('tests/integration/test_crawl_scheduling_service.py','tests/integration/test_public_source_enrollment_api.py')`.

The script runs migrations and selected tests in a uniquely named Compose
project using dedicated PostgreSQL and Redis containers. It publishes no ports,
mounts no application volumes, and does not load `.env`. It removes the test
project and its database volume in a `finally` block. A cleanup error prints
an explicit recovery command; do not delete or bring down the main Compose
project in response.

The test image is `semantic_drift_engine-api:latest`, so rebuild after changing
source. The stack uses static credentials only inside the disposable network.
Never use them for an accessible or persistent database.

This harness isolates the included DB and Redis suites. Other integration tests
may need additional dependencies, permission modes, or external services; do
not assume a passing selected suite is complete product acceptance. Live
browser and provider verification remain separate admission criteria.
