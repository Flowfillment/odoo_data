# Transform the Sales Analysis staging CSVs (phase 2) into the fact +
# dimension tables, using the files in output/. This is the "run" half of
# the workflow: develop in the cloud session, push to GitHub, then run here.
#
# Usage:  .\scripts\run-report-transform.ps1  [extra args passed to transform_report_data.py]
# Example: .\scripts\run-report-transform.ps1 --iso-weeks
# Example: .\scripts\run-report-transform.ps1 --cutoff 2024-04-01

$ErrorActionPreference = 'Stop'

# Always operate from the repo root (one level up from this script).
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
. (Join-Path $PSScriptRoot 'lib-run.ps1')

$python = Assert-Preflight -Repo $repo   # checks venv + .env
Sync-Repo                                # check GitHub, fast-forward, or abort

# --- Run the transform ---------------------------------------------------------
Write-Host "==> running transform_report_data.py $args" -ForegroundColor Cyan
& $python transform_report_data.py @args
if ($LASTEXITCODE -ne 0) {
    Write-Host "transform_report_data.py exited with code $LASTEXITCODE." -ForegroundColor Red
    exit $LASTEXITCODE
}

# --- Summarise the output ------------------------------------------------------
$csvs = @('report_invoiced.csv', 'dim_product.csv', 'dim_partner.csv',
          'dim_currency.csv', 'dim_date.csv', 'dim_uom.csv', 'dim_company.csv',
          'refresh_date_time.csv')
Write-Host ""
foreach ($name in $csvs) {
    $out = Join-Path $repo "output\report\$name"
    if (Test-Path $out) {
        $lines = (Get-Content $out | Measure-Object -Line).Lines
        $records = [Math]::Max(0, $lines - 1)   # minus header
        $size = [Math]::Round((Get-Item $out).Length / 1KB, 1)
        Write-Host ("  {0,-25} {1,8} row(s)  ({2} KB)" -f $name, $records, $size) -ForegroundColor Green
    }
}
