@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    call setup_venv.bat
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m streamlit run app.py
exit /b %errorlevel%
