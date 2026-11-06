@echo off
chcp 65001 >nul
echo [08:20] 正在啟動 策略 12：全台 ETF 跨市場折溢價事件雷達 (盤前早鳥快篩)...
python "%~dp0screener.py" --line
echo.
echo 執行完畢，按任意鍵退出...
pause >nul
