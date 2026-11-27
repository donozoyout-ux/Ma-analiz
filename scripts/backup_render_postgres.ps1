# Read-only logical backup of the known Render PostgreSQL database.
# Execute locally on Windows with PostgreSQL 16+ client tools installed.
# NEVER commit the resulting dump, password, or connection string.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$HostName,
    [Parameter(Mandatory=$true)][string]$DatabaseUser,
    [string]$OutputFolder = "$HOME\\RenderBackups"
)
$ErrorActionPreference = "Stop"
$DatabaseName = "cbcxbcxbcv"
$ExpectedInstance = "dpg-dbtlovifngtc739kaf1g-a"
if ($HostName -ne $ExpectedInstance -and
    $HostName -ne "$ExpectedInstance.frankfurt-postgres.render.com") {
    throw "TARGET_HOST_MISMATCH: verify the host in Render Dashboard before proceeding."
}
if ($DatabaseUser -ne "cbcxbcxbcv_user") {
    throw "TARGET_USER_MISMATCH: verify the database username in Render."
}
foreach ($binary in @("pg_dump", "pg_restore")) {
    if (-not (Get-Command $binary -ErrorAction SilentlyContinue)) {
        throw "$binary is not on PATH. Install PostgreSQL command-line utilities first."
    }
}
New-Item -ItemType Directory -Force -Path $OutputFolder | Out-Null
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$backup = Join-Path $OutputFolder "cbcxbcxbcv-$timestamp.dump"
$secret = Read-Host -AsSecureString "Render external PostgreSQL password (never echoed)"
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$env:PGSSLMODE = "require"
try {
    & pg_dump --host=$HostName --port=5432 --username=$DatabaseUser --dbname=$DatabaseName --format=custom --no-owner --no-acl --file=$backup
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $backup -ErrorAction SilentlyContinue
        throw "PG_DUMP_FAILED"
    }
    & pg_restore --list $backup | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "PG_RESTORE_LIST_FAILED: backup was not validated"
    }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $backup).Hash.ToLowerInvariant()
    $length = (Get-Item -LiteralPath $backup).Length
    if ($length -le 0) { throw "EMPTY_BACKUP" }
    Write-Output "BACKUP_CREATED: $backup"
    Write-Output "BACKUP_BYTES: $length"
    Write-Output "BACKUP_SHA256: $hash"
    Write-Output "BACKUP_CONTENTS_LIST: PASS"
    Write-Output "RESTORE_REHEARSAL: NOT_RUN (requires separately isolated database)"
    Write-Output "SERVICE_DATABASE_IDENTITY: NOT_VERIFIED"
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue
}
