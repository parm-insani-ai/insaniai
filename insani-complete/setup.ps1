# insani - Local development setup (Windows PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File setup.ps1

Write-Host ""
Write-Host "========================================"
Write-Host "  insani - Local Development Setup"
Write-Host "========================================"
Write-Host ""

# Check Python
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3") {
            $python = $cmd
            break
        }
    } catch {}
}

if (-not $python) {
    Write-Host "Error: Python 3 is not installed." -ForegroundColor Red
    Write-Host "Download it from https://python.org"
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "OK Python found ($python)" -ForegroundColor Green

# Check directory structure
if (-not (Test-Path "insani-backend") -or -not (Test-Path "insani-project")) {
    Write-Host "Error: Run this from the folder with insani-backend and insani-project" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Backend setup
Write-Host ""
Write-Host "Setting up backend..." -ForegroundColor Yellow
Set-Location insani-backend

if (-not (Test-Path "venv")) {
    Write-Host "  Creating virtual environment..."
    & $python -m venv venv
}

& .\venv\Scripts\Activate.ps1
Write-Host "  OK Virtual environment activated" -ForegroundColor Green

Write-Host "  Installing dependencies..."
pip install -r requirements.txt --quiet 2>$null
Write-Host "  OK Dependencies installed" -ForegroundColor Green

if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    $jwtSecret = & $python -c "import secrets; print(secrets.token_urlsafe(48))"

    Write-Host ""
    Write-Host "  You need an Anthropic API key for AI chat." -ForegroundColor Yellow
    Write-Host "  Get one at: https://console.anthropic.com/settings/keys"
    Write-Host ""
    $apiKey = Read-Host "  Enter your ANTHROPIC_API_KEY (or press Enter to skip)"

    if ([string]::IsNullOrEmpty($apiKey)) {
        $apiKey = "sk-ant-placeholder-add-your-key"
        Write-Host "  No API key entered. Edit .env later to add it." -ForegroundColor Yellow
    }

    $envContent = Get-Content .env -Raw
    $envContent = $envContent -replace "ANTHROPIC_API_KEY=.*", "ANTHROPIC_API_KEY=$apiKey"
    $envContent = $envContent -replace "JWT_SECRET=.*", "JWT_SECRET=$jwtSecret"
    Set-Content .env $envContent
    Write-Host "  OK .env created" -ForegroundColor Green
} else {
    Write-Host "  OK .env already exists" -ForegroundColor Green
}

Set-Location ..

Write-Host ""
Write-Host "========================================"
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "========================================"
Write-Host ""
Write-Host "To start, open TWO PowerShell windows:"
Write-Host ""
Write-Host "  Window 1 - Backend:" -ForegroundColor Yellow
Write-Host "    cd insani-backend"
Write-Host "    .\venv\Scripts\Activate.ps1"
Write-Host "    uvicorn app.main:app --reload --port 8000"
Write-Host ""
Write-Host "  Window 2 - Frontend:" -ForegroundColor Yellow
Write-Host "    cd insani-project"
Write-Host "    python -m http.server 3000"
Write-Host ""
Write-Host "  Then open http://localhost:3000" -ForegroundColor Yellow
Write-Host ""

$start = Read-Host "Start both servers now? (y/n)"

if ($start -eq "y" -or $start -eq "Y") {
    Write-Host ""
    Write-Host "Starting servers..." -ForegroundColor Green

    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd insani-backend; .\venv\Scripts\Activate.ps1; uvicorn app.main:app --reload --port 8000"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd insani-project; python -m http.server 3000"

    Start-Sleep -Seconds 3
    Start-Process "http://localhost:3000"

    Write-Host "  Backend:  http://localhost:8000" -ForegroundColor Green
    Write-Host "  Frontend: http://localhost:3000" -ForegroundColor Green
    Write-Host ""
    Write-Host "Two new windows opened. Close them to stop."
}

Write-Host ""
Read-Host "Press Enter to exit"
