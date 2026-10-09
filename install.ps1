# One-step install of J.A.R.V.I.S. on Windows. In PowerShell, inside this folder:
#   Set-ExecutionPolicy -Scope Process Bypass; .\install.ps1
Set-Location $PSScriptRoot

function Test-Python {
    # The Microsoft Store "python" alias exists even when Python is not installed.
    try { $v = & python --version 2>&1; return "$v" -match "Python 3\.(1[0-9])" } catch { return $false }
}

if (-not (Test-Python)) {
    Write-Host "Инсталирам Python..."
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    Write-Host ""
    Write-Host "Python е инсталиран. Затвори този прозорец, отвори нов терминал в папката и пусни .\install.ps1 отново."
    exit
}

Write-Host "Инсталирам ffmpeg (за гласа) и adb (за Android телефон/TV)..."
winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements | Out-Null
winget install -e --id Google.PlatformTools --accept-source-agreements --accept-package-agreements | Out-Null

python -m venv .venv
if ($LASTEXITCODE -ne 0) { Write-Host "Неуспешно създаване на .venv"; exit 1 }
& .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[all]"
if ($LASTEXITCODE -ne 0) { Write-Host "Инсталацията на пакетите се провали. Изпрати ми последните редове отгоре."; exit 1 }
python -m playwright install chromium

jarvis setup
Write-Host ""
Write-Host "Готово. Следващия път го пускаш с:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  jarvis"
