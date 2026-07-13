# Full refresh of the Sales Analysis pipeline: sync the repo with GitHub,
# run phase 1 (extract) and phase 2 (transform), and finish with the run
# report (durations per phase, records downloaded, observations). The
# report is also appended to output\refresh_log.md.
#
# Usage:  .\scripts\run-full-refresh.ps1  [extra args passed to refresh_report_data.py]
# Example: .\scripts\run-full-refresh.ps1 --iso-weeks
# Example: .\scripts\run-full-refresh.ps1 --all-dates

$ErrorActionPreference = 'Stop'

# Always operate from the repo root (one level up from this script).
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
. (Join-Path $PSScriptRoot 'lib-run.ps1')

$python = Assert-Preflight -Repo $repo   # checks venv + .env

# Time the git sync so it shows up in the run report.
$sw = [System.Diagnostics.Stopwatch]::StartNew()
Sync-Repo                                # check GitHub, fast-forward, or abort
$sw.Stop()
$syncSeconds = [Math]::Round($sw.Elapsed.TotalSeconds, 1)

# --- Run the full refresh ------------------------------------------------------
Write-Host "==> running refresh_report_data.py $args" -ForegroundColor Cyan
& $python refresh_report_data.py --sync-seconds $syncSeconds @args
if ($LASTEXITCODE -ne 0) {
    Write-Host "refresh_report_data.py exited with code $LASTEXITCODE." -ForegroundColor Red
    exit $LASTEXITCODE
}
