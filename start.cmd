@echo off
REM ============================================================
REM  AI Interview Coach - one-click launcher (recommended entry)
REM
REM  Why this file exists:
REM    Windows default ExecutionPolicy is "Restricted", so running
REM    .\start.ps1 directly fails with "running scripts is disabled
REM    on this system". This wrapper passes -ExecutionPolicy Bypass
REM    for THIS process only. It does not change any system setting.
REM
REM  Usage: double-click this file, or run  .\start.cmd
REM         Ports occupied by another project? Run side by side:
REM           .\start.cmd -BackendPort 8010 -FrontendPort 5183
REM ============================================================
setlocal
cd /d "%~dp0"

where powershell >nul 2>nul
if errorlevel 1 (
    echo [ERROR] powershell not found.
    pause
    exit /b 1
)

REM start.ps1 must be saved as UTF-8 *with* BOM: Windows PowerShell 5.1
REM decodes BOM-less UTF-8 using the local ANSI code page, which mangles the
REM Chinese comments and swallows quotes -> "Missing closing '}'" parse errors.
REM Checking here (not inside start.ps1) because a corrupted file cannot parse
REM its own guard.
powershell -NoProfile -Command "$b=[IO.File]::ReadAllBytes('%~dp0start.ps1'); if ($b.Length -lt 3 -or $b[0] -ne 239 -or $b[1] -ne 187 -or $b[2] -ne 191) { exit 9 }"
if "%ERRORLEVEL%"=="9" (
    echo.
    echo [ERROR] start.ps1 is missing its UTF-8 BOM.
    echo         Re-save it as "UTF-8 with BOM" ^(not plain UTF-8^), then retry.
    echo         In VS Code: bottom-right encoding -^> "Save with Encoding" -^> UTF-8 with BOM.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
    echo.
    echo [ERROR] Launcher exited with code %RC%. See the output above.
    echo.
    pause
)

endlocal
exit /b %RC%
