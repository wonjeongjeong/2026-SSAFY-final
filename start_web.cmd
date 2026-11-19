@echo off
cd /d "%~dp0"
python services\web\server.py --port 5173
