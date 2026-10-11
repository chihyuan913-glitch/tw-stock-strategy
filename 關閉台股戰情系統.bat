@echo off
chcp 65001 >nul
cd /d "%~dp0"
python stop_web.py
ping -n 3 127.0.0.1 >nul
