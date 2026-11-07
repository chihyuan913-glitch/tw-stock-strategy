@echo off
chcp 65001 >nul
title 全台股 268 檔 ETF 事件型交易即時監控雷達
echo ====================================================
echo  全台股 268 檔 ETF 跨市場事件交易雷達 (常駐監控模式)
echo  覆蓋: 海外跨境、台股市值、高股息、債券、主動型
echo ====================================================
cd /d "%~dp0"
python all_etf_event_radar.py --monitor --interval 60
pause
