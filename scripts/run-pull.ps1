# Run the partner pull against Odoo, using the local .env credentials.
# This is the "run" half of the workflow: develop in the cloud session,
# push to GitHub, then run here.
#
# Usage:  .\scripts\run-pull.ps1  [extra args passed to pull_partners.py]
# Example: .\scripts\run-pull.ps1 --limit 5

$ErrorActionPreference = 'Stop'

# Always operate from the repo root (one level up from this script).
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$python = Join-Path $repo 'venv\Scripts\python.exe'

# --- Pre-flight checks -------------------------------------------------------
if (-not (Test-Path $python)) {
    Write-Host "venv not found. Create it first:" -ForegroundColor Red
    Write-Host "  python -m venv venv; venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path (Join-Path $repo '.env'))) {
    Write-Host ".env not found. Copy the example and fill in your Odoo credentials:" -ForegroundColor Red
    Write-Host "  Copy-Item .env.example .env"
    exit 1
}

# --- Pull latest code from GitHub (source of truth) --------------------------
Write-Host "==> git pull" -ForegroundColor Cyan
git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull failed (uncommitted local changes or a diverged branch?). Resolve, then retry." -ForegroundColor Red
    exit 1
}

# --- Run the pull ------------------------------------------------------------
Write-Host "==> running pull_partners.py $args" -ForegroundColor Cyan
& $python pull_partners.py @args
if ($LASTEXITCODE -ne 0) {
    Write-Host "pull_partners.py exited with code $LASTEXITCODE." -ForegroundColor Red
    exit $LASTEXITCODE
}

# --- Summarise the output ----------------------------------------------------
$out = Join-Path $repo 'output\partners.csv'
if (Test-Path $out) {
    $lines = (Get-Content $out | Measure-Object -Line).Lines
    $records = [Math]::Max(0, $lines - 1)   # minus header
    $size = [Math]::Round((Get-Item $out).Length / 1KB, 1)
    Write-Host ""
    Write-Host "Done. $records record(s) -> output\partners.csv ($size KB)" -ForegroundColor Green
}
