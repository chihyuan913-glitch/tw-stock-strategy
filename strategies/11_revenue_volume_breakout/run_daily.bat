@echo off
chcp 65001 >nul
echo ========================================================
echo   台股策略 11：台股營收創高與量價突破成長動能 盤後選股器
echo ========================================================
cd /d "%~dp0"
python screener.py --export result.csv --line
pause
