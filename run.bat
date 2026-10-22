@echo off
rem Lance le site musique en local : http://127.0.0.1:8070
cd /d "%~dp0"
start "" http://127.0.0.1:8070
venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8070
