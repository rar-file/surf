# ============================================================
# SURF - Quick Setup Script (Windows PowerShell)
# ============================================================
# Usage: .\setup.ps1
# ============================================================

Write-Host "Setting up SURF..." -ForegroundColor Cyan
Write-Host ""

# Check for Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "Python is required but not installed." -ForegroundColor Red
    Write-Host "Install Python from: https://python.org"
    exit 1
}

Write-Host "Python found: $(python --version)" -ForegroundColor Green

# Create virtual environment
Write-Host ""
Write-Host "Creating virtual environment..." -ForegroundColor Yellow
python -m venv venv

# Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& .\venv\Scripts\Activate.ps1

# Install dependencies
Write-Host ""
Write-Host "Installing dependencies..." -ForegroundColor Yellow
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q

# Install Playwright browsers
Write-Host ""
Write-Host "Installing browser (Chromium)..." -ForegroundColor Yellow
python -m playwright install chromium

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "To use SURF:"
Write-Host ""
Write-Host "1. Activate the environment:"
Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "2. Run SURF:"
Write-Host "   python chat.py" -ForegroundColor Cyan
Write-Host "   python chat.py --search" -ForegroundColor Cyan
Write-Host ""
