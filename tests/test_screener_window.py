# -*- coding: utf-8 -*-
"""
tests/test_screener_window.py
================================================================================
台股雲端全策略盤後選股時段守衛 (Daily Screener Time Window Guard) 離線測試套件
================================================================================
依據 2026-11-13 規範，全面覆蓋 6 大測試情境：
1. 交易日週五 21:15、21:10:00、21:40:59：視窗函式為通過；run_all.py（含 --line）正常放行
2. 交易日週五 21:09:59、21:41:00：視窗函式為不通過；run_all.py（不論有無 --line，即使帶 --force）以碼 0 結束，不得啟動策略子程序、不得呼叫 LINE／Telegram 發送
3. 實例凍結時間：台北時間 2026-11-13 04:47:37 及對應 UTC 2026-11-12 20:47:37（機器時區為 UTC 也必須判為窗外）；run_all.py --line 以碼 0 結束，不推播不跑策略
4. 週六 04:47、週日 21:15、週三 15:58：均為窗外，結束碼 0、不推播
5. Workflow YAML 模擬：
   - 窗外時輸出 in_window=false，後續步驟全部跳過；窗內正常放行
   - 封存 Artifact 步驟條件精確為: if: always() && steps.check_window.outputs.in_window == 'true'
   - 窗內時，即使前面步驟失敗 (如 CMoney 404 exit 1)，Artifact 條件仍為 True (必須封存)
   - 窗外時，即使有 always()，Artifact 條件仍為 False (絕對不得封存)
6. 盤中守衛未受干擾驗證：確保 09:00~13:35 既有開盤到價確認邏輯完全不受干擾
================================================================================
"""

import sys
import os
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

WORK_DIR = Path(__file__).resolve().parents[1]
if str(WORK_DIR) not in sys.path:
    sys.path.insert(0, str(WORK_DIR))

import market_schedule
from market_schedule import (
    TZ_TW,
    SCREENER_WINDOW_START_TIME,
    SCREENER_WINDOW_END_TIME,
    is_daily_screener_window,
    get_taiwan_now,
)
import run_all


# ------------------------------------------------------------------------------
# 測試情境 1: 交易日週五視窗內 (21:15:00, 21:10:00, 21:40:59)
# ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "dt_tw,desc",
    [
        (datetime.datetime(2026, 11, 13, 21, 15, 0, tzinfo=TZ_TW), "週五 21:15:00 正常排程時段"),
        (datetime.datetime(2026, 11, 13, 21, 10, 0, tzinfo=TZ_TW), "週五 21:10:00 視窗起始下邊界(含)"),
        (datetime.datetime(2026, 11, 13, 21, 40, 59, 999999, tzinfo=TZ_TW), "週五 21:40:59 視窗結束上邊界(含)"),
    ],
)
def test_scenario_1_inside_window_function(dt_tw, desc):
    """驗證週五視窗內時，is_daily_screener_window 回傳 True"""
    in_win, reason = is_daily_screener_window(dt_tw)
    assert in_win is True, f"[{desc}] 應在視窗內，但回傳 False: {reason}"
    assert "處於盤後選股時段視窗內" in reason


def test_scenario_1_run_all_proceeds_in_window():
    """驗證週五 21:15 視窗內執行 run_all.py --line 時，不會在時段檢查處被提早中斷"""
    dt_tw = datetime.datetime(2026, 11, 13, 21, 15, 0, tzinfo=TZ_TW)

    with patch.object(sys, "argv", ["run_all.py", "--line"]), \
         patch("market_schedule.get_taiwan_now", return_value=dt_tw), \
         patch("market_schedule.check_market_open_or_exit", return_value=False) as mock_market_check, \
         patch("run_all.run_strategy") as mock_run_strat:

        # 當 check_market_open_or_exit 被呼叫並回傳 False 時，main() 會返回 0
        # 這證明已成功通過第一道「時段守衛檢查」，進入了第二道「開休市守衛檢查」
        ret = run_all.main()
        assert ret == 0
        mock_market_check.assert_called_once()
        mock_run_strat.assert_not_called()


# ------------------------------------------------------------------------------
# 測試情境 2: 交易日週五邊界外 (21:09:59, 21:41:00) 嚴格攔截，--force 亦不可略過
# ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "dt_tw,desc",
    [
        (datetime.datetime(2026, 11, 13, 21, 9, 59, tzinfo=TZ_TW), "週五 21:09:59 早於視窗起始邊界"),
        (datetime.datetime(2026, 11, 13, 21, 41, 0, tzinfo=TZ_TW), "週五 21:41:00 晚於或等於視窗結束邊界"),
    ],
)
def test_scenario_2_outside_window_boundaries(dt_tw, desc):
    """驗證週五邊界外一秒的點，is_daily_screener_window 均精確判定為 False"""
    in_win, reason = is_daily_screener_window(dt_tw)
    assert in_win is False, f"[{desc}] 應在視窗外，但回傳 True: {reason}"


@pytest.mark.parametrize(
    "cli_args",
    [
        ["run_all.py"],
        ["run_all.py", "--line"],
        ["run_all.py", "--force"],
        ["run_all.py", "--line", "--force"],
    ],
)
def test_scenario_2_run_all_outside_window_with_force(cli_args):
    """驗證週五 21:41:00 窗外時，無論是否帶 --line 或 --force，run_all.py 均以碼 0 退出，且絕不啟動策略與推播"""
    dt_tw = datetime.datetime(2026, 11, 13, 21, 41, 0, tzinfo=TZ_TW)

    with patch.object(sys, "argv", cli_args), \
         patch("market_schedule.get_taiwan_now", return_value=dt_tw), \
         patch("market_schedule.check_market_open_or_exit") as mock_market_check, \
         patch("market_schedule.verify_twse_data_settlement") as mock_verify_twse, \
         patch("run_all.run_strategy") as mock_run_strat, \
         patch("subprocess.run") as mock_subprocess:

        ret = run_all.main()
        assert ret == 0, f"窗外必須回傳 exit code 0，實際回傳: {ret}"
        # 關鍵驗證：窗外直接退出，連休市檢查與數據結算都不必執行
        mock_market_check.assert_not_called()
        mock_verify_twse.assert_not_called()
        mock_run_strat.assert_not_called()
        mock_subprocess.assert_not_called()


# ------------------------------------------------------------------------------
# 測試情境 3: 凍結 2026-11-13 凌晨延誤實例時間 (台北 04:47:37 / UTC 20:47:37)
# ------------------------------------------------------------------------------
def test_scenario_3_incident_freeze_taipei_time():
    """驗證實例時間 台北 2026-11-13 04:47:37 必定判定為窗外"""
    dt_incident_tw = datetime.datetime(2026, 11, 13, 4, 47, 37, tzinfo=TZ_TW)
    in_win, reason = is_daily_screener_window(dt_incident_tw)
    assert in_win is False
    assert "尚未達到盤後選股視窗時段" in reason or "早於 21:10:00" in reason


def test_scenario_3_incident_freeze_utc_time():
    """驗證實例對應之 UTC 時間 2026-11-12 20:47:37 傳入時，自動轉為台北時間 04:47:37 並判為窗外"""
    dt_incident_utc = datetime.datetime(2026, 11, 12, 20, 47, 37, tzinfo=datetime.timezone.utc)
    in_win, reason = is_daily_screener_window(dt_incident_utc)
    assert in_win is False
    assert "尚未達到盤後選股視窗時段" in reason or "早於 21:10:00" in reason


def test_scenario_3_incident_run_all_execution():
    """模擬 11/13 凌晨延誤執行 run_all.py --line，驗證以碼 0 結束，不推播不跑策略"""
    dt_incident_tw = datetime.datetime(2026, 11, 13, 4, 47, 37, tzinfo=TZ_TW)

    with patch.object(sys, "argv", ["run_all.py", "--line"]), \
         patch("market_schedule.get_taiwan_now", return_value=dt_incident_tw), \
         patch("market_schedule.check_market_open_or_exit") as mock_market_check, \
         patch("run_all.run_strategy") as mock_run_strat, \
         patch("subprocess.run") as mock_subprocess:

        ret = run_all.main()
        assert ret == 0
        mock_market_check.assert_not_called()
        mock_run_strat.assert_not_called()
        mock_subprocess.assert_not_called()


# ------------------------------------------------------------------------------
# 測試情境 4: 非交易日與其他時段 (週六 04:47、週日 21:15、週三 15:58)
# ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "dt_tw,expected_snippet",
    [
        (datetime.datetime(2026, 11, 14, 4, 47, 0, tzinfo=TZ_TW), "週六"),
        (datetime.datetime(2026, 11, 15, 21, 15, 0, tzinfo=TZ_TW), "週日"),
        (datetime.datetime(2026, 11, 11, 15, 58, 0, tzinfo=TZ_TW), "早於 21:10:00"),
    ],
)
def test_scenario_4_weekend_and_intraday_outside_window(dt_tw, expected_snippet):
    """驗證週末與平日非盤後時段皆為窗外"""
    in_win, reason = is_daily_screener_window(dt_tw)
    assert in_win is False
    assert expected_snippet in reason

    with patch.object(sys, "argv", ["run_all.py", "--line"]), \
         patch("market_schedule.get_taiwan_now", return_value=dt_tw), \
         patch("run_all.run_strategy") as mock_run_strat:
        ret = run_all.main()
        assert ret == 0
        mock_run_strat.assert_not_called()


# ------------------------------------------------------------------------------
# 測試情境 5: Workflow YAML 條件與 step output 邏輯模擬
# ------------------------------------------------------------------------------
def test_scenario_5_workflow_yaml_structure_and_logic():
    """驗證 cloud_daily_screener.yml 中的 check_window 步驟與後續步驟之 if 條件約束"""
    wf_path = WORK_DIR / ".github" / "workflows" / "cloud_daily_screener.yml"
    assert wf_path.exists(), "cloud_daily_screener.yml 檔案必須存在"

    with open(wf_path, "r", encoding="utf-8") as f:
        wf_content = f.read()
        f.seek(0)
        wf_data = yaml.safe_load(f)

    job = wf_data["jobs"]["run-daily-screener"]
    steps = job["steps"]

    # 1. 驗證 check_window 步驟位置在 checkout 之後
    step_names = [s.get("name", "") for s in steps]
    assert "簽出專案代碼" in step_names[0]
    assert "檢驗台北盤後選股時段視窗 (21:10 ~ 21:40)" in step_names[1]

    check_step = steps[1]
    assert check_step.get("id") == "check_window"
    assert "is_daily_screener_window" in check_step.get("run", "")
    assert "GITHUB_OUTPUT" in check_step.get("run", "")

    # 2. 驗證 check_window 之後的所有步驟都嚴格掛載 if 條件
    expected_if_cond = "steps.check_window.outputs.in_window == 'true'"
    subsequent_steps = steps[2:]
    assert len(subsequent_steps) >= 5, "必須包含 setup-python, pip, run, sync, artifact 等步驟"

    for step in subsequent_steps:
        step_name = step.get("name", "")
        step_if = step.get("if", "")
        assert expected_if_cond in step_if, (
            f"步驟 [{step_name}] 未配置要求的 if 條件! 實際為: {step_if}"
        )


def test_scenario_5_artifact_if_condition_exact():
    """驗證 cloud_daily_screener.yml 中的『封存選股報表 Artifact』步驟精確配置 always() && in_window"""
    wf_path = WORK_DIR / ".github" / "workflows" / "cloud_daily_screener.yml"
    with open(wf_path, "r", encoding="utf-8") as f:
        wf_data = yaml.safe_load(f)

    job = wf_data["jobs"]["run-daily-screener"]
    steps = job["steps"]

    artifact_step = next((s for s in steps if "封存" in s.get("name", "")), None)
    assert artifact_step is not None, "未找到『封存選股報表 Artifact』步驟"
    assert artifact_step.get("if") == "always() && steps.check_window.outputs.in_window == 'true'", (
        f"Artifact 條件必須為 'always() && steps.check_window.outputs.in_window == \\'true\\''，實際為: {artifact_step.get('if')}"
    )


@pytest.mark.parametrize(
    "dt_tw,desc",
    [
        (datetime.datetime(2026, 11, 13, 21, 15, 0, tzinfo=TZ_TW), "週五 21:15:00 正常排程時段"),
        (datetime.datetime(2026, 11, 13, 21, 10, 0, tzinfo=TZ_TW), "週五 21:10:00 視窗起始下邊界(含)"),
        (datetime.datetime(2026, 11, 13, 21, 40, 59, 999999, tzinfo=TZ_TW), "週五 21:40:59 視窗結束上邊界(含)"),
    ],
)
def test_scenario_5_artifact_condition_inside_window_with_failure(dt_tw, desc):
    """驗證窗內時段，即使前面步驟失敗 (如 CMoney 404 exit 1)，Artifact 條件 always() && in_window 仍為 True (必須封存)"""
    in_win, _ = is_daily_screener_window(dt_tw)
    assert in_win is True, f"[{desc}] 應在視窗內"

    # 模擬 GitHub Actions 表達式評估：always() 回傳 True，步驟輸出 in_window 為 'true'
    # 即使上一步 run_all.py 失敗 (exit 1)，always() 保證評估繼續進行
    step_output_in_window = str(in_win).lower()  # "true"
    always_func = True  # always() evaluates to True on failure
    artifact_condition_eval = always_func and (step_output_in_window == "true")

    assert artifact_condition_eval is True, f"[{desc}] 窗內即使前面失敗，Artifact 條件也必須為 True"


@pytest.mark.parametrize(
    "dt,desc",
    [
        (datetime.datetime(2026, 11, 13, 4, 47, 37, tzinfo=TZ_TW), "台北時間 2026-11-13 04:47:37 (實例延誤)"),
        (datetime.datetime(2026, 11, 12, 20, 47, 37, tzinfo=datetime.timezone.utc), "UTC 時間 2026-11-12 20:47:37 (對應實例 UTC)"),
        (datetime.datetime(2026, 11, 14, 4, 47, 0, tzinfo=TZ_TW), "週六 04:47:00 (週末例休)"),
        (datetime.datetime(2026, 11, 15, 21, 15, 0, tzinfo=TZ_TW), "週日 21:15:00 (週末例休)"),
        (datetime.datetime(2026, 11, 11, 15, 58, 0, tzinfo=TZ_TW), "週三 15:58:00 (盤後非選股視窗)"),
        (datetime.datetime(2026, 11, 13, 21, 9, 59, tzinfo=TZ_TW), "週五 21:09:59 (早於視窗起始邊界)"),
        (datetime.datetime(2026, 11, 13, 21, 41, 0, tzinfo=TZ_TW), "週五 21:41:00 (晚於或等於視窗結束邊界)"),
    ],
)
def test_scenario_5_artifact_condition_outside_window_blocked_despite_always(dt, desc):
    """驗證窗外時段，即使條件帶有 always()，也因為 in_window 為 false，Artifact 條件必定為 False，不可封存"""
    in_win, _ = is_daily_screener_window(dt)
    assert in_win is False, f"[{desc}] 應在視窗外"

    # 模擬 GitHub Actions 表達式評估：即使 always() 為 True，但 in_window 為 "false"
    step_output_in_window = str(in_win).lower()  # "false"
    always_func = True
    artifact_condition_eval = always_func and (step_output_in_window == "true")

    assert artifact_condition_eval is False, f"[{desc}] 窗外即使有 always()，Artifact 條件也必須為 False，嚴禁封存！"


def test_scenario_5_workflow_script_output_simulation(tmp_path):
    """模擬 GitHub Actions runner 執行 check_window 腳本時 GITHUB_OUTPUT 寫入情況"""
    output_file = tmp_path / "github_output.txt"

    # 模擬窗外時 (例如 11/13 04:47:37)
    dt_outside = datetime.datetime(2026, 11, 13, 4, 47, 37, tzinfo=TZ_TW)
    with patch("market_schedule.get_taiwan_now", return_value=dt_outside), \
         patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}):

        in_win, reason = is_daily_screener_window()
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"in_window={str(in_win).lower()}\n")

    content = output_file.read_text(encoding="utf-8")
    assert "in_window=false" in content

    # 模擬窗內時 (例如 11/13 21:15:00)
    output_file.write_text("", encoding="utf-8")
    dt_inside = datetime.datetime(2026, 11, 13, 21, 15, 0, tzinfo=TZ_TW)
    with patch("market_schedule.get_taiwan_now", return_value=dt_inside), \
         patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}):

        in_win, reason = is_daily_screener_window()
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"in_window={str(in_win).lower()}\n")

    content = output_file.read_text(encoding="utf-8")
    assert "in_window=true" in content


# ------------------------------------------------------------------------------
# 測試情境 6: 盤中守衛未受干擾驗證 (與既有 test_afterhours_guard.py 一致性)
# ------------------------------------------------------------------------------
def test_scenario_6_intraday_schedule_untouched():
    """驗證盤中時段守衛 (is_intraday_window) 邏輯與常數未受本次盤後選股修改影響"""
    from market_schedule import is_intraday_window, MARKET_OPEN_TIME, MARKET_CLOSE_TIME

    assert MARKET_OPEN_TIME == datetime.time(9, 0, 0)
    assert MARKET_CLOSE_TIME == datetime.time(13, 35, 0)

    # 盤中 11:00 應正常判定在盤中
    dt_midday = datetime.datetime(2026, 11, 13, 11, 0, 0, tzinfo=TZ_TW)
    with patch("market_schedule.is_market_open_today", return_value=(True, "正常交易日")):
        is_intra, reason = is_intraday_window(dt_midday)
        assert is_intra is True

    # 盤後 15:58 應判定非盤中
    dt_after = datetime.datetime(2026, 11, 13, 15, 58, 0, tzinfo=TZ_TW)
    with patch("market_schedule.is_market_open_today", return_value=(True, "正常交易日")):
        is_intra, reason = is_intraday_window(dt_after)
        assert is_intra is False
