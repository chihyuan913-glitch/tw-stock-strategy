# =====================================================================
# 一鍵建立 Windows 工作排程器：台股選股策略最高總指揮排程
# 1. 每日夜間官方籌碼全沉澱終極選股 (週一至週五 21:15 執行 run_nightly_final.bat)
# 2. 盤中全策略即時到價雷達 (週一至週五 08:55 啟動 run_all_intraday.py)
# 3. 每週日全策略動態優化與再升級 (每週日 20:00 執行 optimize_all.py)
# =====================================================================

$WorkingDir = "g:\我的雲端硬碟\台股選股策略"
$NightlyRunner = "$WorkingDir\run_nightly_final.bat"
$IntradayScript = "$WorkingDir\run_all_intraday.py"
$WeeklyOptimizer = "$WorkingDir\optimize_all.py"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " 正在設定 Windows 工作排程器：台股量化選股策略總指揮排程" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 清理舊版 17:35 盤後排程與歷史個別單一策略排程（全面移轉至 21:15 官方籌碼全沉澱終極版）
$LegacyTasks = @("TWStock_Daily_Master_Runner", "TWStock_BB_Intraday_Radar", "TWStock_BB_Reversal_Daily")
foreach ($t in $LegacyTasks) {
    try {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "[*] 已清理歷史舊排程：$t" -ForegroundColor DarkGray
    } catch { }
}

# 任務一：每日夜間官方籌碼全沉澱終極版 (21:15)
$TaskNightly = "TWStock_Nightly_Final_Master_Runner"
if (Test-Path $NightlyRunner) {
    $ActionNightly = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$NightlyRunner`" --no-pause" -WorkingDirectory $WorkingDir
    $TriggerNightly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 09:15PM
    $SettingsNightly = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

    try {
        Unregister-ScheduledTask -TaskName $TaskNightly -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskNightly -Action $ActionNightly -Trigger $TriggerNightly -Settings $SettingsNightly -Description "台股每日夜間官方籌碼全沉澱終極版選股與明日買賣決策表" | Out-Null
        Write-Host "[✓] 成功註冊夜間終極版排程工作：$TaskNightly (每週一至週五 21:15 執行)" -ForegroundColor Green
    } catch {
        Write-Host "[!] 註冊 $TaskNightly 失敗: $_" -ForegroundColor Red
    }
}

# 任務二：盤中全策略即時到價雷達 (08:55 ~ 13:35)
$TaskIntraday = "TWStock_All_Intraday_Radar"
if (Test-Path $IntradayScript) {
    $ActionIntraday = New-ScheduledTaskAction -Execute "python.exe" -Argument "`"$IntradayScript`" --interval 60" -WorkingDirectory $WorkingDir
    $TriggerIntraday = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 08:55AM
    $SettingsIntraday = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 5)

    try {
        Unregister-ScheduledTask -TaskName $TaskIntraday -Confirm:$false -ErrorAction SilentlyContinue
        # 清除舊版個別策略工作
        Unregister-ScheduledTask -TaskName "TWStock_Momentum_Intraday_Scanner" -Confirm:$false -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName "TWStock_CB_Intraday_Scanner" -Confirm:$false -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName "TWStock_CB_Pricing_Ambush_Daily" -Confirm:$false -ErrorAction SilentlyContinue

        Register-ScheduledTask -TaskName $TaskIntraday -Action $ActionIntraday -Trigger $TriggerIntraday -Settings $SettingsIntraday -Description "台股全策略盤中到價即時雷達平行監控" | Out-Null
        Write-Host "[✓] 成功註冊盤中排程工作：$TaskIntraday (每週一至週五 08:55 啟動)" -ForegroundColor Green
    } catch {
        Write-Host "[!] 註冊 $TaskIntraday 失敗: $_" -ForegroundColor Red
    }
}

# 任務三：每週日全策略動態優化與再升級 (週日 20:00)
$TaskWeekly = "TWStock_Weekly_Strategy_Optimizer"
if (Test-Path $WeeklyOptimizer) {
    $ActionWeekly = New-ScheduledTaskAction -Execute "python.exe" -Argument "`"$WeeklyOptimizer`" --line" -WorkingDirectory $WorkingDir
    $TriggerWeekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 08:00PM
    $SettingsWeekly = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

    try {
        Unregister-ScheduledTask -TaskName $TaskWeekly -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskWeekly -Action $ActionWeekly -Trigger $TriggerWeekly -Settings $SettingsWeekly -Description "台股每週日全策略動態優化與再升級總引擎" | Out-Null
        Write-Host "[✓] 成功註冊週日排程工作：$TaskWeekly (每週日 20:00 執行)" -ForegroundColor Green
    } catch {
        Write-Host "[!] 註冊 $TaskWeekly 失敗: $_" -ForegroundColor Red
    }
}

Write-Host "`n[完成] 排程設定已全面升級為三大精準排程（已全面清理 17:35 與舊排程）！" -ForegroundColor Cyan
