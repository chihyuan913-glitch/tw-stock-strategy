# =====================================================================
# 一鍵建立 Windows 工作排程器：台股盤中即時雷達自動監控
# 觸發時段：每週一至週五 早上 08:55 自動啟動，13:35 收盤自動休眠
# =====================================================================

$TaskName = "TWStock_Momentum_Intraday_Scanner"
$ScriptPath = "g:\我的雲端硬碟\台股選股策略\start_momentum_intraday.bat"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " 正在設定 Windows 工作排程器：台股法人籌碼起漲即時雷達" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 檢查檔案是否存在
if (-not (Test-Path $ScriptPath)) {
    Write-Host "[X] 找不到批次檔: $ScriptPath" -ForegroundColor Red
    exit 1
}

# 動作：啟動批次檔
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$ScriptPath`"" -WorkingDirectory "g:\我的雲端硬碟\台股選股策略"

# 觸發器：週一至週五 08:55
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 08:55AM

# 設定選項：允許按需執行、錯過補跑、最長執行時間 5 小時 (到 14:00 自動收尾)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 5)

# 註冊工作排程
try {
    # 若已存在先取消註冊
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "台股法人籌碼集中起漲盤中即時雷達自動監控 (09:00~13:35 自動推播 LINE)" | Out-Null
    Write-Host "`n[✓] 成功建立 Windows 排程工作：$TaskName" -ForegroundColor Green
    Write-Host "    • 執行頻率：週一至週五 早上 08:55 自動啟動" -ForegroundColor Yellow
    Write-Host "    • 監控邏輯：成交量>1000張、站上20MA且月線上揚、正乖離<8%、無長上影線" -ForegroundColor Yellow
    Write-Host "    • 訊號推播：觸發即時發送至您的 LINE" -ForegroundColor Yellow
    Write-Host "    • 結束時間：13:35 盤後自動停止`n" -ForegroundColor Yellow
} catch {
    Write-Host "[!] 建立排程失敗，可能需要以系統管理員身分執行: $_" -ForegroundColor Red
}
