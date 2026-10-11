$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Environment not found. Run .\setup.ps1 first."
}

& $VenvPython (Join-Path $ProjectRoot "realtime_asr.py") @args
