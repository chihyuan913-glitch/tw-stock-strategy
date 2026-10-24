#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
單元測試：操盤多階段進場/停利歷程、待剔除保留 1 天與歷史戰績記錄庫
"""

import pytest
import datetime
import pandas as pd
from pathlib import Path
from rolling_retention_manager import merge_rolling_records
from trade_history_manager import TradeHistoryManager


@pytest.fixture
def temp_history_manager(tmp_path):
    json_path = tmp_path / "test_trade_history.json"
    csv_path = tmp_path / "test_trade_history.csv"
    return TradeHistoryManager(json_path=json_path, csv_path=csv_path)


def test_multi_stage_entry_and_tp_ladder():
    """驗證個股字卡第一次進場/停利、第二次進場/停利 (加碼) 階梯記錄"""
    d1 = "2026-10-20"
    day1_records = [
        {
            "證券代號": "2330",
            "證券名稱": "台積電",
            "建議進場價": 1000.0,
            "停利TP1": 1080.0,
            "動能加碼價": 1040.0,
            "停利TP2": 1150.0,
            "嚴格停損價": 960.0,
            "收盤價": 1005.0,
            "評分": 90
        }
    ]

    df_stage1 = merge_rolling_records(None, day1_records, today_date_str=d1)
    r1 = df_stage1[df_stage1["證券代號"] == "2330"].iloc[0]

    assert r1["第一次進場價位"] == 1000.0
    assert r1["第一次停利價位"] == 1080.0
    assert r1["第二次進場價位"] == 1040.0
    assert r1["第二次停利價位"] == 1150.0
    assert "第 1 階段" in r1["操盤階段"]
    assert r1["生命週期狀態"] == "ACTIVE"

    # Day 2: 續強觸發加碼
    d2 = "2026-10-21"
    day2_records = [
        {
            "證券代號": "2330",
            "證券名稱": "台積電",
            "建議進場價": 1045.0,
            "停利TP1": 1120.0,
            "動能加碼價": 1080.0,
            "停利TP2": 1200.0,
            "嚴格停損價": 990.0,
            "收盤價": 1050.0,
            "評分": 95
        }
    ]

    df_stage2 = merge_rolling_records(df_stage1, day2_records, today_date_str=d2)
    r2 = df_stage2[df_stage2["證券代號"] == "2330"].iloc[0]

    # 第一次進場價位必須恆久保持
    assert r2["第一次進場價位"] == 1000.0
    assert r2["第一次停利價位"] == 1080.0
    # 第二次加碼價位被刷新記錄
    assert r2["第二次進場價位"] == 1045.0 or r2["第二次進場價位"] == 1080.0
    assert "第 2 階段" in r2["操盤階段"]
    assert r2["狀態標註"] == "🔥 續強觸發"


def test_pending_removal_retention_and_next_day_archival(monkeypatch, tmp_path):
    """
    驗證待剔除機制：
    1. 觸發停損或停利時，當天轉為 PENDING_REMOVAL 並保留一天
    2. 次日盤後更新時，正式從名冊除帳並寫入操盤戰績記錄庫
    """
    fake_mgr = TradeHistoryManager(
        json_path=tmp_path / "trade_history.json",
        csv_path=tmp_path / "trade_history.csv"
    )
    import rolling_retention_manager
    monkeypatch.setattr(rolling_retention_manager, "GLOBAL_TRADE_HISTORY", fake_mgr)

    d1 = "2026-10-20"
    day1_records = [
        {
            "證券代號": "2603",
            "證券名稱": "長榮",
            "建議進場價": 200.0,
            "停利TP1": 220.0,
            "停利TP2": 240.0,
            "嚴格停損價": 190.0,
            "收盤價": 202.0,
            "評分": 85
        }
    ]
    df1 = rolling_retention_manager.merge_rolling_records(None, day1_records, today_date_str=d1, strategy_id="02_institutional_momentum")
    assert len(df1) == 1
    assert df1.iloc[0]["生命週期狀態"] == "ACTIVE"

    # Day 2: 股價跌破停損線 (188.0 <= 190.0)，今日沒有再次入選
    d2 = "2026-10-21"
    df1.loc[df1["證券代號"] == "2603", "收盤價"] = 188.0

    # 當日選股無新股
    df2 = rolling_retention_manager.merge_rolling_records(df1, [], today_date_str=d2, strategy_id="02_institutional_momentum")

    # 檢驗：該股當天依然保留在名單中（保留 1 天）
    assert len(df2) == 1
    r_rem = df2[df2["證券代號"] == "2603"].iloc[0]
    assert r_rem["生命週期狀態"] == "PENDING_REMOVAL"
    assert "待剔除" in r_rem["狀態標註"]
    assert r_rem["剔除觸發日期"] == d2
    assert "跌破嚴格停損" in r_rem["剔除原因"]
    assert r_rem["操作損益率%"] < 0  # 虧損停損

    # 此時尚未除帳，戰績庫仍無紀錄
    assert len(fake_mgr.load_trades()) == 0

    # Day 3: 次日更新，該股已保留滿 1 天，應正式除帳並寫入戰績庫
    d3 = "2026-10-22"
    day3_new_records = [
        {
            "證券代號": "2330",
            "證券名稱": "台積電",
            "建議進場價": 1050.0,
            "停利TP1": 1100.0,
            "收盤價": 1050.0
        }
    ]
    df3 = rolling_retention_manager.merge_rolling_records(df2, day3_new_records, today_date_str=d3, strategy_id="02_institutional_momentum")

    # 檢驗：2603 已正式自選股名冊除帳
    assert "2603" not in df3["證券代號"].values
    assert len(df3) == 1
    assert df3.iloc[0]["證券代號"] == "2330"

    # 檢驗：2603 已成功歸檔至操盤戰績記錄庫
    closed_trades = fake_mgr.load_trades()
    assert len(closed_trades) == 1
    t = closed_trades[0]
    assert t["code"] == "2603"
    assert t["name"] == "長榮"
    assert t["strategy_id"] == "02_institutional_momentum"
    assert t["first_entry_price"] == 200.0
    assert t["exit_price"] == 188.0
    assert t["return_pct"] == -6.0
    assert t["pnl_status"] == "LOSS"
    assert "跌破嚴格停損" in t["exit_reason"]


def test_trade_history_manager_kpi_summary(temp_history_manager):
    """驗證歷史戰績統計總覽與勝率計算"""
    temp_history_manager.record_closed_trade({
        "code": "2330", "name": "台積電", "strategy_id": "01_bollinger_reversal",
        "first_entry_price": 1000.0, "exit_price": 1080.0, "holding_days": 3
    })
    temp_history_manager.record_closed_trade({
        "code": "2454", "name": "聯發科", "strategy_id": "01_bollinger_reversal",
        "first_entry_price": 1200.0, "exit_price": 1320.0, "holding_days": 4
    })
    temp_history_manager.record_closed_trade({
        "code": "2603", "name": "長榮", "strategy_id": "01_bollinger_reversal",
        "first_entry_price": 200.0, "exit_price": 190.0, "holding_days": 2
    })

    summary = temp_history_manager.get_performance_summary("01_bollinger_reversal")
    assert summary["total_trades"] == 3
    assert summary["winning_trades"] == 2
    assert summary["losing_trades"] == 1
    assert summary["win_rate_pct"] == 66.7
    assert summary["max_win_pct"] == 10.0  # 聯發科 +10%
    assert summary["max_loss_pct"] == -5.0  # 長榮 -5%
