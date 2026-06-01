param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Resolve-Path (Join-Path $root "..\..")
Set-Location $projectRoot

if ($Clean) {
    Remove-Item -Recurse -Force "$root\dist" -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force "$root\build" -ErrorAction SilentlyContinue
    Remove-Item -Force "$root\KoperasiPrintAgent.spec" -ErrorAction SilentlyContinue
}

venv\Scripts\python -m pip install pyinstaller pywin32

venv\Scripts\pyinstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name KoperasiPrintAgent `
    --distpath "$root\dist" `
    --workpath "$root\build" `
    --specpath "$root" `
    "$root\app.py"

Write-Host "Build selesai: $root\dist\KoperasiPrintAgent.exe"
