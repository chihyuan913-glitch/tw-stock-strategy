@echo off
chcp 65001 >nul
echo [09:02] 正在啟動 策略 12：全台 ETF 跨市場折溢價事件雷達 (開盤實戰確認與下單指令)...
python "%~dp0intraday_scanner.py" --confirm --line
echo.
echo 開盤確認完成，準備進入盤中到價輪詢...
python "%~dp0intraday_scanner.py" --interval 60 --line
pause >nul
