@echo off
REM Runs the whole test suite. Temp files stay on F: (never C:).
set "TEMP=%~dp0.tmp"
set "TMP=%~dp0.tmp"
if not exist "%~dp0.tmp" mkdir "%~dp0.tmp"
python -m unittest discover -s "%~dp0tests" %*
