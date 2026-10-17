# Read-only operator preflight. Does not stop services or create backup data.
# A PASS does not prove no external process can reconnect after this check.
$ErrorActionPreference = 'Stop'

$running = @(docker compose ps --services --status running)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect live Compose services.' }

$writers = @(
    'api',
    'crawler_scheduler',
    'vector_worker',
    'semantic_recovery_worker',
    'alert_delivery_worker',
    'lineage_invalidation_executor',
    'email_worker',
    'backup_maintenance',
    'knowledge_backup_maintenance'
)
$stillRunning = @($running | ForEach-Object { $_.Trim() } | Where-Object { $writers -contains $_ })
if ($stillRunning.Count -gt 0) {
    Write-Host ('STOP: SDE writer-capable services are still running: ' + ($stillRunning -join ', '))
    exit 2
}

# --no-deps prevents runtime_volume_init from mutating volume metadata.
# --pull never and the prebuilt-only Compose image prevent an implicit
# build or network pull during a supposedly read-only inspection.
docker compose --profile maintenance run --rm --no-deps --pull never `
    knowledge_backup_maintenance `
    python -m scripts.check_knowledge_backup_connections_readonly --require-quiescent
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host "STOP: Database connections were found or visibility insufficient (exit $code)."
    exit $code
}
Write-Host 'PASS: Known Compose writers stopped and PostgreSQL connection gate clear.'
Write-Host 'NOTE: Point-in-time only. External writers require operator verification; no lock is held.'
