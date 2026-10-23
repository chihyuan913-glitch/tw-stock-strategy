#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
單元測試：5 日滾動保留與歷史追蹤管理器 (rolling_retention_manager.py)
================================================================================
驗證指標：
1. 5 日滾動保留：今日新選與歷史標的順利合併，不丟失過往標的。
2. 重複觸發更新：舊標的今日再度入選時，刷新數值並標記「🔥 續強觸發」。
3. 嚴格 5 交易日水位防守：超過 5 個交易日之標的自動被淘汰除帳。
4. 欄位結構完整性：自動補齊「選股日期」、「入選天數」、「狀態標註」。
5. 現有策略報表相容性：檢查 strategies/*/result.csv 皆符合規範。
================================================================================
"""

import os
import sys
import tempfile
import pandas as pd
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rolling_retention_manager import (
    normalize_date_str,
    extract_code,
    extract_name,
    merge_rolling_records,
    save_rolling_result_csv,
    DEFAULT_RETENTION_DAYS,
)


def test_normalize_date_str():
    assert normalize_date_str("2026-10-23") == "2026-10-23"
    assert normalize_date_str("2026/10/23") == "2026-10-23"
    assert normalize_date_str("20261023") == "2026-10-23"
    assert normalize_date_str("2026.10.23") == "2026-10-23"


def test_extract_code_and_name():
    row1 = {"證券代號": "2330", "證券名稱": "台積電"}
    assert extract_code(row1) == "2330"
    assert extract_name(row1) == "台積電"

    row2 = {"股票代碼": "2317.0", "股票名稱": "鴻海"}
    assert extract_code(row2) == "2317"
    assert extract_name(row2) == "鴻海"


def test_merge_rolling_records_basic():
    """驗證新舊標的合併與天數遞增"""
    day1_records = [
        {"證券代號": "2330", "證券名稱": "台積電", "收盤價": 1000.0, "評分": 85},
        {"證券代號": "2317", "證券名稱": "鴻海", "收盤價": 200.0, "評分": 75},
    ]
    df_day1 = merge_rolling_records(None, day1_records, today_date_str="2026-10-20")
    assert len(df_day1) == 2
    assert "選股日期" in df_day1.columns
    assert "入選天數" in df_day1.columns
    assert "狀態標註" in df_day1.columns
    assert all(df_day1["選股日期"] == "2026-10-20")
    assert all("T+0" in t for t in df_day1["入選天數"])
    assert all(s == "🟢 今日新選" for s in df_day1["狀態標註"])

    # Day 2 新增 2454 聯發科
    day2_records = [
        {"證券代號": "2454", "證券名稱": "聯發科", "收盤價": 1300.0, "評分": 90}
    ]
    df_day2 = merge_rolling_records(df_day1, day2_records, today_date_str="2026-10-21")
    assert len(df_day2) == 3

    # 今日新選在最前
    assert df_day2.iloc[0]["證券代號"] == "2454"
    assert df_day2.iloc[0]["狀態標註"] == "🟢 今日新選"
    assert "T+0" in df_day2.iloc[0]["入選天數"]

    # 舊標的仍保留且天數標註為 T+1
    old_2330 = df_day2[df_day2["證券代號"] == "2330"].iloc[0]
    assert old_2330["選股日期"] == "2026-10-20"
    assert "T+1" in old_2330["入選天數"]
    assert old_2330["狀態標註"] == "⏳ 持續追蹤"


def test_merge_rolling_records_retrigger():
    """驗證已在池中之標的再次觸發 (續強更新)"""
    day1_records = [
        {"證券代號": "2330", "證券名稱": "台積電", "收盤價": 1000.0, "評分": 80},
    ]
    df_day1 = merge_rolling_records(None, day1_records, today_date_str="2026-10-20")

    # Day 2 台積電再次被選中，收盤價漲至 1050，評分升至 95
    day2_records = [
        {"證券代號": "2330", "證券名稱": "台積電", "收盤價": 1050.0, "評分": 95},
        {"證券代號": "2382", "證券名稱": "廣達", "收盤價": 300.0, "評分": 85},
    ]
    df_day2 = merge_rolling_records(df_day1, day2_records, today_date_str="2026-10-21")

    # 不應重複，總數為 2
    assert len(df_day2) == 2
    r_2330 = df_day2[df_day2["證券代號"] == "2330"].iloc[0]
    assert r_2330["收盤價"] == 1050.0
    assert r_2330["評分"] == 95
    assert r_2330["選股日期"] == "2026-10-21"
    assert r_2330["狀態標註"] == "🔥 續強觸發"
    assert "續強" in r_2330["入選天數"]


def test_merge_rolling_records_pruning_beyond_5_days():
    """驗證超過 5 個交易日之標的自動被淘汰"""
    running_df = None
    dates = [
        "2026-10-15",
        "2026-10-16",
        "2026-10-19",
        "2026-10-20",
        "2026-10-21",
        "2026-10-22",  # 第 6 個交易日
    ]

    for idx, d in enumerate(dates):
        daily_records = [
            {"證券代號": f"100{idx}", "證券名稱": f"股{idx}", "收盤價": 10.0 + idx, "評分": 70 + idx}
        ]
        running_df = merge_rolling_records(running_df, daily_records, today_date_str=d, max_days=5)

    # 應剛好保留 5 個不同交易日的標的
    unique_dates = running_df["選股日期"].unique()
    assert len(unique_dates) == 5
    assert "2026-10-15" not in unique_dates, "最舊的第 6 個交易日 2026-10-15 應被淘汰"
    assert "2026-10-22" in unique_dates
    assert "2026-10-16" in unique_dates
    assert len(running_df) == 5


def test_save_rolling_result_csv_roundtrip():
    """驗證磁碟 CSV 讀寫與 UTF-8-BOM 編碼正確性"""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_file = Path(tmpdir) / "test_result.csv"

        records_day1 = [
            {"證券代號": "2330", "證券名稱": "台積電", "進場價位": 1000.0, "停損價位": 950.0}
        ]
        save_rolling_result_csv(csv_file, records_day1, today_date_str="2026-10-20")

        # 檢測檔案存在與讀取
        assert csv_file.exists()
        df1 = pd.read_csv(csv_file, encoding="utf-8-sig")
        assert len(df1) == 1
        assert df1.iloc[0]["證券代號"] == 2330 or df1.iloc[0]["證券代號"] == "2330"
        assert df1.iloc[0]["選股日期"] == "2026-10-20"

        # Day 2 滾動新增
        records_day2 = [
            {"證券代號": "2454", "證券名稱": "聯發科", "進場價位": 1300.0, "停損價位": 1250.0}
        ]
        save_rolling_result_csv(csv_file, records_day2, today_date_str="2026-10-21")

        df2 = pd.read_csv(csv_file, encoding="utf-8-sig")
        assert len(df2) == 2
        codes = [str(c) for c in df2["證券代號"]]
        assert "2330" in codes and "2454" in codes


def test_existing_strategies_result_csv_columns():
    """驗證現有 9 大策略 result.csv 皆具備 5 日滾動核心追蹤欄位"""
    strat_dirs = [
        "01_bollinger_reversal",
        "02_institutional_momentum",
        "03_short_momentum",
        "04_cb_pricing_ambush",
        "05_stock_futures_long",
        "06_volume_surge",
        "07_margin_call_ambush",
        "08_group_window_dressing",
        "09_treasury_stock_buyback",
    ]

    for s in strat_dirs:
        csv_p = ROOT_DIR / "strategies" / s / "result.csv"
        if not csv_p.exists():
            continue
        df = pd.read_csv(csv_p, encoding="utf-8-sig")
        if df.empty:
            continue
        assert "選股日期" in df.columns, f"{s} result.csv 缺少 '選股日期' 欄位"
        assert "入選天數" in df.columns, f"{s} result.csv 缺少 '入選天數' 欄位"
        assert "狀態標註" in df.columns, f"{s} result.csv 缺少 '狀態標註' 欄位"
