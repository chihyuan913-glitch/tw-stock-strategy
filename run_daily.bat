@echo off
chcp 65001 >nul
echo ======================================================== >> "g:\我的雲端硬碟\台股選股策略\daily_run.log"
echo [開始執行] %date% %time% >> "g:\我的雲端硬碟\台股選股策略\daily_run.log"

cd /d "g:\我的雲端硬碟\台股選股策略"

"C:\Users\User.DESKTOP-OI789R1\AppData\Local\Programs\Python\Python313\python.exe" screener.py --line --export result.csv >> "g:\我的雲端硬碟\台股選股策略\daily_run.log" 2>&1

echo [完成執行] %date% %time% >> "g:\我的雲端硬碟\台股選股策略\daily_run.log"
echo ======================================================== >> "g:\我的雲端硬碟\台股選股策略\daily_run.log"
