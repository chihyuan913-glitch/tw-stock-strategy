@echo off
chcp 65001 >nul
title 台股盤後總體宏觀與信用交易解析 (Market Analyst)
cd /d "%~dp0"

echo ============================================================
echo   🏛️ 台股盤後總體宏觀與信用交易全戰報分析
echo   功能：信用交易、三大法人、維持率、大盤未來走向研判
echo ============================================================
echo.

python market_analyst.py --line %*

echo.
echo 分析執行完畢，按任意鍵關閉視窗...
pause >nul
