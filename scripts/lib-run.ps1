# Shared helpers for the run-*.ps1 scripts. Dot-sourced, not run directly.
# Keeps the pre-flight checks and the "sync with GitHub" step identical
# across every runner so the behaviour can't drift between them.
# Kept ASCII-only: Windows PowerShell 5.1 misreads non-ASCII in .ps1 files.

$ErrorActionPreference = 'Stop'

# Verify the venv and .env exist; return the path to the venv python.
function Assert-Preflight {
    param([Parameter(Mandatory)][string] $Repo)

    $python = Join-Path $Repo 'venv\Scripts\python.exe'
    if (-not (Test-Path $python)) {
        Write-Host "venv not found. Create it first:" -ForegroundColor Red
        Write-Host "  python -m venv venv; venv\Scripts\python.exe -m pip install -r requirements.txt"
        exit 1
    }
    if (-not (Test-Path (Join-Path $Repo '.env'))) {
        Write-Host ".env not found. Copy the example and fill in your Odoo credentials:" -ForegroundColor Red
        Write-Host "  Copy-Item .env.example .env"
        exit 1
    }
    return $python
}

# Explicitly check GitHub for new commits and fast-forward onto them.
# Prints exactly what it found, and refuses to run on unpushed/diverged state
# so you never run against a surprising code version.
function Sync-Repo {
    Write-Host "==> Checking GitHub for updates..." -ForegroundColor Cyan
    git fetch --quiet origin
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    git fetch failed (no network / no access). Aborting." -ForegroundColor Red
        exit 1
    }

    $local  = (git rev-parse '@').Trim()
    $remote = (git rev-parse '@{u}').Trim()
    $base   = (git merge-base '@' '@{u}').Trim()

    if ($local -eq $remote) {
        Write-Host "    Up to date with origin - no changes online." -ForegroundColor Green
    }
    elseif ($local -eq $base) {
        $count = (git rev-list --count '@..@{u}').Trim()
        Write-Host "    $count new commit(s) online - pulling:" -ForegroundColor Yellow
        git --no-pager log --oneline --no-decorate '@..@{u}' | ForEach-Object { Write-Host "      $_" }
        git merge --ff-only --quiet '@{u}'
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    Fast-forward failed. Resolve manually, then rerun." -ForegroundColor Red
            exit 1
        }
        Write-Host "    Updated to latest." -ForegroundColor Green
    }
    elseif ($remote -eq $base) {
        Write-Host "    You have local commits not pushed to GitHub." -ForegroundColor Red
        Write-Host "    Push them (git push) before running, so GitHub stays the source of truth." -ForegroundColor Red
        exit 1
    }
    else {
        Write-Host "    Local and GitHub have diverged. Resolve manually, then rerun." -ForegroundColor Red
        exit 1
    }
}
