@echo off
rem Builds a single-file Feitto.exe (no console window) with PyInstaller.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name Feitto --icon feitto.ico --version-file version_info.txt feitto.py
echo.
echo Done: dist\Feitto.exe
pause
