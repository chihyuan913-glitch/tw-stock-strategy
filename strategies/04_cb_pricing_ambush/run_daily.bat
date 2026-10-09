@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "LOG_FILE=%~dp0daily_run.log"
echo ======================================================== >> "%LOG_FILE%"
echo [開始執行 策略04：CB定價伏擊選股器] %date% %time% >> "%LOG_FILE%"

python screener.py --line --export result.csv >> "%LOG_FILE%" 2>&1

echo [完成執行 策略04] %date% %time% >> "%LOG_FILE%"
echo ======================================================== >> "%LOG_FILE%"
