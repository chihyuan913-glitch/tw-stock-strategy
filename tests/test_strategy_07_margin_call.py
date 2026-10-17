#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 07【高融資使用率 × 低融資維持率（雙向斷頭伏擊與浮額清洗）】自動化測試合約 (pytest)
================================================================================
本測試嚴格依據《台股量化選股策略最高總指揮中心》全域規範與 docs/api-spec.json 制定：
- 機械客觀真實驗證，絕不使用過度假 Mock 或必過空測試
- 檢驗真實演算法核心：
  1. 融資流水帳移動加權成本與融資維持率/使用率精算與極端防呆 (ZeroDivisionError)
  2. 雙向狀態機分流 (SHORT_BREAKDOWN vs. LONG_REVERSAL) 與四大防線定價幾何順序
  3. 4+1 硬濾網與大盤多頭 Tier-2 相對高危降級容錯與排序
  4. 盤中雷達雙向到價觸發與 GLOBAL_TICK_GUARD 防毛刺雙重確認
  5. 總指揮中心全域調度器 (run_all, run_all_intraday, line_sender, line_bot_server) 系統掛載
================================================================================
"""

import os
import sys
import importlib.util
from pathlib import Path
import pytest

# 將專案根目錄加入 sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from algo_guards import GLOBAL_TICK_GUARD


def load_screener_module():
    """動態載入策略 07 盤後選股模組"""
    screener_path = ROOT_DIR / "strategies" / "07_margin_call_ambush" / "screener.py"
    if not screener_path.exists():
        pytest.fail(f"策略 07 盤後選股模組尚未建立: {screener_path} (請確認後端模組已生成)")
    spec = importlib.util.spec_from_file_location("screener_07", screener_path)
    if spec is None or spec.loader is None:
        pytest.fail(f"無法載入模組規格: {screener_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_scanner_module():
    """動態載入策略 07 盤中雷達模組"""
    scanner_path = ROOT_DIR / "strategies" / "07_margin_call_ambush" / "intraday_scanner.py"
    if not scanner_path.exists():
        pytest.fail(f"策略 07 盤中雷達模組尚未建立: {scanner_path} (請確認後端模組已生成)")
    spec = importlib.util.spec_from_file_location("scanner_07", scanner_path)
    if spec is None or spec.loader is None:
        pytest.fail(f"無法載入模組規格: {scanner_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================================
# 1. 動態融資成本與維持率精算驗證 (Margin Maintenance Ratio Calculation)
# ============================================================================
def test_margin_maintenance_ratio_calculation():
    """
    測試 1：
    1. 構造確定性的融資買賣現償流水帳：
       在典型價格 100 元買進 5,000 張，限額 10,000 張，最新收盤價 78 元。
       斷言:
         avg_cost == approx(100.0, rel=1e-2)
         maintenance_ratio == approx(130.0, rel=1e-2)
         usage_ratio == approx(50.0, rel=1e-2)
    2. 測試極端邊界：空陣列 []、quota_lots=0、balance_lots=0，斷言安全回傳 0.0 且不發生 ZeroDivisionError。
    """
    screener = load_screener_module()
    assert hasattr(screener, "calculate_margin_cost_and_maintenance"), "screener 缺少 calculate_margin_cost_and_maintenance 函式"

    # 標準情境：單日融資建倉 5,000 張 @ 100 元，融資限額 10,000 張，收盤跌至 78 元
    history_standard = [
        {
            "buy_lots": 5000,
            "sell_lots": 0,
            "cash_repay_lots": 0,
            "prev_balance_lots": 0,
            "balance_lots": 5000,
            "quota_lots": 10000,
            "typical_price": 100.0,
        }
    ]
    res_std = screener.calculate_margin_cost_and_maintenance(history_standard, close_price=78.0)

    # 驗證數學精確度:
    # 融資成本 C_bar = 100.0
    # 融資維持率 MR = 78.0 / (100.0 * 0.6) * 100 = 130.0%
    # 融資使用率 UR = 5000 / 10000 * 100 = 50.0%
    assert res_std["avg_cost"] == pytest.approx(100.0, rel=1e-2)
    assert res_std["maintenance_ratio"] == pytest.approx(130.0, rel=1e-2)
    assert res_std["usage_ratio"] == pytest.approx(50.0, rel=1e-2)
    assert res_std["balance_lots"] == 5000

    # 多日遞推情境：Day 1 買進 3,000 張 @ 100 元；Day 2 買進 2,000 張 @ 90 元、賣出 1,000 張 @ 90 元
    # 遞推成本:
    # Day 1: 3000 * 100 = 300,000 (成本 100.0, 餘額 3000)
    # Day 2: 賣出 1000 張按成本出帳 -> 剩餘 (3000 - 1000) = 2000 張 @ 100.0 = 200,000
    #        新買進 2000 張 @ 90.0 = 180,000
    #        加權成本 = (200,000 + 180,000) / 4000 = 380,000 / 4000 = 95.0
    # 收盤價 76.0 -> MR = 76.0 / (95.0 * 0.6) * 100 = 133.33%
    history_multiday = [
        {
            "buy_lots": 3000,
            "sell_lots": 0,
            "cash_repay_lots": 0,
            "prev_balance_lots": 0,
            "balance_lots": 3000,
            "quota_lots": 10000,
            "typical_price": 100.0,
        },
        {
            "buy_lots": 2000,
            "sell_lots": 1000,
            "cash_repay_lots": 0,
            "prev_balance_lots": 3000,
            "balance_lots": 4000,
            "quota_lots": 10000,
            "typical_price": 90.0,
        },
    ]
    res_multi = screener.calculate_margin_cost_and_maintenance(history_multiday, close_price=76.0)
    assert res_multi["avg_cost"] == pytest.approx(95.0, rel=1e-2)
    assert res_multi["maintenance_ratio"] == pytest.approx(133.33, rel=1e-2)
    assert res_multi["usage_ratio"] == pytest.approx(40.0, rel=1e-2)

    # 極端邊界情境 1：空陣列 []
    res_empty = screener.calculate_margin_cost_and_maintenance([], close_price=78.0)
    assert res_empty["avg_cost"] == 0.0
    assert res_empty["maintenance_ratio"] == 0.0
    assert res_empty["usage_ratio"] == 0.0

    # 極端邊界情境 2：quota_lots == 0（融資限額為 0，防範除以零）
    history_zero_quota = [
        {
            "buy_lots": 500,
            "sell_lots": 0,
            "cash_repay_lots": 0,
            "prev_balance_lots": 0,
            "balance_lots": 500,
            "quota_lots": 0,
            "typical_price": 60.0,
        }
    ]
    res_zero_quota = screener.calculate_margin_cost_and_maintenance(history_zero_quota, close_price=60.0)
    assert res_zero_quota["usage_ratio"] == 0.0
    assert res_zero_quota["avg_cost"] == pytest.approx(60.0, rel=1e-2)

    # 極端邊界情境 3：balance_lots == 0（融資全數出清，無任何餘額）
    history_zero_bal = [
        {
            "buy_lots": 0,
            "sell_lots": 500,
            "cash_repay_lots": 0,
            "prev_balance_lots": 500,
            "balance_lots": 0,
            "quota_lots": 10000,
            "typical_price": 60.0,
        }
    ]
    res_zero_bal = screener.calculate_margin_cost_and_maintenance(history_zero_bal, close_price=60.0)
    assert res_zero_bal["avg_cost"] == 0.0
    assert res_zero_bal["maintenance_ratio"] == 0.0
    assert res_zero_bal["usage_ratio"] == 0.0


# ============================================================================
# 2. 雙向狀態機分流與四大防線定價幾何驗證 (Dual State Machine & Four-Price Standard)
# ============================================================================
def test_dual_state_machine_classification():
    """
    測試 2：
    情境 A（空方斷頭預警）：
      傳入 close=75.0, open=77.0, high=77.5, low=74.5, ma20=80.0,
           inst_1d_lots=-800, inst_3d_lots=-1500, inst_5d_lots=-2500,
           margin_change_1d_pct=1.5, margin_change_3d_pct=2.5,
           usage_ratio=38.0, maintenance_ratio=134.0
      斷言:
        state_code == "SHORT_BREAKDOWN"
        tp2_price < tp1_price < addon_price < entry_price < sl_price

    情境 B（多方清洗抄底）：
      傳入 close=72.0, open=70.5, high=72.5, low=68.0, ma20=78.0,
           inst_1d_lots=1200, inst_3d_lots=1800, inst_5d_lots=500,
           margin_change_1d_pct=-4.2, margin_change_3d_pct=-6.5,
           usage_ratio=28.0, maintenance_ratio=128.0
      斷言:
        state_code == "LONG_REVERSAL"
        sl_price < entry_price < addon_price < tp1_price < tp2_price
    """
    screener = load_screener_module()
    assert hasattr(screener, "classify_dual_state_and_pricing"), "screener 缺少 classify_dual_state_and_pricing 函式"

    # ----------------------------------------------------
    # 情境 A：空方斷頭預警模式 (SHORT_BREAKDOWN)
    # ----------------------------------------------------
    stock_a = {
        "code": "2330",
        "name": "測試空方高危股",
        "close": 75.0,
        "open": 77.0,
        "high": 77.5,
        "low": 74.5,
        "ma20": 80.0,
        "inst_1d_lots": -800,
        "inst_3d_lots": -1500,
        "inst_5d_lots": -2500,
        "margin_change_1d_pct": 1.5,
        "margin_change_3d_pct": 2.5,
        "usage_ratio": 38.0,
        "maintenance_ratio": 134.0,
    }
    res_a = screener.classify_dual_state_and_pricing(stock_a)

    assert res_a["state_code"] == "SHORT_BREAKDOWN", f"預期 SHORT_BREAKDOWN, 實際得到: {res_a.get('state_code')}"
    assert "🔴" in res_a["badge"], "空方模式徽章必須包含 🔴 標記"
    assert any(term in res_a["badge"] for term in ["放空", "斷頭助跌", "做空"]), f"徽章詞義不符: {res_a['badge']}"

    # 四大防線價位擷取
    entry_a = res_a["entry_price"]
    addon_a = res_a["addon_price"]
    tp1_a = res_a["tp1_price"]
    tp2_a = res_a["tp2_price"]
    sl_a = res_a["sl_price"]

    # 斷言幾何順序：tp2_price < tp1_price < addon_price < entry_price < sl_price
    assert tp2_a < tp1_a < addon_a < entry_a < sl_a, (
        f"空方價位幾何順序錯誤: TP2={tp2_a}, TP1={tp1_a}, Addon={addon_a}, Entry={entry_a}, SL={sl_a}"
    )

    # 檢驗空方定價精算符合規格書公式:
    # entry_price == 75.0
    # addon_price == min(low * 0.995, close * 0.975) = min(74.13, 73.13) = 73.13
    # tp1_price == round(close * 0.92, 2) = 69.00
    # tp2_price == round(close * 0.85, 2) = 63.75
    # sl_price > entry_price (站回 20MA 或 +3.5%)
    assert entry_a == 75.0
    assert addon_a == pytest.approx(73.13, abs=0.05)
    assert tp1_a == pytest.approx(69.0, abs=0.05)
    assert tp2_a == pytest.approx(63.75, abs=0.05)
    assert sl_a > entry_a

    # ----------------------------------------------------
    # 情境 B：多方清洗抄底模式 (LONG_REVERSAL)
    # ----------------------------------------------------
    stock_b = {
        "code": "2454",
        "name": "測試多方抄底股",
        "close": 72.0,
        "open": 70.5,
        "high": 72.5,
        "low": 68.0,
        "ma20": 78.0,
        "inst_1d_lots": 1200,
        "inst_3d_lots": 1800,
        "inst_5d_lots": 500,
        "margin_change_1d_pct": -4.2,
        "margin_change_3d_pct": -6.5,
        "usage_ratio": 28.0,
        "maintenance_ratio": 128.0,
    }
    res_b = screener.classify_dual_state_and_pricing(stock_b)

    assert res_b["state_code"] == "LONG_REVERSAL", f"預期 LONG_REVERSAL, 實際得到: {res_b.get('state_code')}"
    assert "🟢" in res_b["badge"], "多方模式徽章必須包含 🟢 標記"
    assert any(term in res_b["badge"] for term in ["抄底", "浮額清洗", "多方"]), f"徽章詞義不符: {res_b['badge']}"

    # 四大防線價位擷取
    entry_b = res_b["entry_price"]
    addon_b = res_b["addon_price"]
    tp1_b = res_b["tp1_price"]
    tp2_b = res_b["tp2_price"]
    sl_b = res_b["sl_price"]

    # 斷言幾何順序：sl_price < entry_price < addon_price < tp1_price < tp2_price
    assert sl_b < entry_b < addon_b < tp1_b < tp2_b, (
        f"多方價位幾何順序錯誤: SL={sl_b}, Entry={entry_b}, Addon={addon_b}, TP1={tp1_b}, TP2={tp2_b}"
    )

    # 檢驗多方定價精算符合規格書公式:
    # entry_price == 72.0
    # addon_price == max(high * 1.01, close * 1.025) = max(73.23, 73.80) = 73.80
    # tp1_price == max(ma20, close * 1.07) = max(78.0, 77.04) = 78.00
    # tp2_price == round(close * 1.14, 2) = 82.08
    # sl_price == min(low * 0.985, close * 0.96) = min(66.98, 69.12) = 66.98
    assert entry_b == 72.0
    assert addon_b == pytest.approx(73.80, abs=0.05)
    assert tp1_b == pytest.approx(78.00, abs=0.05)
    assert tp2_b == pytest.approx(82.08, abs=0.05)
    assert sl_b == pytest.approx(66.98, abs=0.05)


# ============================================================================
# 3. 4+1 硬性濾網與大盤多頭 Tier-2 降級容錯驗證 (Hard Filters & Tier-2 Fallback)
# ============================================================================
def test_hard_filters_and_tier2_fallback():
    """
    測試 3：
    當輸入候選池中所有標的維持率皆在 148.0 ~ 156.0（無任何標的 <= 142.0），
    調用 screener.apply_screening_with_fallback，
    斷言:
      1. 自動降級回傳 tier_mode == "TIER2_RELATIVE_WARNING"
      2. 篩出相對高危標的（非空列表）
      3. 結果按維持率由低至高嚴格排序（最危急脆弱者優先）
    """
    screener = load_screener_module()
    assert hasattr(screener, "apply_screening_with_fallback"), "screener 缺少 apply_screening_with_fallback 函式"

    # 模擬多頭行情候選池：市場熱絡，維持率皆高於 Tier-1 絕對死線 142.0%
    # 但部分個股使用率高 (>=18%)，維持率處於全市場相對脆弱底層 (148% ~ 156%)
    candidates_raw = [
        {
            "code": "1101",
            "name": "候選股A",
            "close": 35.0,
            "open": 35.5,
            "high": 35.8,
            "low": 34.6,
            "ma20": 36.5,
            "volume_today": 1500,
            "volume_5d_avg": 2000,
            "usage_ratio": 24.0,
            "maintenance_ratio": 155.0,
            "balance_lots": 2500,
            "margin_change_1d_pct": 1.0,
            "margin_change_3d_pct": 2.0,
            "inst_1d_lots": -500,
            "inst_3d_lots": -1200,
            "inst_5d_lots": -2500,
        },
        {
            "code": "2303",
            "name": "候選股B(最脆弱)",
            "close": 48.0,
            "open": 48.5,
            "high": 48.8,
            "low": 47.5,
            "ma20": 51.0,
            "volume_today": 2500,
            "volume_5d_avg": 3000,
            "usage_ratio": 28.0,
            "maintenance_ratio": 148.5,  # 維持率最低
            "balance_lots": 4500,
            "margin_change_1d_pct": 2.5,
            "margin_change_3d_pct": 3.8,
            "inst_1d_lots": -800,
            "inst_3d_lots": -1800,
            "inst_5d_lots": -3500,
        },
        {
            "code": "2603",
            "name": "候選股C(次脆弱)",
            "close": 175.0,
            "open": 176.5,
            "high": 177.0,
            "low": 173.0,
            "ma20": 182.0,
            "volume_today": 1200,
            "volume_5d_avg": 1800,
            "usage_ratio": 22.0,
            "maintenance_ratio": 151.0,  # 維持率次低
            "balance_lots": 2000,
            "margin_change_1d_pct": 0.8,
            "margin_change_3d_pct": 1.5,
            "inst_1d_lots": -400,
            "inst_3d_lots": -900,
            "inst_5d_lots": -1800,
        },
        {
            "code": "3481",
            "name": "候選股D(中等)",
            "close": 14.5,
            "open": 14.8,
            "high": 14.9,
            "low": 14.3,
            "ma20": 15.2,
            "volume_today": 4000,
            "volume_5d_avg": 5000,
            "usage_ratio": 25.0,
            "maintenance_ratio": 153.2,
            "balance_lots": 3500,
            "margin_change_1d_pct": 1.2,
            "margin_change_3d_pct": 2.1,
            "inst_1d_lots": -600,
            "inst_3d_lots": -1500,
            "inst_5d_lots": -2800,
        },
    ]

    filtered_list, tier_mode = screener.apply_screening_with_fallback(candidates_raw)

    # 1. 斷言觸發 Tier-2 降級機制
    assert tier_mode == "TIER2_RELATIVE_WARNING", f"預期 TIER2_RELATIVE_WARNING, 實際得到: {tier_mode}"

    # 2. 斷言非空名單
    assert len(filtered_list) > 0, "Tier-2 降級機制應篩出相對高危標的，不可為空名單"

    # 3. 斷言排序：維持率由低至高嚴格遞增排序
    mr_list = [c["maintenance_ratio"] for c in filtered_list]
    for i in range(len(mr_list) - 1):
        assert mr_list[i] <= mr_list[i + 1], f"候選清單未按維持率由低至高排序: {mr_list}"

    # 第一檔標的必須是最脆弱的 2303 (148.5%)
    assert filtered_list[0]["code"] == "2303", f"第一檔應為維持率最低的 2303, 實際為: {filtered_list[0]['code']}"


# ============================================================================
# 4. 盤中雷達雙向到價觸發與防毛刺機制驗證 (Intraday Scanner & Tick Guard)
# ============================================================================
def test_intraday_scanner_dual_direction_triggers():
    """
    測試 4：
    測試 intraday_scanner.evaluate_intraday_alert 雙向到價判定與防毛刺：
    1. SHORT_BREAKDOWN 標的：
       - 當即時價 price >= sl_price * 1.015 時觸發 STOP_LOSS
       - 當 price <= tp1_price 時，配合 use_tick_guard 驗證：
         第 1 次觸碰阻擋攔截，第 2 次確認放行觸發 TP1
    2. LONG_REVERSAL 標的：
       - 當即時價 price <= sl_price * 0.985 時觸發 STOP_LOSS
       - 當 price >= addon_price 時，配合 use_tick_guard 驗證第 2 次確認觸發 ADDON
    """
    scanner = load_scanner_module()
    assert hasattr(scanner, "evaluate_intraday_alert"), "scanner 缺少 evaluate_intraday_alert 函式"

    # ----------------------------------------------------
    # 空方標的 (SHORT_BREAKDOWN)
    # ----------------------------------------------------
    short_target = {
        "code": "8888",
        "name": "空方測試標的",
        "state_code": "SHORT_BREAKDOWN",
        "entry_price": 75.0,
        "addon_price": 73.0,
        "tp1_price": 69.0,
        "tp2_price": 64.0,
        "sl_price": 78.0,
    }
    alerted_set_short = set()

    # 1. 空方停損驗證：空單在價格強彈時停損 (price >= sl_price * 1.015 = 78.0 * 1.015 = 79.17)
    rt_short_sl = {"price": 79.5, "change_pct": 6.0, "volume": 2000}
    GLOBAL_TICK_GUARD.reset_target("8888")
    event_type, event_title, event_desc = scanner.evaluate_intraday_alert(
        short_target, rt_short_sl, alerted_set_short, use_tick_guard=False
    )
    assert event_type == "STOP_LOSS", f"空方急拉應觸發 STOP_LOSS, 實際: {event_type}"
    assert any(term in event_title for term in ["停損", "STOP_LOSS", "風控"]), f"標題缺少停損關鍵詞: {event_title}"

    # 2. 空方停利 TP1 驗證與防毛刺兩段確認 (price <= tp1_price = 69.0)
    rt_short_tp1 = {"price": 68.8, "change_pct": -8.2, "volume": 2500}
    GLOBAL_TICK_GUARD.reset_target("8888")
    alerted_set_short.clear()

    # (a) 不使用防毛刺 (use_tick_guard=False) 直接放行
    ev_direct, _, _ = scanner.evaluate_intraday_alert(
        short_target, rt_short_tp1, alerted_set_short, use_tick_guard=False
    )
    assert ev_direct == "TP1", f"關閉防毛刺時應直接觸發 TP1, 實際: {ev_direct}"

    # (b) 啟用防毛刺 (use_tick_guard=True): 第 1 次觸碰應被攔截，第 2 次確認才放行
    GLOBAL_TICK_GUARD.reset_target("8888")
    alerted_set_short.clear()

    ev_tick1, _, _ = scanner.evaluate_intraday_alert(
        short_target, rt_short_tp1, alerted_set_short, use_tick_guard=True
    )
    assert ev_tick1 is None, "第 1 次觸碰防毛刺機制必須攔截，不可立即觸發"

    ev_tick2, title_tick2, _ = scanner.evaluate_intraday_alert(
        short_target, rt_short_tp1, alerted_set_short, use_tick_guard=True
    )
    assert ev_tick2 == "TP1", f"第 2 次確認應順利放行 TP1, 實際: {ev_tick2}"
    assert "TP1" in title_tick2 or "停利" in title_tick2

    # ----------------------------------------------------
    # 多方標的 (LONG_REVERSAL)
    # ----------------------------------------------------
    long_target = {
        "code": "7777",
        "name": "多方測試標的",
        "state_code": "LONG_REVERSAL",
        "entry_price": 72.0,
        "addon_price": 73.8,
        "tp1_price": 78.0,
        "tp2_price": 82.0,
        "sl_price": 67.0,
    }
    alerted_set_long = set()

    # 1. 多方停損驗證：多單在破底跌穿時停損 (price <= sl_price * 0.985 = 67.0 * 0.985 = 65.99)
    rt_long_sl = {"price": 65.5, "change_pct": -9.0, "volume": 3000}
    GLOBAL_TICK_GUARD.reset_target("7777")
    event_type_long, title_long_sl, _ = scanner.evaluate_intraday_alert(
        long_target, rt_long_sl, alerted_set_long, use_tick_guard=False
    )
    assert event_type_long == "STOP_LOSS", f"多方破底應觸發 STOP_LOSS, 實際: {event_type_long}"

    # 2. 多方加碼 ADDON 驗證與防毛刺兩段確認 (price >= addon_price = 73.8)
    rt_long_addon = {"price": 74.2, "change_pct": 3.0, "volume": 3500}
    GLOBAL_TICK_GUARD.reset_target("7777")
    alerted_set_long.clear()

    # 第 1 次觸碰攔截
    ev_long_tick1, _, _ = scanner.evaluate_intraday_alert(
        long_target, rt_long_addon, alerted_set_long, use_tick_guard=True
    )
    assert ev_long_tick1 is None, "多方第 1 次觸碰 ADDON 必須被防毛刺攔截"

    # 第 2 次確認放行
    ev_long_tick2, title_long_addon, _ = scanner.evaluate_intraday_alert(
        long_target, rt_long_addon, alerted_set_long, use_tick_guard=True
    )
    assert ev_long_tick2 == "ADDON", f"多方第 2 次確認應順利放行 ADDON, 實際: {ev_long_tick2}"
    assert any(term in title_long_addon for term in ["加碼", "ADDON", "轉強"])


# ============================================================================
# 5. 全域調度器與系統註冊完整性驗證 (System Integration & Registration)
# ============================================================================
def test_visual_cards_and_system_registration():
    """
    測試 5：
    驗證總指揮中心全域調度器與通訊模組皆已完整掛載策略 07：
    1. run_all.STRATEGIES 包含 07_margin_call_ambush
    2. run_all_intraday.INTRADAY_STRATEGIES 包含 07_margin_call_ambush
    3. line_sender.resolve_strategy_meta("07") 正確解析元數據與專屬配色
    4. line_bot_server.py 包含 07_margin_call_ambush
    5. screener.CSV_COLUMNS 包含完整的四大防線與融資指標欄位
    """
    # 1. run_all.py 註冊檢驗
    import run_all
    strat_07 = next((s for s in run_all.STRATEGIES if s["id"] == "07_margin_call_ambush"), None)
    assert strat_07 is not None, "run_all.STRATEGIES 未註冊 07_margin_call_ambush"
    assert any(term in strat_07["name"] for term in ["融資", "斷頭"]), f"策略名稱未包含核心特徵: {strat_07['name']}"
    assert Path(strat_07["script"]).name == "screener.py", f"腳本路徑錯誤: {strat_07['script']}"

    # 2. run_all_intraday.py 註冊檢驗
    import run_all_intraday
    intra_07 = next((s for s in run_all_intraday.INTRADAY_STRATEGIES if s["id"] == "07_margin_call_ambush"), None)
    assert intra_07 is not None, "run_all_intraday.INTRADAY_STRATEGIES 未註冊 07_margin_call_ambush"
    assert Path(intra_07["script"]).name == "intraday_scanner.py", f"盤中雷達腳本路徑錯誤: {intra_07['script']}"

    # 3. line_sender.py 元數據解析檢驗
    from line_sender import resolve_strategy_meta
    meta_07 = resolve_strategy_meta("07")
    assert meta_07["id"] == "07", f"預期 id 07, 實際: {meta_07.get('id')}"
    assert any(term in meta_07["name"] for term in ["融資", "斷頭"]), f"元數據名稱錯誤: {meta_07['name']}"
    assert "color_theme" in meta_07 and meta_07["color_theme"].startswith("#"), f"色系未配置: {meta_07.get('color_theme')}"
    assert "entry_label" in meta_07 and meta_07["entry_label"], "缺少 entry_label"
    assert "addon_label" in meta_07 and meta_07["addon_label"], "缺少 addon_label"
    assert "tp_label" in meta_07 and meta_07["tp_label"], "缺少 tp_label"
    assert "sl_label" in meta_07 and meta_07["sl_label"], "缺少 sl_label"

    # 4. line_bot_server.py 靜態與路由整合檢驗
    server_path = ROOT_DIR / "line_bot_server.py"
    assert server_path.exists(), "line_bot_server.py 檔案不存在"
    server_content = server_path.read_text(encoding="utf-8")
    assert "07_margin_call_ambush" in server_content, "line_bot_server.py 未包含 07_margin_call_ambush 策略註冊"

    # 5. CSV 規格合約欄位完整性檢驗
    screener = load_screener_module()
    assert hasattr(screener, "CSV_COLUMNS"), "screener 模組必須定義 CSV_COLUMNS 列表"
    required_cols = [
        "證券代號", "證券名稱", "收盤價", "多空狀態", "狀態代碼", "門檻層級",
        "融資使用率%", "融資維持率%", "預估融資均價", "融資餘額(張)",
        "進場價位", "加碼價位", "停利TP1", "停利TP2", "停損價位",
        "評分", "星級推薦"
    ]
    for col in required_cols:
        assert col in screener.CSV_COLUMNS, f"screener.CSV_COLUMNS 缺少必要欄位: {col}"
