@echo off
cd /d "%~dp0"
title KidsChamp Version 1
py -c "import PySide6, PIL, cv2, numpy" >nul 2>nul
if errorlevel 1 (
  echo First-time setup: installing required Python libraries...
  py -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Installation failed. Check internet connection and Python installation.
    pause
    exit /b 1
  )
)
py main.py
if errorlevel 1 pause
