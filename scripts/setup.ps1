# LocalPilot setup for Windows 10/11.
# Easiest: double-click install.bat in the project folder. (It runs this script.)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$Root = Get-Location

function Ask($q, $default = $false) {
    $hint = if ($default) { "[Y/n]" } else { "[y/N]" }
    $a = Read-Host "$q $hint"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return $a -match '^(y|yes)$'
}
function Step($n, $text) { Write-Host "`n[$n/7] $text" -ForegroundColor Cyan }
function HasCmd($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }

Write-Host @"

  ==============================================
     LocalPilot  -  your private AI agent setup
  ==============================================
  Everything runs on THIS PC. Nothing is installed without asking.
"@ -ForegroundColor Green

# ---- 1. Python ---------------------------------------------------------------
Step 1 "Checking Python 3.10+"
$py = $null
foreach ($cand in @("py -3.12", "py -3", "python")) {
    try {
        $v = Invoke-Expression "$cand -c `"import sys; print(sys.version_info[:2] >= (3,10))`"" 2>$null
        if ($v -eq "True") { $py = $cand; break }
    } catch {}
}
if (-not $py) {
    Write-Host "Python 3.10+ was not found." -ForegroundColor Yellow
    if ((HasCmd winget) -and (Ask "Install Python 3.12 now with winget?" $true)) {
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        Write-Host "`nPython installed. CLOSE this window and double-click install.bat again." -ForegroundColor Green
    } else {
        Write-Host "Install Python from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run install.bat again."
    }
    Read-Host "Press Enter to exit"; exit 1
}
Write-Host "Using: $py"

# ---- 2. Virtual environment + packages ----------------------------------------
Step 2 "Installing LocalPilot's Python packages (private .venv folder)"
if (-not (Test-Path .venv\Scripts\python.exe)) { Invoke-Expression "$py -m venv .venv" }
$VPY = Join-Path $Root ".venv\Scripts\python.exe"
& $VPY -m pip install --upgrade pip -q
& $VPY -m pip install -r requirements.txt -q
Write-Host "Packages installed." -ForegroundColor Green

# ---- 3. Ollama -------------------------------------------------------------------
Step 3 "Checking Ollama (runs the AI model locally)"
if (-not (HasCmd ollama)) {
    if ((HasCmd winget) -and (Ask "Ollama is not installed. Install it now with winget?" $true)) {
        winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
        $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
    } else {
        Write-Host "Download Ollama from https://ollama.com/download/windows and run install.bat again." -ForegroundColor Yellow
    }
}

# ---- 4. Model for this PC -----------------------------------------------------------
Step 4 "Choosing the best model for this PC"
& $VPY -m agent.hardware
$model = (& $VPY -m agent.hardware --model).Trim()
if (HasCmd ollama) {
    if (-not (Get-Process ollama -ErrorAction SilentlyContinue)) { Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep 3 }
    if (Ask "Download $model now? (one time, a few GB)" $true) { ollama pull $model }
}

# ---- 5. LAYA safety layer (optional) ------------------------------------------------
Step 5 "LAYA safety layer (optional)"
Write-Host "LAYA is a small, fast model that double-checks commands and scans web pages for"
Write-Host "prompt-injection. Needs ~2-3 GB disk (PyTorch + model). LocalPilot works without it."
if (Ask "Install LAYA?" $false) {
    & $VPY -m pip install -r requirements-laya.txt
    Write-Host "Downloading the LAYA model (one time)..."
    & $VPY -c "from agent.config import load_config; from agent.laya_guard import LayaGuard; g=LayaGuard(load_config()); g._load(); print('LAYA:', g.status())"
}

# ---- 6. Browser (optional) -------------------------------------------------------------
Step 6 "Headless browser for JavaScript websites (optional, ~150 MB)"
if (Ask "Install it?" $false) {
    & $VPY -m pip install playwright -q
    & $VPY -m playwright install chromium
}

# ---- 7. Shortcut ------------------------------------------------------------------------
Step 7 "Desktop shortcut"
if (Ask "Create a 'LocalPilot' shortcut on your Desktop?" $true) {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "LocalPilot.lnk"))
    $lnk.TargetPath = Join-Path $Root "LocalPilot.bat"
    $lnk.WorkingDirectory = "$Root"
    $lnk.Description = "LocalPilot - private AI agent"
    $lnk.Save()
}

Write-Host "`nAll set! Start LocalPilot by double-clicking LocalPilot.bat (or the Desktop shortcut)." -ForegroundColor Green
Write-Host "It opens in your browser at http://127.0.0.1:7860"
if (Ask "Start LocalPilot now?" $true) { Start-Process (Join-Path $Root "LocalPilot.bat") }
