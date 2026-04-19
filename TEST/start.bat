@echo off
cd /d "%~dp0"
py -3 app.py
if %errorlevel% neq 0 python app.py
pause
