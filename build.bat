@echo off
REM ── Rehearsal Room — release build script ──────────────────────────────
REM Run from the project root:  build.bat
REM Optionally pass a version:  build.bat 1.2.0

setlocal
set VERSION=%~1
if "%VERSION%"=="" set VERSION=1.0.0

echo.
echo ══════════════════════════════════════════════════════
echo  Rehearsal Room build  v%VERSION%
echo ══════════════════════════════════════════════════════
echo.

REM ── 1. PyInstaller ────────────────────────────────────────────────────
echo [1/3] Checking PyInstaller...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller
)

echo [2/3] Cleaning previous build...
if exist build\RehearsalRoom rmdir /s /q build\RehearsalRoom
if exist dist\RehearsalRoom  rmdir /s /q dist\RehearsalRoom

echo [3/3] Building executable...
python -m PyInstaller rehearsalroom.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED - PyInstaller error. See output above.
    exit /b 1
)

echo.
echo  Executable ready: dist\RehearsalRoom\RehearsalRoom.exe

REM ── 2. Inno Setup installer (optional) ────────────────────────────────
echo.
echo Checking for Inno Setup...

set ISCC=""
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe"      set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"

if %ISCC%=="" (
    echo Inno Setup not found - skipping installer build.
    echo Download from: https://jrsoftware.org/isdl.php
    goto :done
)

echo Building installer...
if not exist installer\output mkdir installer\output

%ISCC% /Q "/DAppVersion=%VERSION%" installer\rehearsalroom.iss
if errorlevel 1 (
    echo.
    echo INSTALLER BUILD FAILED. See output above.
    exit /b 1
)

echo  Installer ready: installer\output\RehearsalRoom-v%VERSION%-Setup.exe

:done
echo.
echo ══════════════════════════════════════════════════════
echo  Done!  v%VERSION%
echo ══════════════════════════════════════════════════════
endlocal
