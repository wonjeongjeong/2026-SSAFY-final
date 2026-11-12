@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\background.ps1" stop
pause
