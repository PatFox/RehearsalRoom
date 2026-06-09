@echo off
REM ── Rehearsal Room — release build script ──────────────────────────────
REM Run from the project root:  build.bat

echo [1/3] Checking PyInstaller...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller
)

echo [2/3] Cleaning previous build...
if exist build\RehearsalRoom rmdir /s /q build\RehearsalRoom
if exist dist\RehearsalRoom  rmdir /s /q dist\RehearsalRoom

echo [3/3] Building...
python -m PyInstaller rehearsalroom.spec

if errorlevel 1 (
    echo.
    echo BUILD FAILED. See output above for details.
    exit /b 1
)

echo.
echo ══════════════════════════════════════════════════════
echo  Build complete:  dist\RehearsalRoom\RehearsalRoom.exe
echo ══════════════════════════════════════════════════════
