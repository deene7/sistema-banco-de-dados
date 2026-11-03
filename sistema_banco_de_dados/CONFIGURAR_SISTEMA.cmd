@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0sistema\configurar.py" --janela
pause
