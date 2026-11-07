@echo off
chcp 65001 >nul
title 全台股 ETF 盤前/單次事件快篩
cd /d "%~dp0"
python all_etf_event_radar.py --once
pause
