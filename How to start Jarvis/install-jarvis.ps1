# Installs J.A.R.V.I.S. on a Windows computer from scratch. Run it in PowerShell with one line:
#   irm https://raw.githubusercontent.com/kafe7o/Jarvis/main/How%20to%20start%20Jarvis/install-jarvis.ps1 | iex
# It downloads the code into Documents\Jarvis (or uses the copy already there), installs Python if needed,
# then runs install.ps1, which installs everything else and asks for the keys in Bulgarian.

$ErrorActionPreference = "Stop"
$folder = Join-Path $HOME "Documents\Jarvis"
$zip = "https://github.com/kafe7o/Jarvis/archive/refs/heads/main.zip"

function Test-Python {
    try { $v = & python --version 2>&1; return "$v" -match "Python 3\.(1[0-9])" } catch { return $false }
}

if (-not (Test-Path (Join-Path $folder "install.ps1"))) {
    Write-Host "Свалям Jarvis в $folder ..."
    $tmp = Join-Path $env:TEMP "jarvis-main.zip"
    Invoke-WebRequest $zip -OutFile $tmp
    $out = Join-Path $env:TEMP "jarvis-unzip"
    if (Test-Path $out) { Remove-Item $out -Recurse -Force }
    Expand-Archive $tmp -DestinationPath $out -Force
    New-Item -ItemType Directory -Force (Split-Path $folder) | Out-Null
    Move-Item (Join-Path $out "Jarvis-main") $folder
    Remove-Item $tmp -Force
} else {
    Write-Host "Jarvis вече е свален в $folder."
}
Set-Location $folder

if (-not (Test-Python)) {
    Write-Host "Инсталирам Python..."
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    Write-Host ""
    Write-Host "Python е инсталиран. Затвори този прозорец, отвори нов PowerShell и пусни същия ред още веднъж."
    exit
}

Set-ExecutionPolicy -Scope Process Bypass -Force
& (Join-Path $folder "install.ps1")
