@echo off
setlocal
REM MTG Score Tracker - Build Script
REM Usage: source\build\build.bat
REM Requirements: pip install pyinstaller

cd /d "%~dp0.."

set DOWNLOAD_DIR=..\app-download
set RELEASE_DIR=%DOWNLOAD_DIR%\MTG-Score-Tracker
set EXISTING_DATA_DIR=%RELEASE_DIR%\data
set DATA_BACKUP_DIR=build\release_data_backup

echo === MTG Score Tracker Build ===

REM Step 0: Preserve packaged user data
if exist "%DATA_BACKUP_DIR%" rmdir /s /q "%DATA_BACKUP_DIR%"
if exist "%EXISTING_DATA_DIR%" (
    echo [0/4] Backing up saved leagues and config...
    mkdir "%DATA_BACKUP_DIR%"
    xcopy "%EXISTING_DATA_DIR%" "%DATA_BACKUP_DIR%\data" /E /I /Y >nul
    if %ERRORLEVEL% GEQ 4 (
        echo ERROR: Failed to back up existing packaged data.
        exit /b 1
    )
)

REM Step 1: Clean previous build
if exist build\dist rmdir /s /q build\dist
if exist build\work rmdir /s /q build\work
if exist "%RELEASE_DIR%" rmdir /s /q "%RELEASE_DIR%"
mkdir "%DOWNLOAD_DIR%" 2>nul

REM Step 2: PyInstaller bundle
echo [1/4] Building executable with PyInstaller...
pyinstaller --distpath build\dist --workpath build\work build\MTGScoreTracker.spec
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: PyInstaller failed.
    exit /b 1
)

REM Step 3: Create user-friendly portable release layout
echo [2/4] Preparing portable release folder...
mkdir "%RELEASE_DIR%" 2>nul
xcopy /E /I /Y "build\dist\MTGScoreTracker\*" "%RELEASE_DIR%\" >nul
mkdir "%RELEASE_DIR%\data\leagues" 2>nul
mkdir "%RELEASE_DIR%\data\global" 2>nul
mkdir "%RELEASE_DIR%\data\config" 2>nul

if exist "data\global\history.md" copy /Y "data\global\history.md" "%RELEASE_DIR%\data\global\history.md" >nul
if exist "data\global\stats.json" copy /Y "data\global\stats.json" "%RELEASE_DIR%\data\global\stats.json" >nul
if exist "data\config\decks.json" copy /Y "data\config\decks.json" "%RELEASE_DIR%\data\config\decks.json" >nul
if exist "data\config\app_settings.json" copy /Y "data\config\app_settings.json" "%RELEASE_DIR%\data\config\app_settings.json" >nul
if exist "data\config\starter_decks_by_format.json" copy /Y "data\config\starter_decks_by_format.json" "%RELEASE_DIR%\data\config\starter_decks_by_format.json" >nul
if exist "data\config\starter_decks_meta.json" copy /Y "data\config\starter_decks_meta.json" "%RELEASE_DIR%\data\config\starter_decks_meta.json" >nul

if not exist "%RELEASE_DIR%\data\global\history.md" echo # League History> "%RELEASE_DIR%\data\global\history.md"
if not exist "%RELEASE_DIR%\data\global\stats.json" echo {}> "%RELEASE_DIR%\data\global\stats.json"
if not exist "%RELEASE_DIR%\data\config\decks.json" echo {}> "%RELEASE_DIR%\data\config\decks.json"
if not exist "%RELEASE_DIR%\data\config\app_settings.json" echo {}> "%RELEASE_DIR%\data\config\app_settings.json"

if exist "%DATA_BACKUP_DIR%\data" (
    echo [2b/4] Restoring saved leagues and config...
    xcopy "%DATA_BACKUP_DIR%\data" "%RELEASE_DIR%\data" /E /I /Y >nul
    if %ERRORLEVEL% GEQ 4 (
        echo ERROR: Failed to restore saved packaged data.
        exit /b 1
    )
    rmdir /s /q "%DATA_BACKUP_DIR%"
)

echo MTG Score Tracker portable package > "%RELEASE_DIR%\START_HERE.txt"
echo Run MTGScoreTracker.exe >> "%RELEASE_DIR%\START_HERE.txt"
echo Data is stored in the data\ folder next to the exe. >> "%RELEASE_DIR%\START_HERE.txt"

REM Step 4: Inno Setup installer
echo [3/4] Building installer with Inno Setup...
set INNO=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
if not exist "%INNO%" (
    echo WARNING: Inno Setup not found at default path.
    echo          Install from https://jrsoftware.org/isinfo.php
    echo          or update INNO path in this script.
    goto :skip_inno
)
"%INNO%" build\installer.iss
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Inno Setup failed.
    exit /b 1
)
:skip_inno

echo [4/4] Done!
echo.
echo Output:
echo   Portable (easy):  %RELEASE_DIR%\MTGScoreTracker.exe
echo   Portable (raw):   build\dist\MTGScoreTracker\MTGScoreTracker.exe
echo   Installer: %DOWNLOAD_DIR%\MTGScoreTracker_v1.0.0_Setup.exe
