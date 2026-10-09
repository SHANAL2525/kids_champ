# Kids Champ Windows Release Packaging

This folder is prepared to create a Windows build without distributing the Python source project to the office admin.

## What the admin finally receives

`KidsChamp_Setup_V4_9.exe`

The admin does **not** need Python, VS Code, pip, PySide6, Pillow, OpenCV or NumPy installed separately.

## Why this build uses Python 3.12 on the build PC

Your development Python can remain installed. The release build uses a separate Python 3.12 virtual environment because it is a conservative/stable choice for the Nuitka + Windows MinGW64 build path.

Install 64-bit Python 3.12 on the developer/build PC only. It can live side-by-side with Python 3.14.

## Step 1 - Build compiled application

Double-click:

`BUILD_RELEASE_NUITKA.bat`

It will:
1. Create `.buildvenv` using Python 3.12.
2. Install the project requirements inside that isolated build environment.
3. Install Nuitka.
4. Compile the app in standalone mode.
5. Include `assets`, `templates`, and `presets`.

Output:

`build_nuitka/main.dist/KidsChamp.exe`

## Step 2 - TEST before installer

Double-click:

`build_nuitka/main.dist/KidsChamp.exe`

Test at minimum:
- Generator opens.
- Upload ZIP works.
- Portrait/Landscape/Square templates load.
- Compare opens.
- Crop/Wrap/colour/presets work.
- Download All works.
- Close/reopen and check settings/templates.

Do not build the office installer until the compiled app passes this test.

## Step 3 - Install Inno Setup on the build PC

Install the current Inno Setup from its official website.

## Step 4 - Build setup EXE

Double-click:

`BUILD_INSTALLER.bat`

Output:

`installer_output/KidsChamp_Setup_V4_9.exe`

## Step 5 - Test on another Windows PC

Preferably test on a Windows x64 computer with **no Python installed**.

Run:

`KidsChamp_Setup_V4_9.exe`

Then launch the desktop shortcut and perform a small real batch test.

## Source protection note

The installer contains the Nuitka-compiled standalone application, not this Python source project folder. Do not give this build/source directory to the admin. Give only the final setup EXE.

No desktop application is impossible to reverse engineer, but this is substantially better than distributing the readable `.py` project source.

## IMPORTANT

This packaging kit does not mean the application itself is a final published release. Use it to test the compiled/installed form. Only label it final after the app workflow is fully approved.
