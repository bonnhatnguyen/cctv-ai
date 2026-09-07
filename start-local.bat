@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHON_EXE=python"
set "PNPM_EXE=pnpm"

where py >nul 2>nul && set "PYTHON_EXE=py -3.12"
if not exist "%ROOT%backend\.venv\Scripts\python.exe" goto runtime_check
set "PYTHON_EXE=%ROOT%backend\.venv\Scripts\python.exe"
goto start_services

:runtime_check
if exist "C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "PYTHON_EXE=C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" set "PNPM_EXE=C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"

:start_services
echo Starting transaction review API at http://127.0.0.1:8000 ...
start "Transaction API" /D "%ROOT%backend" cmd /k "\"%PYTHON_EXE%\" -m uvicorn app.api:app --host 127.0.0.1 --port 8000"
echo Starting review web app at http://127.0.0.1:5173 ...
start "Transaction Review UI" /D "%ROOT%frontend" cmd /k "\"%PNPM_EXE%\" dev --host 127.0.0.1"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5173"
endlocal
