@echo off
chcp 65001 >nul
title 【高融資斷頭與浮額清洗】盤中即時到價雷達
cd /d "%~dp0"

set "PY_BIN=python"
if exist "..\..\runtime\python.exe" (
    set "PY_BIN=..\..\runtime\python.exe"
)

echo =====================================================================
echo   台股【高融資使用率×低維持率斷頭伏擊與浮額清洗雷達】啟動中...
echo   監控時段：週一至週五 09:00 ~ 13:35
echo   雙向狀態：🔴【斷頭助跌・放空獵殺】 / 🟢【浮額清洗・逆勢抄底】
echo   到價防線：🟢進場、🔵加碼、🔴停利TP1/TP2、🛑嚴格停損
echo   到價時將定向推播至【台股】07-融資斷頭 專屬 LINE 群組！
echo =====================================================================
echo.

"%PY_BIN%" intraday_scanner.py --interval 60

echo.
echo 監控已結束，按任意鍵關閉視窗...
pause >nul
