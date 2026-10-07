# One-step install of J.A.R.V.I.S. on Windows. In PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass; .\install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    winget install -e --id Python.Python.3.12
    Write-Host "Python е инсталиран. Затвори и отвори PowerShell, после пусни install.ps1 отново."
    exit
}
winget install -e --id Gyan.FFmpeg --accept-source-agreements 2>$null
winget install -e --id Google.PlatformTools --accept-source-agreements 2>$null

python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[all]"
python -m playwright install chromium

jarvis setup
Write-Host ""
Write-Host "Пусни го с:  .\.venv\Scripts\Activate.ps1; jarvis serve"
