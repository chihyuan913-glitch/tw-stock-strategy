@echo off
chcp 65001 >nul
title 08:20 盤前早鳥跨市場預警快篩
cd /d "%~dp0"
python all_etf_two_stage_radar.py --premarket
pause
