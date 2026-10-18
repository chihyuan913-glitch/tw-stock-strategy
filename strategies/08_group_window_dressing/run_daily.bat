@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY_BIN=python"
if exist "..\..\runtime\python.exe" (
    set "PY_BIN=..\..\runtime\python.exe"
)

set "LOG_FILE=%~dp0daily_run.log"
echo ======================================================== >> "%LOG_FILE%"
echo [開始執行] %date% %time% >> "%LOG_FILE%"

"%PY_BIN%" screener.py --line --export result.csv >> "%LOG_FILE%" 2>&1

echo [完成執行] %date% %time% >> "%LOG_FILE%"
echo ======================================================== >> "%LOG_FILE%"
