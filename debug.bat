@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto python
py -3 app.py
goto done
:python
python app.py
:done
pause
