@echo off
chcp 65001 >nul
title 【台股最高總指揮中心】全策略盤中到價即時雷達
cd /d "%~dp0"

echo =====================================================================
echo   台股量化選股策略最高總指揮中心 - 全策略盤中到價即時雷達啟動
echo   涵蓋策略：
echo     【01】布林通道下軌超跌反轉到價雷達
echo     【02】法人籌碼集中起漲動能到價雷達
echo     【03】弱勢破線做空與個股期貨避險到價雷達
echo     【04】可轉債 (CB) 定價伏擊到價雷達
echo   監控時段：週一至週五 09:00 ~ 13:35
echo   所有觸發事件將定向投遞至各自專屬之 LINE 視窗！
echo =====================================================================
echo.
echo [1] 啟動全策略即時輪詢監控 (每 60 秒巡邏)
echo [2] 執行單次全市場快篩巡邏
echo [3] 發送全策略模擬到價推播測試 (驗證 4 個 LINE 視窗)
echo.
set /p opt="請選擇執行模式 (預設 1): "

if "%opt%"=="2" (
    python run_all_intraday.py --once
) else if "%opt%"=="3" (
    python run_all_intraday.py --test-push
) else (
    python run_all_intraday.py --interval 60
)

echo.
echo 執行完畢，按任意鍵退出...
pause >nul
