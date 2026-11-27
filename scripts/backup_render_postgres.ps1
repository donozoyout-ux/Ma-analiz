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
# PostgreSQL Windows installers usually do not add the tools to PATH.
# Support Windows PowerShell 5.1 and discover the installed clients automatically.
$tools = @{}
foreach ($binary in @("pg_dump", "pg_restore")) {
    $command = Get-Command "$binary.exe" -ErrorAction SilentlyContinue
    if ($command) {
        $tools[$binary] = $command.Source
    } else {
        $installRoot = Join-Path $env:ProgramFiles "PostgreSQL"
        $candidates = @(Get-ChildItem -LiteralPath $installRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object { try { [version]$_.Name } catch { [version]"0.0" } } -Descending |
            ForEach-Object { Join-Path $_.FullName "bin\\$binary.exe" } |
            Where-Object { Test-Path -LiteralPath $_ })
        if ($candidates.Count -eq 0) {
            throw "$binary not found. Install PostgreSQL Command Line Tools (version 16 or later)."
        }
        $tools[$binary] = $candidates[0]
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
    & $tools['pg_dump'] --host=$HostName --port=5432 --username=$DatabaseUser --dbname=$DatabaseName --format=custom --no-owner --no-acl --file=$backup
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $backup -ErrorAction SilentlyContinue
        throw "PG_DUMP_FAILED"
    }
    & $tools['pg_restore'] --list $backup | Out-Null
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
