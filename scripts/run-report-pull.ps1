# Pull the Sales Analysis staging CSVs (phase 1) from Odoo, using the local
# .env credentials. This is the "run" half of the workflow: develop in the
# cloud session, push to GitHub, then run here.
#
# Usage:  .\scripts\run-report-pull.ps1  [extra args passed to pull_report_data.py]
# Example: .\scripts\run-report-pull.ps1 --limit 5
# Example: .\scripts\run-report-pull.ps1 --only res_partner,res_currency

$ErrorActionPreference = 'Stop'

# Always operate from the repo root (one level up from this script).
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
. (Join-Path $PSScriptRoot 'lib-run.ps1')

$python = Assert-Preflight -Repo $repo   # checks venv + .env
Sync-Repo                                # check GitHub, fast-forward, or abort

# --- Run the pull ------------------------------------------------------------
Write-Host "==> running pull_report_data.py $args" -ForegroundColor Cyan
& $python pull_report_data.py @args
if ($LASTEXITCODE -ne 0) {
    Write-Host "pull_report_data.py exited with code $LASTEXITCODE." -ForegroundColor Red
    exit $LASTEXITCODE
}

# --- Summarise the output ----------------------------------------------------
$csvs = @('account_move.csv', 'account_move_line.csv', 'product_template.csv',
          'res_currency.csv', 'res_partner.csv', 'sale_order.csv',
          'sale_order_line.csv')
Write-Host ""
foreach ($name in $csvs) {
    $out = Join-Path $repo "output\$name"
    if (Test-Path $out) {
        $lines = (Get-Content $out | Measure-Object -Line).Lines
        $records = [Math]::Max(0, $lines - 1)   # minus header
        $size = [Math]::Round((Get-Item $out).Length / 1KB, 1)
        Write-Host ("  {0,-25} {1,8} record(s)  ({2} KB)" -f $name, $records, $size) -ForegroundColor Green
    }
}
