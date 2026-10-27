@echo off
cd /d "%~dp0"
echo Starting SLR Paper Fetcher...
if not exist node_modules\ goto install_frontend
goto build_frontend
:install_frontend
call npm ci
if errorlevel 1 goto failed
:build_frontend
call npm run build
if errorlevel 1 goto failed
python -m pip install -r requirements.txt
if errorlevel 1 goto failed
python -m playwright install chromium
if errorlevel 1 goto failed
python app.py
goto end
:failed
echo Installation failed. Read the error above before starting again.
:end
pause
