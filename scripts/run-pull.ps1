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
. (Join-Path $PSScriptRoot 'lib-run.ps1')

$python = Assert-Preflight -Repo $repo   # checks venv + .env
Sync-Repo                                # check GitHub, fast-forward, or abort

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
