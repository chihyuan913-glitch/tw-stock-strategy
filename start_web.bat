@echo off
chcp 65001 >nul
title 台股量化選股策略 最高總指揮中心戰情儀表板
cd /d "%~dp0"
python start_web.py
