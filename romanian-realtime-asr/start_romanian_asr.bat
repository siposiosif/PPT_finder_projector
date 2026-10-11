@echo off
setlocal
title Romanian Realtime ASR - GPU Service

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Voice Python environment not found. Run setup.ps1 before the event.
    pause
    exit /b 1
)

echo.
echo Starting independent Romanian voice recognition...
echo Open:   http://127.0.0.1:8765
echo The microphone starts only when you press its button.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File ".\run_web.ps1" -OpenBrowser %*

echo.
echo Service stopped.
pause
