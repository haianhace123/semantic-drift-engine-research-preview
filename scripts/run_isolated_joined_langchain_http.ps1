<#
Joined external LangChain SDK -> disposable PGVector -> SDE HTTP integration.
Builds an ephemeral image with a unique tag. Never overwrites the production
semantic_drift_engine-api:latest tag, starts production services, or publishes
host ports. Only removes resources in the random project created by this run.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$baseCompose = Join-Path $repo "docker-compose.integration-isolated.yml"
$id = [guid]::NewGuid().ToString("N").Substring(0, 12)
$project = "sdejoined$id"
$image = "sde-joined-$($id):local"
$tempRoot = Join-Path $env:TEMP ("SDE_Joined_$id")
$override = Join-Path $tempRoot "isolated-image.yml"
$created = $false
$imageBuilt = $false

if (-not (Test-Path -LiteralPath $baseCompose -PathType Leaf)) {
    throw "Missing isolated Compose manifest."
}
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
# Override resolution is relative to the first Compose file (repository root).
# ASCII avoids Windows PowerShell 5.1 UTF-8 without BOM decoding mistakes.
$yaml = @(
    "services:"
    "  api:"
    "    image: $image"
    "    build:"
    "      context: ."
    "      dockerfile: Dockerfile"
    "  tests:"
    "    image: $image"
) -join "`n"
[System.IO.File]::WriteAllText($override, $yaml + "`n", [System.Text.Encoding]::ASCII)
$compose = @("compose", "--project-name", $project, "-f", $baseCompose, "-f", $override)

Push-Location $repo
try {
    $configJson = (& docker @compose config --format json | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose config failed." }
    $cfg = $configJson | ConvertFrom-Json
    $expectedServices = @("api", "db", "integration_downstream_db", "redis", "tests")
    $actualServices = @($cfg.services.PSObject.Properties.Name | Sort-Object)
    if (@(Compare-Object ($expectedServices | Sort-Object) $actualServices).Count -ne 0) {
        throw "Unexpected service set in isolated Compose project."
    }
    if ($cfg.services.api.image -ne $image -or $cfg.services.tests.image -ne $image) {
        throw "Image isolation override did not take effect."
    }
    foreach ($serviceName in $expectedServices) {
        $svc = $cfg.services.$serviceName
        if ($svc.ports -or $svc.network_mode -or $svc.privileged -or $svc.container_name -or $svc.secrets) {
            throw "Unsafe network/privilege/container override for $serviceName"
        }
        foreach ($volume in @($svc.volumes)) {
            if ($null -ne $volume -and $volume.type -ne "volume") {
                throw "Non-isolated mount refused for $serviceName"
            }
        }
    }
    if ($cfg.volumes) {
        foreach ($volume in $cfg.volumes.PSObject.Properties) {
            if ($volume.Value.external -or -not ($volume.Value.name -like ($project + "_*"))) {
                throw "Volume is external or not owned by disposable project: $($volume.Name)"
            }
        }
    }
    if ($cfg.networks) {
        foreach ($network in $cfg.networks.PSObject.Properties) {
            if ($network.Value.external -or -not ($network.Value.name -like ($project + "_*"))) {
                throw "Network is external or not owned by disposable project: $($network.Name)"
            }
        }
    }
    Write-Host "ISOLATED_COMPOSE_PREFLIGHT_PASSED: $project"
    Write-Host "BUILDING_EPHEMERAL_IMAGE: $image"
    & docker @compose build api
    if ($LASTEXITCODE -ne 0) { throw "Disposable candidate image build failed." }
    $imageBuilt = $true

    $created = $true
    & docker @compose up --detach --wait db redis integration_downstream_db
    if ($LASTEXITCODE -ne 0) { throw "Disposable DB/Redis services failed." }
    & docker @compose run --rm tests python -m scripts.migrate
    if ($LASTEXITCODE -ne 0) { throw "Disposable authority migrations failed." }
    & docker @compose up --detach --wait api
    if ($LASTEXITCODE -ne 0) { throw "Disposable HTTP API not ready." }

    # A venv inside the disposable tests container keeps optional SDK packages
    # out of the SDE API deployment image and host Python environment.
    # One-line POSIX command avoids Windows CRLF argument interpretation.
    $command = "python -m venv --system-site-packages /tmp/sde-joined-venv && /tmp/sde-joined-venv/bin/python -m pip install -q -r requirements.integration-langchain.txt && /tmp/sde-joined-venv/bin/python -m scripts.ci_joined_langchain_http_gate"
    & docker @compose run --rm -e "SDE_JOINED_DOWNSTREAM_DATABASE_URL=postgresql://downstream_test:downstream_test@integration_downstream_db:5432/downstream_test" tests sh -c $command
    if ($LASTEXITCODE -ne 0) { throw "Joined real SDK/PGVector/HTTP acceptance failed." }
    Write-Host "SDE_JOINED_ACCEPTANCE_VERIFIED: isolated real SDK to persisted event to HTTP block"
    Write-Host "NOT_PROVEN: autonomous classification, physical recovery, live production"
}
finally {
    if ($created -and $project.StartsWith("sdejoined")) {
        Write-Host "Cleaning only disposable Compose project: $project"
        & docker @compose down --volumes --remove-orphans
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Disposable Compose cleanup failed. Inspect ONLY project $project."
        }
    }
    if ($imageBuilt) {
        & docker image rm $image *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Disposable image tag could not be removed: $image"
        }
    }
    Pop-Location
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
