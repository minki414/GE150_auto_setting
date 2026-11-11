@echo off
rem GE150 preset loader - build one-file exe (ASCII only)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo .venv not found. Create it first: py -3.13 -m venv .venv
  exit /b 1
)
.venv\Scripts\python.exe tools\build_exe.py
if errorlevel 1 exit /b 1
echo Built: dist\GE150PresetLoader.exe
