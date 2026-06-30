@echo off
setlocal enabledelayedexpansion
REM ======================================================================
REM  Rehearsal Room - release script
REM
REM  Stamps the version, commits it, tags + pushes, and creates the GitHub
REM  release. The Windows / macOS / Linux installers are built by GitHub
REM  Actions on the tag push and attached to the release automatically — no
REM  local build is performed here.
REM
REM  Usage:
REM    release.bat                        auto-increment patch, prompt for notes
REM    release.bat 1.2.0                  specific version,   prompt for notes
REM    release.bat "my release notes"     auto-increment patch, notes supplied
REM    release.bat 1.2.0 "release notes"  specific version,   notes supplied
REM ======================================================================

set VERSION_FILE=.version

REM -- Detect arguments --------------------------------------------------
REM If the first arg starts with a digit (0-9) treat it as a version number;
REM otherwise treat it as release notes.
set ARG1=%~1
set ARG2=%~2
set VERSION=
set NOTES=

if not "!ARG1!"=="" (
    set _FC=!ARG1:~0,1!
    if "!_FC!" GEQ "0" if "!_FC!" LEQ "9" (
        set VERSION=!ARG1!
        set NOTES=!ARG2!
    ) else (
        set NOTES=!ARG1!
    )
)

REM -- Load previous version (outside any block so %% expansion is reliable)
set PREV_VERSION=0.0.0
if exist %VERSION_FILE% set /p PREV_VERSION=<%VERSION_FILE%

REM -- Auto-increment patch if no explicit version given -----------------
if "!VERSION!"=="" (
    powershell -NoProfile -Command "$v='!PREV_VERSION!'.Split('.'); $v[2]=[int]$v[2]+1; $v -join '.'" > "%TEMP%\rr_ver.tmp" 2>nul
    set /p VERSION=<"%TEMP%\rr_ver.tmp"
    del "%TEMP%\rr_ver.tmp" >nul 2>&1
    echo No version supplied - auto-incremented to v!VERSION! ^(was v!PREV_VERSION!^)
)

REM -- Prompt for notes if not supplied ---------------------------------
if "!NOTES!"=="" (
    echo.
    echo Enter release notes ^(single line, press Enter when done^):
    set /p NOTES="Notes: "
)
if "!NOTES!"=="" set NOTES=Release v!VERSION!

REM -- Confirm -----------------------------------------------------------
echo.
echo ======================================================
echo   Version : v!VERSION!
echo   Notes   : !NOTES!
echo.
echo   Installers will be built by GitHub Actions on push.
echo ======================================================
echo.
set /p CONFIRM="Proceed? (Y/N): "
if /i not "!CONFIRM!"=="Y" (
    echo Cancelled.
    exit /b 0
)

REM -- Check gh CLI ------------------------------------------------------
gh --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: GitHub CLI ^(gh^) is not installed or not on PATH.
    echo Install from: https://cli.github.com   then run: gh auth login
    exit /b 1
)

REM -- Stamp version into core\version.py -------------------------------
echo.
echo Stamping version v!VERSION! into core\version.py...
python -c "import re; v='!VERSION!'; c=open('core/version.py').read(); c=re.sub(r'__version__\s*=\s*.*', '__version__ = ' + chr(34) + v + chr(34), c); open('core/version.py', 'w').write(c)"

REM -- Commit the version bump and push so the tagged commit carries it --
echo.
echo [1/3] Committing version bump...
git add core\version.py
git commit -m "Release v!VERSION!" >nul 2>&1
if errorlevel 1 echo   ^(core\version.py already at v!VERSION! - nothing to commit^)
git push
if errorlevel 1 (
    echo.
    echo ERROR: could not push the version commit. Resolve and re-run.
    exit /b 1
)

REM -- Tag and push (this triggers the build workflows) -----------------
echo.
echo [2/3] Tagging release v!VERSION!...
git tag v!VERSION!
if errorlevel 1 (
    echo WARNING: Tag v!VERSION! may already exist - continuing.
)
git push origin v!VERSION!
if errorlevel 1 (
    echo WARNING: Could not push tag - it may already exist on remote.
)

REM -- Create the GitHub release (CI attaches the installers) ------------
echo.
echo [3/3] Creating GitHub release v!VERSION!...
gh release create v!VERSION! --title "v!VERSION!" --notes "!NOTES!"
if errorlevel 1 (
    echo.
    echo ERROR: GitHub release failed. Check authentication: gh auth login
    exit /b 1
)

REM -- Save version for next run -----------------------------------------
(echo !VERSION!)>%VERSION_FILE%

echo.
echo ======================================================
echo  Release v!VERSION! created.
echo  GitHub Actions is now building the Windows, macOS and
echo  Linux installers and will attach them to the release:
echo  https://github.com/PatFox/RehearsalRoom/releases/tag/v!VERSION!
echo ======================================================
endlocal
