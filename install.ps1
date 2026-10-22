# One-step install of J.A.R.V.I.S. on Windows. In PowerShell, inside this folder:
#   Set-ExecutionPolicy -Scope Process Bypass; .\install.ps1
Set-Location $PSScriptRoot

function Test-Python {
    # The Microsoft Store "python" alias exists even when Python is not installed.
    try { $v = & python --version 2>&1; return "$v" -match "Python 3\.(1[0-9])" } catch { return $false }
}

function Repair-Winget {
    # On a brand-new Windows the App Installer (winget) is not registered for the user yet and fails with
    # "The file cannot be accessed by the system"; registering it once fixes that.
    try { $null = & winget --version 2>&1; if ($LASTEXITCODE -eq 0) { return $true } } catch {}
    Write-Host "Активирам инсталатора на Windows (winget)..."
    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop
        $null = & winget --version 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Install-Optional($id, $what) {
    # ffmpeg and adb are nice to have (voice, the phone): a failure here must not stop the installation.
    try {
        $ErrorActionPreference = "Continue"
        winget install -e --id $id --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
    } catch {}
    if ($LASTEXITCODE -ne 0) { Write-Host "  $what не се инсталира сега; по-късно: winget install -e --id $id" }
}

if (-not (Test-Python)) {
    Write-Host "Инсталирам Python..."
    if (-not (Repair-Winget)) {
        Write-Host "winget не работи на този компютър. Инсталирай Python 3.12 от https://www.python.org/downloads/windows/"
        Write-Host "(сложи отметка 'Add python.exe to PATH'), после пусни инсталацията отново."
        exit 1
    }
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    Write-Host ""
    Write-Host "Python е инсталиран. Затвори този прозорец, отвори нов терминал в папката и пусни .\install.ps1 отново."
    exit
}

Write-Host "Инсталирам ffmpeg (за гласа) и adb (за Android телефон/TV)..."
if (Repair-Winget) {
    Install-Optional "Gyan.FFmpeg" "ffmpeg (гласът)"
    Install-Optional "Google.PlatformTools" "adb (телефонът)"
} else {
    Write-Host "  winget не работи; ffmpeg и adb ще се инсталират по-късно (Настройки > Връзки показва какво липсва)."
}
$ErrorActionPreference = "Stop"

python -m venv .venv
if ($LASTEXITCODE -ne 0) { Write-Host "Неуспешно създаване на .venv"; exit 1 }
& .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[all]"
if ($LASTEXITCODE -ne 0) { Write-Host "Инсталацията на пакетите се провали. Изпрати ми последните редове отгоре."; exit 1 }
python -m playwright install chromium

jarvis setup
Write-Host ""
Write-Host "Готово. Пускаш го с:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  jarvis app      (прозорецът на Jarvis; само 'jarvis' е конзолата)"
