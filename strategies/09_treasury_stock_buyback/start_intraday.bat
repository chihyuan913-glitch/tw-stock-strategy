@echo off
chcp 65001 >nul
title 策略 09：庫藏股宣告護盤到價雷達
cd /d "%~dp0"

set "PY_BIN=python"
if exist "..\..\runtime\python.exe" (
    set "PY_BIN=..\..\runtime\python.exe"
)

echo =====================================================================
echo   台股策略 09：庫藏股宣告護盤與低估值事件到價雷達監控中...
echo   監控時段：週一至週五 09:00 ~ 13:35
echo   防毛刺機制：GLOBAL_TICK_GUARD 連讀複檢啟動
echo   到價事件：🟢買進上限、🔵續強加碼、🔴停利TP1/TP2、🛑嚴格停損
echo   到價訊號定向推播至【LINE_TARGET_STRATEGY_09】專屬頻道
echo =====================================================================
echo.

"%PY_BIN%" intraday_scanner.py --interval 60

echo.
echo 監控已結束，按任意鍵關閉視窗...
pause >nul
