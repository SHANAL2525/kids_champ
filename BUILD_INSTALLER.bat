@echo off
setlocal
cd /d "%~dp0"
title KidsChamp - Build Version 1 Windows Installer

echo ============================================================
echo   KidsChamp Version 1 - Installer Build

echo ============================================================
echo.

if not exist "build_pyinstaller_v1_1\KidsChamp\KidsChamp.exe" (
    echo [ERROR] Compiled app not found.
    echo Build and test build_pyinstaller_v1_1\KidsChamp\KidsChamp.exe first.
    echo.
    pause
    exit /b 1
)

set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files (x86)\Inno Setup 7\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "C:\Program Files\Inno Setup 7\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if not defined ISCC (
    echo [ERROR] Inno Setup was not found.
    echo Install Inno Setup from its official website, then run this file again.
    echo.
    pause
    exit /b 1
)

if not exist "installer_output" mkdir "installer_output"
if exist "installer_output\KidsChamp_Setup_V1_1.exe" del /q "installer_output\KidsChamp_Setup_V1_1.exe"

echo Compiling installer with:
echo %ISCC%
echo.
"%ISCC%" "installer\KidsChamp.iss"
if errorlevel 1 (
    echo.
    echo [ERROR] Installer build failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo INSTALLER SUCCESS

echo Give the admin ONLY the setup file in:
echo   installer_output\KidsChamp_Setup_V1_1.exe

echo Do NOT send your Python source project folder.
echo ============================================================
echo.
pause
