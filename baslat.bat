@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  call .venv\Scripts\python -m pip install -r requirements.txt
)
call .venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
