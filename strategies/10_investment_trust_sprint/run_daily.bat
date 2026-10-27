@echo off
chcp 65001 >nul
echo ========================================================
echo   台股策略 10：投信認養集中度爆發飆股策略 盤後選股器
echo ========================================================
cd /d "%~dp0"
python screener.py --export result.csv --line
pause
