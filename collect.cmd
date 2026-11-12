@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul && (py -3 -u collector.py) || (python -u collector.py)
pause
