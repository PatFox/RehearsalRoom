@echo off
setlocal enabledelayedexpansion
REM ══════════════════════════════════════════════════════════════════════
REM  Rehearsal Room — release script
REM
REM  Usage:
REM    release.bat                        auto-increment patch, prompt for notes
REM    release.bat 1.2.0                  specific version,   prompt for notes
REM    release.bat "my release notes"     auto-increment patch, notes supplied
REM    release.bat 1.2.0 "release notes"  specific version,   notes supplied
REM ══════════════════════════════════════════════════════════════════════

set VERSION_FILE=.version
set INSTALLER_DIR=installer\output

REM ── Detect arguments ──────────────────────────────────────────────────
REM If first arg matches x.y.z it's a version number, otherwise it's notes.
set ARG1=%~1
set ARG2=%~2
set VERSION=
set NOTES=

if not "%ARG1%"=="" (
    REM Test if ARG1 looks like a version number (digits.digits.digits)
    echo %ARG1% | findstr /r "^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$" >nul 2>&1
    if !errorlevel!==0 (
        set VERSION=%ARG1%
        set NOTES=%ARG2%
    ) else (
        REM First arg is notes, not a version
        set NOTES=%ARG1%
    )
)

REM ── Resolve version ───────────────────────────────────────────────────
if "%VERSION%"=="" (
    REM Load previous version and increment the patch number
    set PREV_VERSION=0.0.0
    if exist %VERSION_FILE% (
        set /p PREV_VERSION=<%VERSION_FILE%
    )
    for /f "tokens=*" %%i in ('powershell -NoProfile -Command ^
        "$v = '%PREV_VERSION%'.Split('.'); $v[2] = [int]$v[2] + 1; $v -join '.'"') do (
        set VERSION=%%i
    )
    echo No version supplied — auto-incremented to v!VERSION! (was v%PREV_VERSION%)
)

REM ── Prompt for notes if not supplied ─────────────────────────────────
if "%NOTES%"=="" (
    echo.
    echo Enter release notes (single line, press Enter when done):
    set /p NOTES="Notes: "
)
if "%NOTES%"=="" set NOTES=Release v%VERSION%

REM ── Confirm ───────────────────────────────────────────────────────────
echo.
echo ══════════════════════════════════════════════════════
echo   Version : v%VERSION%
echo   Notes   : %NOTES%
echo ══════════════════════════════════════════════════════
echo.
set /p CONFIRM="Proceed? (Y/N): "
if /i not "%CONFIRM%"=="Y" (
    echo Cancelled.
    exit /b 0
)

REM ── Check gh CLI ──────────────────────────────────────────────────────
gh --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: GitHub CLI (gh) is not installed or not on PATH.
    echo.
    echo Install it from: https://cli.github.com
    echo   winget install GitHub.cli      ^(recommended^)
    echo   or download the installer from the link above.
    echo.
    echo After installing, run:  gh auth login
    echo Then re-run this script.
    exit /b 1
)

REM ── Build ─────────────────────────────────────────────────────────────
echo.
echo [1/3] Building executable and installer...
call build.bat %VERSION%
if errorlevel 1 (
    echo.
    echo Build failed — aborting release.
    exit /b 1
)

REM ── Locate installer ──────────────────────────────────────────────────
set INSTALLER=%INSTALLER_DIR%\RehearsalRoom-v%VERSION%-Setup.exe
if not exist "%INSTALLER%" (
    echo.
    echo ERROR: Expected installer not found: %INSTALLER%
    echo Check that Inno Setup is installed and the build completed successfully.
    exit /b 1
)

REM ── Tag and push ──────────────────────────────────────────────────────
echo.
echo [2/3] Tagging release v%VERSION%...
git tag v%VERSION%
if errorlevel 1 (
    echo WARNING: Tag v%VERSION% may already exist — continuing.
)
git push origin v%VERSION%
if errorlevel 1 (
    echo WARNING: Could not push tag — it may already exist on remote.
)

REM ── Create GitHub release and upload installer ────────────────────────
echo.
echo [3/3] Creating GitHub release and uploading installer...
gh release create v%VERSION% "%INSTALLER%" ^
    --title "v%VERSION%" ^
    --notes "%NOTES%"

if errorlevel 1 (
    echo.
    echo ERROR: GitHub release failed. Check that you are authenticated:
    echo   gh auth login
    exit /b 1
)

REM ── Save version for next run ─────────────────────────────────────────
echo %VERSION%>%VERSION_FILE%

echo.
echo ══════════════════════════════════════════════════════
echo  Release v%VERSION% published!
echo  https://github.com/PatFox/RehearsalRoom/releases/tag/v%VERSION%
echo ══════════════════════════════════════════════════════
endlocal
