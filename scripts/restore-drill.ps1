[CmdletBinding()]
param(
    [string]$SourceContainer = 'agent-room-postgres-1',
    # Resolve the default after PowerShell has initialized $PSScriptRoot.
    # Parameter default expressions run too early for $PSScriptRoot on Windows
    # PowerShell, which otherwise makes the no-BackupPath invocation fail.
    [string]$BackupPath = '',
    [string]$PostgresImage = 'pgvector/pgvector:pg17',
    [string]$Database = 'agent_room',
    [string]$User = 'agent_room',
    [string]$Password,
    [switch]$KeepRestoreContainer
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found."
    }
}

function Wait-Postgres([string]$Container) {
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        & docker exec $Container pg_isready -U $User -d $Database | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    throw "PostgreSQL container '$Container' did not become ready."
}

function Remove-RestoreContainer([string]$Container) {
    # `docker rm` returns an error when startup failed before creating the
    # container. Check first so cleanup never hides the actual drill failure.
    $containerIDOutput = & docker ps -aq --filter "name=^/$Container$"
    $containerID = if ($null -eq $containerIDOutput) { '' } else { ($containerIDOutput -join '').Trim() }
    if (-not [string]::IsNullOrWhiteSpace($containerID)) {
        & docker rm -f $containerID | Out-Null
    }
}

Require-Command docker
if ([string]::IsNullOrWhiteSpace($Password)) {
    throw 'Pass the database password using -Password; do not put it in this script or source control.'
}
if ([string]::IsNullOrWhiteSpace($BackupPath)) {
    $BackupPath = Join-Path $PSScriptRoot '..\artifacts\restore-drill.backup'
}

$backupAbsolute = [IO.Path]::GetFullPath($BackupPath)
$backupDirectory = Split-Path -Parent $backupAbsolute
New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$restoreContainer = "agent-room-restore-$stamp"

try {
    Write-Host "Creating a custom-format backup from $SourceContainer..."
    # Do not pipe a custom-format dump through PowerShell: Windows PowerShell
    # 5.1 has no byte-stream Set-Content and text pipelines corrupt binary data.
    # Start-Process redirects Docker's stdout directly to the backup file.
    $dumpProcess = Start-Process -FilePath docker -ArgumentList @(
        'exec', '-e', "PGPASSWORD=$Password", $SourceContainer,
        'pg_dump', '-U', $User, '-d', $Database,
        '--format=custom', '--no-owner', '--no-privileges'
    ) -NoNewWindow -Wait -PassThru -RedirectStandardOutput $backupAbsolute
    if ($dumpProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $backupAbsolute) -or (Get-Item -LiteralPath $backupAbsolute).Length -eq 0) {
        throw 'pg_dump failed or produced an empty backup.'
    }

    Write-Host "Starting isolated restore container $restoreContainer..."
    & docker run --detach --rm --name $restoreContainer -e "POSTGRES_DB=$Database" -e "POSTGRES_USER=$User" -e "POSTGRES_PASSWORD=$Password" $PostgresImage | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not start isolated restore container.' }
    Wait-Postgres $restoreContainer

    & docker cp $backupAbsolute "${restoreContainer}:/tmp/restore.backup"
    if ($LASTEXITCODE -ne 0) { throw 'Could not copy backup into restore container.' }
    & docker exec -e "PGPASSWORD=$Password" $restoreContainer pg_restore -U $User -d $Database --exit-on-error --no-owner --no-privileges /tmp/restore.backup
    if ($LASTEXITCODE -ne 0) { throw 'pg_restore failed.' }

    $checks = @(
        "SELECT to_regclass('public.tasks') IS NOT NULL AS tasks_table;",
        "SELECT to_regclass('public.outbox') IS NOT NULL AS outbox_table;",
        "SELECT extname = 'vector' AS pgvector_enabled FROM pg_extension WHERE extname = 'vector';",
        "SELECT count(*) AS applied_migrations FROM schema_migrations;"
    )
    foreach ($query in $checks) {
        & docker exec -e "PGPASSWORD=$Password" $restoreContainer psql -U $User -d $Database -v ON_ERROR_STOP=1 -Atc $query
        if ($LASTEXITCODE -ne 0) { throw "Restore verification query failed: $query" }
    }
    Write-Host "Restore drill passed. Backup: $backupAbsolute; isolated container: $restoreContainer"
    if (-not $KeepRestoreContainer) {
        Remove-RestoreContainer $restoreContainer
    }
} catch {
    if (-not $KeepRestoreContainer) {
        Remove-RestoreContainer $restoreContainer
    }
    throw
}
