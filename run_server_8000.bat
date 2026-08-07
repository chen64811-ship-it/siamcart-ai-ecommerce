@echo off
rem SiamCart demo server launcher (port 8000) - demo stabilization pass
rem run.py server is a thin wrapper (DB init + uvicorn.run); direct uvicorn
rem is used because the run.py wrapper hangs under scheduled-task start on
rem this host. Same app, same DB init (startup event), same host/port.
cd /d T:\thai-ecommerce-agent
".venv\Scripts\python.exe" -m uvicorn app.api.server:app --host 0.0.0.0 --port 8000 >> logs\uvicorn_8000.log 2>&1
