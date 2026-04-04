@echo off
setlocal EnableExtensions EnableDelayedExpansion
title GitHub Bootstrap Push

echo ============================================
echo   GitHub bootstrap / init / first push
echo ============================================
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Git is not installed or not in PATH.
    echo Install Git for Windows first, then run this file again.
    pause
    exit /b 1
)

echo Current folder:
cd
echo.

choice /M "Use CURRENT folder as repo root"
if errorlevel 2 (
    echo.
    set /p REPO_DIR=Enter full folder path: 
    if not exist "!REPO_DIR!" (
        echo [ERROR] Folder does not exist: !REPO_DIR!
        pause
        exit /b 1
    )
    cd /d "!REPO_DIR!"
)

echo.
echo Working in:
cd
echo.

if not exist ".gitignore" (
    echo Creating .gitignore ...
    > .gitignore echo __pycache__/
    >> .gitignore echo *.pyc
    >> .gitignore echo TEST/
    >> .gitignore echo pipeline_log.txt
    >> .gitignore echo .env
    >> .gitignore echo .venv/
    >> .gitignore echo venv/
) else (
    echo .gitignore already exists - leaving it as-is.
)

if not exist "README.md" (
    echo Creating README.md ...
    > README.md echo # Project
)

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
    echo.
    echo Initializing git repo ...
    git init
    if errorlevel 1 (
        echo [ERROR] git init failed.
        pause
        exit /b 1
    )
) else (
    echo Git repo already initialized.
)

for /f "delims=" %%A in ('git config --get user.name 2^>nul') do set GIT_NAME=%%A
for /f "delims=" %%A in ('git config --get user.email 2^>nul') do set GIT_EMAIL=%%A

if not defined GIT_NAME (
    echo.
    set /p GIT_NAME=Enter git user.name: 
    git config user.name "!GIT_NAME!"
)

if not defined GIT_EMAIL (
    echo.
    set /p GIT_EMAIL=Enter git user.email: 
    git config user.email "!GIT_EMAIL!"
)

echo.
echo Git identity:
echo   Name : !GIT_NAME!
echo   Email: !GIT_EMAIL!
echo.

git add .
git diff --cached --quiet
if errorlevel 1 (
    echo Creating commit ...
    git commit -m "pipeline working baseline"
    if errorlevel 1 (
        echo [ERROR] git commit failed.
        pause
        exit /b 1
    )
) else (
    echo Nothing new to commit right now.
)

echo.
set /p REMOTE_URL=Paste GitHub repo URL (example: https://github.com/USER/REPO.git): 
if not defined REMOTE_URL (
    echo [ERROR] No repo URL provided.
    pause
    exit /b 1
)

git remote get-url origin >nul 2>nul
if errorlevel 1 (
    git remote add origin "!REMOTE_URL!"
) else (
    echo Remote origin already exists. Updating it...
    git remote set-url origin "!REMOTE_URL!"
)

git branch -M main

echo.
echo ============================================
echo About authentication:
echo GitHub no longer accepts normal password push over HTTPS.
echo Use one of these:
echo 1) Git Credential Manager / browser login
echo 2) Personal Access Token (PAT) when prompted
echo ============================================
echo.

choice /C YN /M "Try push now"
if errorlevel 2 goto :afterpush

git push -u origin main
if errorlevel 1 (
    echo.
    echo Push failed.
    echo If GitHub asks for password, use a PAT instead of your normal password.
    echo Or install/use Git Credential Manager and sign in through browser.
    echo.
    pause
    exit /b 1
)

echo.
echo Push complete.

:afterpush
choice /C YN /M "Create tag v1.0-working and push tag"
if errorlevel 2 goto :done

git tag v1.0-working >nul 2>nul
git push origin v1.0-working

:done
echo.
echo Finished.
pause
endlocal
