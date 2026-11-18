@echo off
rem Build HackCPUSim.exe -- a single-file, windowed Windows executable.
rem
rem   build_exe.bat            -> build only
rem   build_exe.bat --console  -> keep a console window (for debugging)
rem
rem Requires: pip install pyinstaller

setlocal
cd /d "%~dp0"

set EXTRA=
if "%1"=="--console" set EXTRA=--console

python -m PyInstaller ^
  --onefile ^
  --windowed ^
  --name HackCPUSim ^
  %EXTRA% ^
  main.py

echo.
echo Done. Executable: dist\HackCPUSim.exe
echo (No arguments: launches the GUI with the built-in demo.)
endlocal
