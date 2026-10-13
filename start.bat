@echo off
setlocal
cd /d "%~dp0"
where pyw >nul 2>nul
if errorlevel 1 goto pythonw
start "" pyw -3 "%~dp0app.py"
exit /b 0
:pythonw
where pythonw >nul 2>nul
if errorlevel 1 goto console
start "" pythonw "%~dp0app.py"
exit /b 0
:console
where py >nul 2>nul
if errorlevel 1 goto python
py -3 app.py
if errorlevel 1 goto failed
exit /b 0
:python
python app.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Setup or startup failed. Install Python 3.10+ from python.org, then try again.
echo See README.md for manual setup and troubleshooting.
pause
exit /b 1
