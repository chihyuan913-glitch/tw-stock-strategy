# =====================================================================
# 一鍵建立 Windows 工作排程器：台股選股策略自動排程
# 1. 每日盤後全策略自動巡邏 (週一至週五 17:30 執行 run_all.bat)
# 2. 盤中即時雷達自動監控 (週一至週五 08:55 啟動)
# =====================================================================

$WorkingDir = "g:\我的雲端硬碟\台股選股策略"
$DailyRunner = "$WorkingDir\run_all.bat"
$IntradayScanner = "$WorkingDir\strategies\02_institutional_momentum\start_intraday.bat"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " 正在設定 Windows 工作排程器：台股量化選股策略總指揮排程" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 任務一：每日盤後全策略自動巡邏 (17:35)
$TaskDaily = "TWStock_Daily_Master_Runner"
if (Test-Path $DailyRunner) {
    $ActionDaily = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$DailyRunner`"" -WorkingDirectory $WorkingDir
    $TriggerDaily = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 05:35PM
    $SettingsDaily = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

    try {
        Unregister-ScheduledTask -TaskName $TaskDaily -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskDaily -Action $ActionDaily -Trigger $TriggerDaily -Settings $SettingsDaily -Description "台股每日盤後全策略自動選股與 LINE 推播日報" | Out-Null
        Write-Host "[✓] 成功註冊盤後排程工作：$TaskDaily (每週一至週五 17:35 執行)" -ForegroundColor Green
    } catch {
        Write-Host "[!] 註冊 $TaskDaily 失敗: $_" -ForegroundColor Red
    }
}

# 任務二：盤中即時雷達 (08:55)
$TaskIntraday = "TWStock_Momentum_Intraday_Scanner"
if (Test-Path $IntradayScanner) {
    $ActionIntraday = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$IntradayScanner`"" -WorkingDirectory "$WorkingDir\strategies\02_institutional_momentum"
    $TriggerIntraday = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 08:55AM
    $SettingsIntraday = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 5)

    try {
        Unregister-ScheduledTask -TaskName $TaskIntraday -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskIntraday -Action $ActionIntraday -Trigger $TriggerIntraday -Settings $SettingsIntraday -Description "台股法人籌碼起漲盤中即時雷達自動監控" | Out-Null
        Write-Host "[✓] 成功註冊盤中排程工作：$TaskIntraday (每週一至週五 08:55 啟動)" -ForegroundColor Green
    } catch {
        Write-Host "[!] 註冊 $TaskIntraday 失敗: $_" -ForegroundColor Red
    }
}

# 任務三：可轉債定價伏擊獨立盤後排程 (17:45)
$TaskCB = "TWStock_CB_Pricing_Ambush_Daily"
$CBRunner = "$WorkingDir\strategies\04_cb_pricing_ambush\run_daily.bat"
if (Test-Path $CBRunner) {
    $ActionCB = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$CBRunner`"" -WorkingDirectory "$WorkingDir\strategies\04_cb_pricing_ambush"
    $TriggerCB = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 05:45PM
    $SettingsCB = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

    try {
        Unregister-ScheduledTask -TaskName $TaskCB -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskCB -Action $ActionCB -Trigger $TriggerCB -Settings $SettingsCB -Description "台股可轉債 (CB) 定價伏擊與區間博弈獨立選股與專屬 LINE 推播" | Out-Null
        Write-Host "[✓] 成功註冊策略04獨立排程工作：$TaskCB (每週一至週五 17:45 執行)" -ForegroundColor Green
    } catch {
        Write-Host "[!] 註冊 $TaskCB 失敗: $_" -ForegroundColor Red
    }
}

# 任務四：可轉債盤中到價即時雷達 (08:55)
$TaskCBRadar = "TWStock_CB_Intraday_Scanner"
$CBRadarRunner = "$WorkingDir\strategies\04_cb_pricing_ambush\start_intraday.bat"
if (Test-Path $CBRadarRunner) {
    $ActionCBRadar = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$CBRadarRunner`"" -WorkingDirectory "$WorkingDir\strategies\04_cb_pricing_ambush"
    $TriggerCBRadar = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 08:55AM
    $SettingsCBRadar = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 5)

    try {
        Unregister-ScheduledTask -TaskName $TaskCBRadar -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskCBRadar -Action $ActionCBRadar -Trigger $TriggerCBRadar -Settings $SettingsCBRadar -Description "台股可轉債 (CB) 盤中到價即時雷達自動監控與推播" | Out-Null
        Write-Host "[✓] 成功註冊策略04盤中雷達工作：$TaskCBRadar (每週一至週五 08:55 啟動)" -ForegroundColor Green
    } catch {
        Write-Host "[!] 註冊 $TaskCBRadar 失敗: $_" -ForegroundColor Red
    }
}

Write-Host "`n[完成] 排程設定已更新完畢！" -ForegroundColor Cyan

