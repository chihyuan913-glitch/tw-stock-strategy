@echo off
chcp 65001 >nul
title 【集團作帳與母子聯動】盤中即時到價雷達
cd /d "%~dp0"

set "PY_BIN=python"
if exist "..\..\runtime\python.exe" (
    set "PY_BIN=..\..\runtime\python.exe"
)

echo =====================================================================
echo   台股【集團作帳與母子聯動伏擊雷達】啟動中...
echo   監控時段：週一至週五 09:00 ~ 13:35
echo   雙向狀態：🟢【作帳點火・母子共振】 / 🔴【集團結帳・順勢放空】
echo   到價防線：🟢進場、🔵加碼、🔴停利TP1/TP2、🛑嚴格停損
echo   到價時將定向推播至【台股】08-集團作帳 專屬 LINE 群組！
echo =====================================================================
echo.

"%PY_BIN%" intraday_scanner.py --interval 60

echo.
echo 監控已結束，按任意鍵關閉視窗...
pause >nul
