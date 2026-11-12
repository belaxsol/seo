@echo off
cd /d "%~dp0"
echo Installation (la premiere fois : telechargement du navigateur, quelques minutes)...
python -m pip install -q -r requirements.txt
python -m playwright install chromium
start "" http://127.0.0.1:5000
python app.py
pause
