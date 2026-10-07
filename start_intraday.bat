@echo off
chcp 65001 >nul
title 台股盤中即時雷達 (布林下軌+法人買超)
cd /d "g:\我的雲端硬碟\台股選股策略"

echo =======================================================
echo   台股盤中即時雷達 - 啟動中
echo   時段：09:00 ~ 13:30 (每 3 分鐘掃描一次)
echo   條件：即時成交量 > 1000 張 且 觸及布林下軌 (-5%% ~ +1%%)
echo   觸發時將自動推播即時快訊至您的 LINE
echo =======================================================

"C:\Users\User.DESKTOP-OI789R1\AppData\Local\Programs\Python\Python313\python.exe" intraday_scanner.py

pause
