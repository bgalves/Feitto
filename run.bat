@echo off
rem Feitto - launch without a console window; data is kept in .\data
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw feitto.py & exit /b)
where pyw >nul 2>nul && (start "" pyw feitto.py & exit /b)
echo Python was not found on PATH. Install Python 3.7+ (with tkinter) or use the .exe build.
pause
