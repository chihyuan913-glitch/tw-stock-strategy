#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
單元測試：台股實戰四大防線標準定價引擎 V4.0 (four_price_engine.py)
"""

import pytest
from four_price_engine import (
    get_twse_tick_size,
    normalize_to_tick,
    get_limit_prices,
    calculate_four_prices_long,
    calculate_four_prices_short,
    apply_four_prices_to_record,
)


def test_twse_tick_sizes():
    """驗證臺灣證交所法定 6 階升降單位"""
    assert get_twse_tick_size(8.5) == 0.01
    assert get_twse_tick_size(9.99) == 0.01
    assert get_twse_tick_size(10.0) == 0.05
    assert get_twse_tick_size(49.95) == 0.05
    assert get_twse_tick_size(50.0) == 0.10
    assert get_twse_tick_size(99.9) == 0.10
    assert get_twse_tick_size(100.0) == 0.50
    assert get_twse_tick_size(499.5) == 0.50
    assert get_twse_tick_size(500.0) == 1.00
    assert get_twse_tick_size(999.0) == 1.00
    assert get_twse_tick_size(1000.0) == 5.00
    assert get_twse_tick_size(2500.0) == 5.00


def test_directional_normalization():
    """驗證方向性檔位取整"""
    # 10~50 元級距 (Tick 0.05)
    assert normalize_to_tick(28.32, direction="up") == 28.35
    assert normalize_to_tick(28.32, direction="down") == 28.30
    assert normalize_to_tick(28.32, direction="nearest") == 28.30

    # 100~500 元級距 (Tick 0.50)
    assert normalize_to_tick(233.15, direction="up") == 233.50
    assert normalize_to_tick(233.15, direction="down") == 233.00

    # >=1000 元級距 (Tick 5.00)
    assert normalize_to_tick(1021.0, direction="up") == 1025.0
    assert normalize_to_tick(1021.0, direction="down") == 1020.0


def test_limit_prices_calc():
    """驗證台股 ±10% 漲跌停價合規計算"""
    up_100, down_100 = get_limit_prices(100.0)
    assert up_100 == 110.0
    assert down_100 == 90.0

    # 測試非整數收盤價，合規向下取整漲停、向上取整跌停
    up_233, down_233 = get_limit_prices(233.5)
    assert up_233 <= 233.5 * 1.10
    assert down_233 >= 233.5 * 0.90
    assert get_twse_tick_size(up_233) == 0.50


def test_calculate_four_prices_long_monotonicity():
    """驗證多方四大防線幾何單調性與合規性"""
    test_cases = [
        {"curr": 9.5, "high": 9.7, "low": 9.3},
        {"curr": 28.5, "high": 29.0, "low": 28.0},
        {"curr": 75.0, "high": 76.2, "low": 73.8},
        {"curr": 233.0, "high": 236.0, "low": 230.0},
        {"curr": 680.0, "high": 690.0, "low": 670.0},
        {"curr": 1050.0, "high": 1070.0, "low": 1030.0},
    ]

    for tc in test_cases:
        p = calculate_four_prices_long(
            curr_price=tc["curr"],
            high=tc["high"],
            low=tc["low"],
            regime_params={"tp1_pct": 5.0, "tp2_pct": 10.0, "stop_loss_pct": -4.0},
        )

        curr = p["current_price"]
        sl = p["sl_price"]
        entry = p["entry_price"]
        addon = p["addon_price"]
        tp1 = p["tp1_price"]
        tp2 = p["tp2_price"]
        limit_up = p["limit_up"]
        limit_down = p["limit_down"]

        # 核心幾何單調性斷言
        assert limit_down <= sl < curr, f"停損必須在現價之下且高於跌停價: sl={sl}, curr={curr}"
        assert curr <= entry, f"進場買進上限不可低於現價: entry={entry}, curr={curr}"
        assert entry < addon, f"動能加碼價必須高於進場價: entry={entry}, addon={addon}"
        assert addon < tp1, f"目標停利 TP1 必須高於加碼價: addon={addon}, tp1={tp1}"
        assert tp1 <= tp2, f"目標停利 TP2 不可小於 TP1: tp1={tp1}, tp2={tp2}"
        assert tp2 <= limit_up, f"停利目標不可高於漲停價: tp2={tp2}, limit_up={limit_up}"

        # 證交所 Tick Size 驗證
        for name, val in [("SL", sl), ("Entry", entry), ("Addon", addon), ("TP1", tp1), ("TP2", tp2)]:
            tick = get_twse_tick_size(val)
            rem = round(round(val / tick, 4) % 1, 4)
            assert rem == 0.0 or rem == 1.0, f"{name} {val} 不符合證交所法定檔位 Tick {tick}"


def test_calculate_four_prices_short_monotonicity():
    """驗證空方四大防線幾何單調性與合規性"""
    test_cases = [
        {"curr": 25.0, "high": 25.8, "low": 24.5},
        {"curr": 180.0, "high": 184.0, "low": 178.0},
        {"curr": 1020.0, "high": 1040.0, "low": 1005.0},
    ]

    for tc in test_cases:
        p = calculate_four_prices_short(
            curr_price=tc["curr"],
            high=tc["high"],
            low=tc["low"],
            regime_params={"tp1_pct": 5.0, "stop_loss_pct": 4.0},
        )

        curr = p["current_price"]
        sl = p["sl_price"]
        entry = p["entry_price"]
        addon = p["addon_price"]
        tp1 = p["tp1_price"]
        tp2 = p["tp2_price"]

        # 空方單調性斷言
        assert tp2 <= tp1 < addon < entry <= curr < sl
        assert p["risk_reward_ratio"] > 0


def test_apply_four_prices_to_record():
    """驗證策略結果記錄的自動標準化與欄位校準"""
    raw_record = {
        "證券代號": "2330",
        "證券名稱": "台積電",
        "收盤價": 980.0,
        "最高價": 995.0,
        "最低價": 970.0,
        "布林中軌": 950.0,
    }

    res = apply_four_prices_to_record(raw_record, is_short=False)

    assert "建議進場價" in res
    assert "動能加碼價" in res
    assert "停利TP1" in res
    assert "停利TP2" in res
    assert "嚴格停損價" in res
    assert "第一次進場價位" in res
    assert "第二次進場價位" in res
    assert "操盤錦囊" in res

    # 驗證單調性
    assert res["嚴格停損價"] < res["收盤價"] <= res["建議進場價"] < res["動能加碼價"] < res["停利TP1"] <= res["停利TP2"]
    # 驗證 980 元位於 500~1000 元級距，Tick Size 為 1.0 元
    assert res["建議進場價"] % 1.0 == 0.0
    assert res["嚴格停損價"] % 1.0 == 0.0

