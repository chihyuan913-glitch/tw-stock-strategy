#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
單元測試：台股開休市與交易日守護引擎 (test_market_schedule.py)
驗證四層防線：週末例休、官方法定假日、颱風假緊急公告與未開盤不推送攔截。
"""

import sys
import datetime
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from market_schedule import (
    is_market_open_today,
    should_suppress_push,
    tw_date_to_iso,
    iso_to_tw_date,
    check_typhoon_and_emergency_closure,
    get_cached_holidays,
    check_market_open_or_exit
)


def test_date_conversion_helpers():
    """驗證民國年與 ISO 日期轉換"""
    assert tw_date_to_iso("1150101") == "2026-01-01"
    assert tw_date_to_iso("1151026") == "2026-10-26"
    assert tw_date_to_iso("1150218") == "2026-02-18"
    assert tw_date_to_iso("invalid") is None

    assert iso_to_tw_date("2026-01-01") == "1150101"
    assert iso_to_tw_date("2026-10-26") == "1151026"
    assert iso_to_tw_date(datetime.date(2026, 10, 26)) == "1151026"


def test_weekend_detection():
    """第 1 防線：驗證週末固定休市"""
    saturday = datetime.date(2026, 10, 24)
    sunday = datetime.date(2026, 10, 25)

    is_open_sat, reason_sat = is_market_open_today(saturday)
    assert is_open_sat is False
    assert "週六例假日休市" in reason_sat

    is_open_sun, reason_sun = is_market_open_today(sunday)
    assert is_open_sun is False
    assert "週日例假日休市" in reason_sun


def test_official_holiday_detection():
    """第 2 防線：驗證官方法定休市日曆檢驗"""
    # 2026-10-26: 臺灣光復暨金門古寧頭大捷紀念日補假
    is_open_1026, reason_1026 = is_market_open_today("2026-10-26", check_realtime=False)
    assert is_open_1026 is False
    assert "紀念日" in reason_1026 or "補假" in reason_1026

    # 2026-01-01: 中華民國開國紀念日
    is_open_0101, reason_0101 = is_market_open_today("2026-01-01", check_realtime=False)
    assert is_open_0101 is False
    assert "開國紀念日" in reason_0101 or "法定假日" in reason_0101


def test_normal_trading_day_detection():
    """驗證正常工作開盤交易日 (2026-10-27 週二)"""
    tuesday = datetime.date(2026, 10, 27)
    is_open, reason = is_market_open_today(tuesday, check_realtime=False, check_emergency=False)
    assert is_open is True
    assert "正常開盤交易日" in reason


def test_typhoon_emergency_detection():
    """第 3 防線：驗證颱風假緊急休市公告 (2026-10-23 颱風休市一天)"""
    is_typhoon, reason = check_typhoon_and_emergency_closure("2026-10-23")
    assert is_typhoon is True
    assert "休市" in reason


def test_should_suppress_push():
    """驗證推播攔截守門員"""
    # 休市日 (2026-10-26) 應嚴格攔截推播
    suppress, reason = should_suppress_push(date_val="2026-10-26", force=False, task_type="盤後戰報")
    assert suppress is True
    assert "休市不推送" in reason

    # 帶入 force=True 應自動放行
    suppress_force, reason_force = should_suppress_push(date_val="2026-10-26", force=True)
    assert suppress_force is False
    assert "強制" in reason_force

    # 正常交易日 (2026-10-27) 應正常放行
    suppress_normal, _ = should_suppress_push(date_val="2026-10-27", force=False)
    assert suppress_normal is False


def test_check_market_open_or_exit_guard(monkeypatch):
    """驗證執行器開盤檢查與強制旗標"""
    # 模擬休市情境
    monkeypatch.setattr("market_schedule.is_market_open_today", lambda: (False, "模擬休市日"))
    monkeypatch.setattr(sys, "argv", ["run_all.py"])
    res = check_market_open_or_exit(context_name="測試任務", allow_force=True)
    assert res is False

    # 模擬休市情境但帶有 --force 旗標時應放行
    monkeypatch.setattr(sys, "argv", ["run_all.py", "--force"])
    res_force = check_market_open_or_exit(context_name="測試任務", allow_force=True)
    assert res_force is True

    # 模擬正常交易日且無強制旗標
    monkeypatch.setattr("market_schedule.is_market_open_today", lambda: (True, "正常交易日"))
    monkeypatch.setattr(sys, "argv", ["run_all.py"])
    res_open = check_market_open_or_exit(context_name="測試任務", allow_force=True)
    assert res_open is True
