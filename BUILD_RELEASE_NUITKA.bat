@echo off
setlocal
cd /d "%~dp0"
title KidsChamp - Version 1 Release Build

echo ============================================================
echo   KidsChamp Version 1 - Nuitka Release Build
echo ============================================================
echo.

py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.12 x64 is not installed.
    echo Install Python 3.12 x64 on this BUILD PC, then run this file again.
    echo Your existing Python 3.14 can stay installed side-by-side.
    echo.
    pause
    exit /b 1
)

echo [1/6] Creating isolated build environment...
if exist ".buildvenv" rmdir /s /q ".buildvenv"
py -3.12 -m venv .buildvenv
if errorlevel 1 goto :fail
call ".buildvenv\Scripts\activate.bat"

echo [2/6] Installing the tested pip version...
python -m pip install pip==26.2.1
if errorlevel 1 goto :fail

echo [3/6] Installing Kids Champ dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [4/6] Installing tested build tools...
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :fail

echo [5/6] Cleaning previous release build...
if exist "build_nuitka" rmdir /s /q "build_nuitka"
mkdir "build_nuitka"

echo [6/6] Compiling Windows standalone application...
echo First build can take a while because Nuitka may download its supported MinGW64 toolchain.
python -m nuitka ^
  --mode=standalone ^
  --enable-plugin=pyside6 ^
  --windows-console-mode=disable ^
  --mingw64 ^
  --assume-yes-for-downloads ^
  --output-dir=build_nuitka ^
  --output-filename=KidsChamp.exe ^
  --company-name="Rusala Media" ^
  --product-name="KidsChamp" ^
  --file-description="KidsChamp Version 1" ^
  --file-version=1.1.0.0 ^
  --product-version=1.1.0.0 ^
  --windows-icon-from-ico=assets\kidsChamp.ico ^
  --include-data-dir=assets=assets ^
  --include-data-dir=templates=templates ^
  --include-data-dir=presets=presets ^
  --include-data-files=app_data/settings.json=app_data/settings.json ^
  main.py
if errorlevel 1 goto :fail

if not exist "build_nuitka\main.dist\KidsChamp.exe" (
    echo.
    echo [ERROR] Build finished but KidsChamp.exe was not found at:
    echo build_nuitka\main.dist\KidsChamp.exe
    echo Check the Nuitka messages above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo BUILD SUCCESS

echo Test this file BEFORE creating the installer:
echo   build_nuitka\main.dist\KidsChamp.exe

echo The whole main.dist folder is the compiled application.
echo No project .py source files need to be sent to the admin.
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo ============================================================
echo BUILD FAILED - read the error above.
echo ============================================================
pause
exit /b 1
