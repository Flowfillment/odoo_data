# Run once after cloning to enable the in-repo git hooks (secret scanning).
# Usage:  .\scripts\setup-hooks.ps1
git config core.hooksPath .githooks
Write-Host "Git hooks enabled (core.hooksPath = .githooks)."
Write-Host "Make sure dependencies are installed: venv\Scripts\python.exe -m pip install -r requirements.txt"
