# Set up GHFS on Windows
# Run as Administrator or in a PowerShell window with unrestricted execution policy:
#   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#   .\scripts\install.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== GHFS installer (Windows) ===" -ForegroundColor Cyan

# ---- Check Python ----
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Install Python 3.9+ from https://python.org and retry."
    exit 1
}
$pyver = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "Python $pyver detected." -ForegroundColor Green

# ---- Check / install WinFSP ----
$winfspKey = "HKLM:\SOFTWARE\WOW6432Node\WinFsp"
$winfspInstalled = Test-Path $winfspKey

if ($winfspInstalled) {
    Write-Host "WinFSP already installed." -ForegroundColor Green
} else {
    Write-Host "WinFSP not found. Downloading installer..." -ForegroundColor Yellow

    $winfspUrl = "https://github.com/winfsp/winfsp/releases/download/v2.0/winfsp-2.0.23075.msi"
    $msiPath   = "$env:TEMP\winfsp.msi"

    Invoke-WebRequest -Uri $winfspUrl -OutFile $msiPath -UseBasicParsing
    Write-Host "Installing WinFSP (requires elevation)..."
    Start-Process msiexec.exe -ArgumentList "/i `"$msiPath`" /quiet ADDLOCAL=ALL" -Verb RunAs -Wait
    Remove-Item $msiPath

    if (-not (Test-Path $winfspKey)) {
        Write-Error "WinFSP installation failed. Please install manually from https://winfsp.dev/rel/"
        exit 1
    }
    Write-Host "WinFSP installed." -ForegroundColor Green
}

# ---- Install Python packages ----
Write-Host "Installing winfspy Python package..." -ForegroundColor Yellow
python -m pip install --upgrade winfspy

Write-Host "Installing GHFS..." -ForegroundColor Yellow
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir
python -m pip install -e $repoRoot

Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Quick start:"
Write-Host '  $env:GITHUB_TOKEN = "ghp_your_token_here"'
Write-Host "  ghfs mount G:"
Write-Host "  dir G:"
Write-Host "  # Ctrl+C to unmount"
Write-Host ""
Write-Host "Or run as a module:"
Write-Host '  python -m ghfs mount G: --token ghp_your_token_here'
