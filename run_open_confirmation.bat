@echo off
chcp 65001 >nul
title 09:02 開盤實戰確認與精準進場價檢核
cd /d "%~dp0"
python all_etf_two_stage_radar.py --confirm
pause
