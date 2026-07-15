@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
if errorlevel 1 (
    echo.
    echo La compilation a echoue.
    pause
    exit /b 1
)
echo.
echo Compilation terminee : dist\TanukiTools.exe
pause
