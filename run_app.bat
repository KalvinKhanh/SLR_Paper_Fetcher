@echo off
cd /d "%~dp0"
echo Starting SLR Paper Fetcher...
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
