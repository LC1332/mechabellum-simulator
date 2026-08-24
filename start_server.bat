@echo off
cd /d %~dp0
python -m uvicorn server:app --app-dir web --port 8300
pause
