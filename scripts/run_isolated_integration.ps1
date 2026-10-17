<#
.SYNOPSIS
Run SDE integration tests against disposable, separate PostgreSQL and Redis.
.DESCRIPTION
Uses the pre-built semantic_drift_engine-api:latest image, starts a uniquely
named Compose project with no published ports, applies forward migrations,
runs selected integration tests, and removes the test project and its volume.
Never modifies or stops the normal SDE Compose stack.
#>
[CmdletBinding()]
param(
    [string[]]$Tests = @(
        "tests/integration/test_crawler_scheduled_identity.py",
        "tests/integration/test_crawler_claim_fenced_snapshot.py",
        "tests/integration/test_crawler_scheduler_runtime.py",
        "tests/integration/test_crawler_tenant_isolation.py",
        "tests/integration/test_crawl_scheduling_service.py",
        "tests/integration/test_public_source_enrollment_api.py",
        "tests/integration/test_source_diagnostics_journey.py",
        "tests/integration/test_source_crawl_outcome.py",
        "tests/integration/test_dashboard_entry.py",
        "tests/integration/test_temporal_event_detail.py"
    )
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $repo "docker-compose.integration-isolated.yml"
$project = "sdeit" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$composeArgs = @("compose", "--project-name", $project, "-f", $composeFile)
$created = $false

Push-Location $repo
try {
    & docker image inspect "semantic_drift_engine-api:latest" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Build the current API image first: docker compose build api"
    }

    Write-Host "Isolated integration project: $project"
    $created = $true
    & docker @composeArgs up --detach --wait db redis
    if ($LASTEXITCODE -ne 0) {
        throw "Isolated DB/Redis failed to become healthy."
    }

    # Fail closed before running test code if this is not the disposable DB.
    & docker @composeArgs run --rm tests python -c "from src.core.database import engine; from sqlalchemy import text; row=engine.connect().execute(text('select current_database()')).scalar_one(); assert row == 'sde_integration', row; print('Test database:', row)"
    if ($LASTEXITCODE -ne 0) {
        throw "Isolation preflight failed."
    }

    & docker @composeArgs run --rm tests python -m scripts.migrate
    if ($LASTEXITCODE -ne 0) {
        throw "Isolated schema migrations failed."
    }

    & docker @composeArgs run --rm tests python -m pytest -q @Tests --tb=short -rs
    if ($LASTEXITCODE -ne 0) {
        throw "Integration tests failed in the isolated stack."
    }

    Write-Host "Integration tests completed against isolated resources."
}
finally {
    if ($created) {
        Write-Host "Removing disposable project $project and its database volume."
        & docker @composeArgs down --volumes --remove-orphans
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Cleanup failed. Remove project explicitly: docker compose --project-name $project -f `"$composeFile`" down --volumes --remove-orphans"
        }
    }
    Pop-Location
}
