@echo off
setlocal

REM Initialize MTG-Score-Tracker as a git repository.
REM Usage: double-click this file or run it from cmd/powershell.

cd /d "%~dp0"

where git >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Git is not installed or not on PATH.
    echo Install Git from https://git-scm.com/download/win and run this file again.
    exit /b 1
)

if exist ".git" (
    echo Repository is already initialized in this folder.
) else (
    git init
    if %ERRORLEVEL% NEQ 0 (
        echo ERROR: git init failed.
        exit /b 1
    )
)

git add .
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: git add failed.
    exit /b 1
)

git commit -m "Initial commit"
if %ERRORLEVEL% NEQ 0 (
    echo NOTE: Initial commit was not created. Configure user.name/user.email if needed:
    echo   git config --global user.name "Your Name"
    echo   git config --global user.email "you@example.com"
    echo Then run: git commit -m "Initial commit"
    exit /b 0
)

echo Done. Repository initialized and initial commit created.
exit /b 0
