param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Environment not found. Run .\setup.ps1 first."
}

$VoiceUrl = "http://127.0.0.1:$Port"
$ExistingService = $null
try {
    $ExistingService = Invoke-RestMethod -Uri "$VoiceUrl/api/status" -TimeoutSec 2
} catch { }
if ($ExistingService -and $ExistingService.service -eq "romanian-realtime-asr") {
    Write-Host "Voice recognition is already running: $VoiceUrl"
    if ($OpenBrowser) { Start-Process $VoiceUrl }
    exit 0
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use by another service. Choose another port with -Port."
}

if ($OpenBrowser) {
    # This helper opens the UI only after this service answers. It never starts
    # the microphone, and it is hidden so it cannot cover the presentation.
    $BrowserWaiter = @"
`$VoiceDeadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt `$VoiceDeadline) {
    try {
        `$VoiceStatus = Invoke-RestMethod -Uri '$VoiceUrl/api/status' -TimeoutSec 2
        if (`$VoiceStatus.service -eq 'romanian-realtime-asr') {
            Start-Process '$VoiceUrl'
            exit 0
        }
    } catch { }
    Start-Sleep -Milliseconds 700
}
"@
    $EncodedWaiter = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($BrowserWaiter))
    $BrowserHelper = Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $EncodedWaiter
    )
}

Push-Location -LiteralPath $ProjectRoot
try {
    & $VenvPython -m uvicorn web_server:app --host 127.0.0.1 --port $Port --ws-max-size 2097152 --ws-max-queue 16
    $VoiceExitCode = $LASTEXITCODE
} finally {
    Pop-Location
    if ($BrowserHelper -and -not $BrowserHelper.HasExited) {
        Stop-Process -Id $BrowserHelper.Id -ErrorAction SilentlyContinue
    }
}
exit $VoiceExitCode
