#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 08【台股集團作帳與母子聯動伏擊策略】自動化驗收測試合約 (pytest)
================================================================================
本測試嚴格依據《台股量化選股策略最高總指揮中心》全域規範與 docs/api-spec.json 制定：
- 機械客觀真實驗證，絕不使用過度假 Mock 或必過空測試
- 檢驗真實演算法核心：
  1. 25 大核心集團成分股字典與季底/年底作帳日曆節氣自動判定 (Calendar Engine)
  2. 同集團轉強共振度、集團共振率%精算、兄弟股清單與母子位階強弱配對 (ZeroDivisionError 邊界防呆)
  3. 雙向狀態機分流 (GROUP_IGNITION vs. GROUP_UNWIND) 與四大防線定價幾何不變量
  4. 4+1 五維度硬性濾網、乖離率過大防追高與 Tier-2 Fallback 降級容錯與評分排序
  5. 盤中雷達雙向到價觸發與 GLOBAL_TICK_GUARD 防毛刺雙重確認
  6. 總指揮中心全域調度器 (run_all, run_all_intraday, line_sender, line_bot_server, web/index.html, optimize_all) 系統註冊掛載
================================================================================
"""

import os
import sys
import datetime
import importlib.util
from pathlib import Path
import pytest

# 將專案根目錄加入 sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from algo_guards import GLOBAL_TICK_GUARD


def load_screener_module():
    """動態載入策略 08 盤後選股模組"""
    screener_path = ROOT_DIR / "strategies" / "08_group_window_dressing" / "screener.py"
    if not screener_path.exists():
        pytest.fail(f"策略 08 盤後選股模組尚未建立: {screener_path} (請確認後端模組已生成)")
    spec = importlib.util.spec_from_file_location("screener_08", screener_path)
    if spec is None or spec.loader is None:
        pytest.fail(f"無法載入模組規格: {screener_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_scanner_module():
    """動態載入策略 08 盤中雷達模組"""
    scanner_path = ROOT_DIR / "strategies" / "08_group_window_dressing" / "intraday_scanner.py"
    if not scanner_path.exists():
        pytest.fail(f"策略 08 盤中雷達模組尚未建立: {scanner_path} (請確認後端模組已生成)")
    spec = importlib.util.spec_from_file_location("scanner_08", scanner_path)
    if spec is None or spec.loader is None:
        pytest.fail(f"無法載入模組規格: {scanner_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================================
# 1. 集團成分股字典與作帳日曆節氣判定引擎 (Group Pool & Calendar Engine)
# ============================================================================
def test_group_pool_and_calendar_engine():
    """
    測試 1：
    1. 驗證 screener.load_group_pool()：
       - 至少載入 >= 20 個集團 (len(pool) >= 20)
       - 總成分股數 >= 120 檔 (sum(len(g['members'])) >= 120)
       - 每集團皆含 group_id, group_name, flagship_code, members
       - 每個 member 包含 code, name, role ('FLAGSHIP' | 'SUBSIDIARY')
       - 驗證傳入自訂 pool_path 亦能正確載入
    2. 驗證 screener.evaluate_window_dressing_calendar(target_date)：
       - datetime.date(2026, 11, 25) -> phase_code == "Q4_YEAR_END_PEAK", season_bonus == 15, unwind_bonus == 0
       - datetime.date(2026, 6, 15)  -> phase_code == "QUARTER_END_SPRINT", season_bonus == 10, unwind_bonus == 0
       - datetime.date(2026, 9, 26)  -> phase_code == "UNWIND_WARNING_ZONE", unwind_bonus == 12, season_bonus == 5
       - datetime.date(2026, 4, 10)  -> phase_code == "NORMAL_ROTATION", season_bonus == 5, unwind_bonus == 0
       - 預設 target_date=None 傳入回傳合法結構 (包含 phase_code, phase_badge, season_bonus, unwind_bonus)
    """
    screener = load_screener_module()
    assert hasattr(screener, "load_group_pool"), "screener 缺少 load_group_pool 函式"
    assert hasattr(screener, "evaluate_window_dressing_calendar"), "screener 缺少 evaluate_window_dressing_calendar 函式"

    # 1. 檢驗集團成分股池載入
    pool = screener.load_group_pool()
    assert isinstance(pool, list), "load_group_pool() 回傳型別必須為 list"
    assert len(pool) >= 20, f"集團總數應 >= 20 個，實際載入: {len(pool)}"

    total_members = sum(len(g.get("members", [])) for g in pool)
    assert total_members >= 120, f"總成分股數應 >= 120 檔，實際成分股數: {total_members}"

    for g in pool:
        assert "group_id" in g and g["group_id"], f"集團缺少 group_id: {g}"
        assert "group_name" in g and g["group_name"], f"集團缺少 group_name: {g}"
        assert "flagship_code" in g and g["flagship_code"], f"集團缺少 flagship_code: {g}"
        assert "members" in g and isinstance(g["members"], list) and len(g["members"]) > 0, (
            f"集團 members 格式不合法或為空: {g}"
        )
        for m in g["members"]:
            assert "code" in m and m["code"], f"成分股缺少 code: {m}"
            assert "name" in m and m["name"], f"成分股缺少 name: {m}"
            assert "role" in m and m["role"] in ["FLAGSHIP", "SUBSIDIARY"], f"成分股 role 不合法: {m}"

    # 檢驗傳入指定 pool_path
    default_json = ROOT_DIR / "strategies" / "08_group_window_dressing" / "group_pool.json"
    if default_json.exists():
        pool_custom = screener.load_group_pool(str(default_json))
        assert len(pool_custom) == len(pool), "傳入 pool_path 載入筆數與預設不符"

    # 2. 檢驗作帳日曆節氣判定
    # (a) 11/25 -> Q4 年底作帳主升期
    d1 = datetime.date(2026, 11, 25)
    r1 = screener.evaluate_window_dressing_calendar(d1)
    assert r1["phase_code"] == "Q4_YEAR_END_PEAK", f"2026/11/25 預期 Q4_YEAR_END_PEAK, 實際: {r1.get('phase_code')}"
    assert r1["season_bonus"] == 15, f"Q4_YEAR_END_PEAK season_bonus 應為 15, 實際: {r1.get('season_bonus')}"
    assert r1.get("unwind_bonus", 0) == 0, f"Q4_YEAR_END_PEAK unwind_bonus 應為 0, 實際: {r1.get('unwind_bonus')}"
    assert any(term in r1["phase_badge"] for term in ["Q4", "年底", "主升"]), f"徽章未包含 Q4 年底特徵: {r1['phase_badge']}"

    # (b) 06/15 -> 季底作帳衝刺期
    d2 = datetime.date(2026, 6, 15)
    r2 = screener.evaluate_window_dressing_calendar(d2)
    assert r2["phase_code"] == "QUARTER_END_SPRINT", f"2026/06/15 預期 QUARTER_END_SPRINT, 實際: {r2.get('phase_code')}"
    assert r2["season_bonus"] == 10, f"QUARTER_END_SPRINT season_bonus 應為 10, 實際: {r2.get('season_bonus')}"
    assert r2.get("unwind_bonus", 0) == 0, f"QUARTER_END_SPRINT unwind_bonus 應為 0, 實際: {r2.get('unwind_bonus')}"
    assert any(term in r2["phase_badge"] for term in ["季底", "衝刺"]), f"徽章未包含季底衝刺特徵: {r2['phase_badge']}"

    # (c) 09/26 -> 季末/年末結帳警戒期
    d3 = datetime.date(2026, 9, 26)
    r3 = screener.evaluate_window_dressing_calendar(d3)
    assert r3["phase_code"] == "UNWIND_WARNING_ZONE", f"2026/09/26 預期 UNWIND_WARNING_ZONE, 實際: {r3.get('phase_code')}"
    assert r3["unwind_bonus"] == 12, f"UNWIND_WARNING_ZONE unwind_bonus 應為 12, 實際: {r3.get('unwind_bonus')}"
    assert r3.get("season_bonus", 5) == 5, f"UNWIND_WARNING_ZONE season_bonus 應為 5, 實際: {r3.get('season_bonus')}"
    assert any(term in r3["phase_badge"] for term in ["結帳", "警戒"]), f"徽章未包含結帳警戒特徵: {r3['phase_badge']}"

    # (d) 04/10 -> 常態輪動觀察期
    d4 = datetime.date(2026, 4, 10)
    r4 = screener.evaluate_window_dressing_calendar(d4)
    assert r4["phase_code"] == "NORMAL_ROTATION", f"2026/04/10 預期 NORMAL_ROTATION, 實際: {r4.get('phase_code')}"
    assert r4["season_bonus"] == 5, f"NORMAL_ROTATION season_bonus 應為 5, 實際: {r4.get('season_bonus')}"
    assert r4.get("unwind_bonus", 0) == 0, f"NORMAL_ROTATION unwind_bonus 應為 0, 實際: {r4.get('unwind_bonus')}"
    assert any(term in r4["phase_badge"] for term in ["非季底", "輪動", "觀察"]), f"徽章未包含非季底輪動特徵: {r4['phase_badge']}"

    # (e) 預設參數 target_date=None
    r_default = screener.evaluate_window_dressing_calendar()
    assert isinstance(r_default, dict), "evaluate_window_dressing_calendar() 回傳必須為 dict"
    for k in ["phase_code", "phase_badge", "season_bonus", "unwind_bonus"]:
        assert k in r_default, f"預設回傳缺少欄位 {k}"


# ============================================================================
# 2. 同集團共振度精算與母子位階強弱配對 (Group Resonance & Hierarchy Calculation)
# ============================================================================
def test_group_resonance_and_hierarchy_calculation():
    """
    測試 2：
    1. 構造華新麗華集團 3 檔成分股測試資料：
       - 母公司 1605 華新 (flagship_code="1605", close=40.0, ma20=38.0, ret_5d_pct=6.5, inst_1d_lots=1500, inst_5d_lots=3500)
       - 子公司 2344 華邦電 (flagship_code="1605", close=28.0, ma20=27.2, ret_5d_pct=2.0, inst_1d_lots=800, inst_5d_lots=1200)
       - 子公司 6191 精成科 (flagship_code="1605", close=65.0, ma20=63.5, ret_5d_pct=1.5, inst_1d_lots=450, inst_5d_lots=900)
    2. 呼叫 screener.calculate_group_resonance_and_hierarchy(records)：
       - 斷言 3 檔之 group_total_count == 3, group_active_count == 3, group_resonance_ratio == pytest.approx(100.0, rel=1e-2)
       - 斷言 1605 之 hierarchy_role 包含 "領頭羊" (👑【集團領頭羊主攻】)
       - 斷言 2344 與 6191 之 hierarchy_role 包含 "補漲" (🚀【母強子落後補漲】)，且 spread_vs_parent_pct < 0
       - 斷言 parent_ret_5d_pct == 6.5
       - 斷言 sibling_leaders 包含同集團轉強夥伴清單
    3. 測試極端邊界：
       - 傳入空陣列 [] 安全回傳 [] 且絕不拋出 ZeroDivisionError
       - 全數未轉強個股 (group_active_count == 0)，安全回傳 group_resonance_ratio == 0.0
       - 孤立成分股 (無母公司或僅 1 檔)，安全計算且不崩潰
    """
    screener = load_screener_module()
    assert hasattr(screener, "calculate_group_resonance_and_hierarchy"), (
        "screener 缺少 calculate_group_resonance_and_hierarchy 函式"
    )

    records = [
        {
            "code": "1605",
            "name": "華新",
            "group_id": "G01_WALSIN",
            "group_name": "華新麗華集團",
            "flagship_code": "1605",
            "flagship_name": "華新",
            "role": "FLAGSHIP",
            "close": 40.0,
            "ma20": 38.0,
            "ret_5d_pct": 6.5,
            "inst_1d_lots": 1500,
            "inst_5d_lots": 3500,
        },
        {
            "code": "2344",
            "name": "華邦電",
            "group_id": "G01_WALSIN",
            "group_name": "華新麗華集團",
            "flagship_code": "1605",
            "flagship_name": "華新",
            "role": "SUBSIDIARY",
            "close": 28.0,
            "ma20": 27.2,
            "ret_5d_pct": 2.0,
            "inst_1d_lots": 800,
            "inst_5d_lots": 1200,
        },
        {
            "code": "6191",
            "name": "精成科",
            "group_id": "G01_WALSIN",
            "group_name": "華新麗華集團",
            "flagship_code": "1605",
            "flagship_name": "華新",
            "role": "SUBSIDIARY",
            "close": 65.0,
            "ma20": 63.5,
            "ret_5d_pct": 1.5,
            "inst_1d_lots": 450,
            "inst_5d_lots": 900,
        },
    ]

    res = screener.calculate_group_resonance_and_hierarchy(records)
    assert isinstance(res, list), "回傳值必須為 list"
    assert len(res) == 3, f"回傳數量應為 3，實際為: {len(res)}"

    # 驗證共振家數與比例
    for r in res:
        assert r["group_total_count"] == 3, f"group_total_count 應為 3, 實際: {r.get('group_total_count')}"
        assert r["group_active_count"] == 3, f"group_active_count 應為 3, 實際: {r.get('group_active_count')}"
        assert r["group_resonance_ratio"] == pytest.approx(100.0, rel=1e-2), (
            f"共振率應為 100.0%, 實際: {r.get('group_resonance_ratio')}"
        )

    # 檢驗母公司 1605
    rec_1605 = next(x for x in res if x["code"] == "1605")
    assert "領頭羊" in rec_1605["hierarchy_role"], f"1605 應為集團領頭羊，實際: {rec_1605['hierarchy_role']}"
    assert any(icon in rec_1605["hierarchy_role"] for icon in ["👑", "主攻"]), (
        f"1605 角色標記未符合規格: {rec_1605['hierarchy_role']}"
    )
    assert rec_1605["parent_ret_5d_pct"] == pytest.approx(6.5, abs=0.01)
    assert rec_1605["spread_vs_parent_pct"] == pytest.approx(0.0, abs=0.01)
    assert isinstance(rec_1605["sibling_leaders"], list), "sibling_leaders 必須為 list"

    # 檢驗子公司 2344
    rec_2344 = next(x for x in res if x["code"] == "2344")
    assert "補漲" in rec_2344["hierarchy_role"], f"2344 應為落後補漲，實際: {rec_2344['hierarchy_role']}"
    assert any(icon in rec_2344["hierarchy_role"] for icon in ["🚀", "落後"]), (
        f"2344 角色標記未符合規格: {rec_2344['hierarchy_role']}"
    )
    assert rec_2344["spread_vs_parent_pct"] < 0, f"子公司漲幅低於母公司，spread 應為負值，實際: {rec_2344['spread_vs_parent_pct']}"
    assert rec_2344["spread_vs_parent_pct"] == pytest.approx(2.0 - 6.5, abs=0.01)
    assert rec_2344["parent_ret_5d_pct"] == pytest.approx(6.5, abs=0.01)

    # 檢驗子公司 6191
    rec_6191 = next(x for x in res if x["code"] == "6191")
    assert "補漲" in rec_6191["hierarchy_role"], f"6191 應為落後補漲，實際: {rec_6191['hierarchy_role']}"
    assert any(icon in rec_6191["hierarchy_role"] for icon in ["🚀", "落後"]), (
        f"6191 角色標記未符合規格: {rec_6191['hierarchy_role']}"
    )
    assert rec_6191["spread_vs_parent_pct"] < 0, f"子公司漲幅低於母公司，spread 應為負值，實際: {rec_6191['spread_vs_parent_pct']}"
    assert rec_6191["spread_vs_parent_pct"] == pytest.approx(1.5 - 6.5, abs=0.01)

    # 3. 極端邊界情境驗證
    # (a) 空陣列 [] 傳入
    empty_res = screener.calculate_group_resonance_and_hierarchy([])
    assert empty_res == [], f"傳入空陣列應回傳空陣列，實際: {empty_res}"

    # (b) 同集團成員皆未轉強 (active_count == 0)，檢驗 ZeroDivisionError 防護
    inactive_records = [
        {
            "code": "9991",
            "name": "休眠母股",
            "group_id": "G99_SLEEP",
            "group_name": "休眠集團",
            "flagship_code": "9991",
            "role": "FLAGSHIP",
            "close": 20.0,
            "ma20": 25.0,  # close < ma20
            "ret_5d_pct": -3.0,
            "inst_1d_lots": -200,
            "inst_5d_lots": -500,
        },
        {
            "code": "9992",
            "name": "休眠子股",
            "group_id": "G99_SLEEP",
            "group_name": "休眠集團",
            "flagship_code": "9991",
            "role": "SUBSIDIARY",
            "close": 15.0,
            "ma20": 18.0,
            "ret_5d_pct": -4.0,
            "inst_1d_lots": -100,
            "inst_5d_lots": -300,
        },
    ]
    res_inact = screener.calculate_group_resonance_and_hierarchy(inactive_records)
    assert len(res_inact) == 2
    for r in res_inact:
        assert r["group_total_count"] == 2
        assert r["group_active_count"] == 0
        assert r["group_resonance_ratio"] == 0.0, "無轉強個股時共振率應安全回傳 0.0"


# ============================================================================
# 3. 雙向狀態機分流與四大防線定價幾何不變量 (Dual State Machine & Pricing Geometry)
# ============================================================================
def test_dual_state_and_four_price_geometry():
    """
    測試 3：
    1. 測試多方 GROUP_IGNITION（作帳點火・母子共振）：
       條件：站上 20MA、法人買超 >= 200 張、收紅K且上影線 < 45%
       斷言：
         - res["state_code"] == "GROUP_IGNITION"
         - res["badge"] 包含 "🟢" 與作帳/共振標記
         - 嚴格四大防線幾何順序：sl_price < entry_price < addon_price < tp1_price < tp2_price
    2. 測試空方 GROUP_UNWIND（集團結帳・順勢放空）：
       條件：跌破 20MA、法人賣超 <= -300 張、收黑K
       斷言：
         - res["state_code"] == "GROUP_UNWIND"
         - res["badge"] 包含 "🔴" 與結帳/放空標記
         - 嚴格四大防線幾何順序：tp2_price < tp1_price < addon_price < entry_price < sl_price
    """
    screener = load_screener_module()
    assert hasattr(screener, "classify_dual_state_and_pricing"), (
        "screener 缺少 classify_dual_state_and_pricing 函式"
    )

    # 1. 多方情境：GROUP_IGNITION
    stock_long = {
        "code": "1605",
        "name": "華新",
        "close": 42.0,
        "open": 40.5,
        "high": 42.5,
        "low": 40.2,
        "ma20": 39.5,
        "inst_1d_lots": 1500,
        "inst_5d_lots": 3500,
        "ret_5d_pct": 5.2,
        "upper_shadow_pct": 20.0,
        "lower_shadow_pct": 10.0,
    }
    res_long = screener.classify_dual_state_and_pricing(stock_long)

    assert res_long["state_code"] == "GROUP_IGNITION", (
        f"多方條件預期 GROUP_IGNITION, 實際: {res_long.get('state_code')}"
    )
    assert "🟢" in res_long["badge"], f"多方徽章缺少 🟢: {res_long.get('badge')}"
    assert any(term in res_long["badge"] for term in ["作帳", "點火", "共振"]), (
        f"徽章詞義未符合多方作帳共振: {res_long.get('badge')}"
    )

    # 擷取多方四大防線價位
    sl_long = res_long["sl_price"]
    entry_long = res_long["entry_price"]
    addon_long = res_long["addon_price"]
    tp1_long = res_long["tp1_price"]
    tp2_long = res_long["tp2_price"]

    # 斷言多方幾何順序：sl_price < entry_price < addon_price < tp1_price < tp2_price
    assert sl_long < entry_long < addon_long < tp1_long < tp2_long, (
        f"多方四大防線幾何順序錯誤: SL={sl_long}, Entry={entry_long}, "
        f"Addon={addon_long}, TP1={tp1_long}, TP2={tp2_long}"
    )

    # 2. 空方情境：GROUP_UNWIND
    stock_short = {
        "code": "2344",
        "name": "華邦電",
        "close": 24.5,
        "open": 25.8,
        "high": 26.0,
        "low": 24.2,
        "ma20": 26.5,
        "inst_1d_lots": -500,
        "inst_5d_lots": -1200,
        "ret_5d_pct": -4.0,
        "upper_shadow_pct": 10.0,
        "lower_shadow_pct": 10.0,
    }
    res_short = screener.classify_dual_state_and_pricing(stock_short)

    assert res_short["state_code"] == "GROUP_UNWIND", (
        f"空方條件預期 GROUP_UNWIND, 實際: {res_short.get('state_code')}"
    )
    assert "🔴" in res_short["badge"], f"空方徽章缺少 🔴: {res_short.get('badge')}"
    assert any(term in res_short["badge"] for term in ["結帳", "放空", "順勢"]), (
        f"徽章詞義未符合空方結帳放空: {res_short.get('badge')}"
    )

    # 擷取空方四大防線價位
    sl_short = res_short["sl_price"]
    entry_short = res_short["entry_price"]
    addon_short = res_short["addon_price"]
    tp1_short = res_short["tp1_price"]
    tp2_short = res_short["tp2_price"]

    # 斷言空方幾何順序：tp2_price < tp1_price < addon_price < entry_price < sl_price
    assert tp2_short < tp1_short < addon_short < entry_short < sl_short, (
        f"空方四大防線幾何順序錯誤: TP2={tp2_short}, TP1={tp1_short}, "
        f"Addon={addon_short}, Entry={entry_short}, SL={sl_short}"
    )


# ============================================================================
# 4. 4+1 五維度硬性濾網與 Tier-2 降級容錯 (Hard Filters & Tier-2 Fallback)
# ============================================================================
def test_screening_4plus1_and_tier2_fallback():
    """
    測試 4：
    1. 測試 Tier-1 正常通過與乖離率過大被剔除：
       - 標的 1 (滿足 Tier-1: avg_vol_5d=1500 >= 1000, bias20_pct=4.2% <= 8.0%, group_active_count=3 >= 2) -> 通過
       - 標的 2 (乖離率過大: bias20_pct=11.5% > 8.0%) -> 剔除
       - 斷言 tier_mode == "TIER1_RESONANCE"
    2. 測試當無標的滿足 Tier-1 時的 Fallback 機制：
       - 輸入標的之均量不足 1000 張 (例如 avg_vol_5d=750) 或共振家數不足 (group_active_count=1)，
         但滿足 Tier-2 門檻 (avg_vol_5d >= 600, abs(bias20_pct) <= 10.0%)
       - screener.apply_screening_with_fallback(raw_list) 自動切換至 tier_mode == "TIER2_GROUP_WATCH"
       - 斷言篩出筆數 > 0 且 <= 6 (取 Top 6 標的)
       - 斷言篩選結果按 score 由高至低嚴格排序
    """
    screener = load_screener_module()
    assert hasattr(screener, "apply_screening_with_fallback"), (
        "screener 缺少 apply_screening_with_fallback 函式"
    )

    # 1. Tier-1 正常與乖離剔除驗證
    cand_pass = {
        "code": "1605",
        "name": "華新",
        "state_code": "GROUP_IGNITION",
        "avg_vol_5d": 1500,
        "bias20_pct": 4.2,
        "group_active_count": 3,
        "group_resonance_ratio": 100.0,
        "score": 90,
    }
    cand_bias_excess = {
        "code": "2344",
        "name": "華邦電",
        "state_code": "GROUP_IGNITION",
        "avg_vol_5d": 2000,
        "bias20_pct": 11.5,  # 乖離率過大 (> 8.0%)
        "group_active_count": 3,
        "group_resonance_ratio": 100.0,
        "score": 85,
    }

    filtered_t1, tier_t1 = screener.apply_screening_with_fallback([cand_pass, cand_bias_excess])
    assert tier_t1 == "TIER1_RESONANCE", f"預期 TIER1_RESONANCE, 實際: {tier_t1}"
    assert len(filtered_t1) == 1, f"預期僅 1 檔通過 Tier-1, 實際通過: {len(filtered_t1)}"
    assert filtered_t1[0]["code"] == "1605", f"通過標的應為 1605, 實際: {filtered_t1[0]['code']}"

    # 2. Tier-2 降級容錯驗證 (無任何標的滿足 Tier-1)
    # 構造 8 檔均量 750 張 (未達 Tier-1 的 1000 張，但達 Tier-2 的 600 張) 之候選股
    scores = [72, 85, 96, 60, 88, 79, 91, 65]
    raw_tier2_candidates = [
        {
            "code": f"800{i}",
            "name": f"候選股{i}",
            "state_code": "GROUP_IGNITION",
            "avg_vol_5d": 750,
            "bias20_pct": 3.0,
            "group_active_count": 1,  # 未達 Tier-1 的 >= 2 家，但符合 Tier-2 觀察
            "group_resonance_ratio": 33.3,
            "score": s,
        }
        for i, s in enumerate(scores, start=1)
    ]

    filtered_t2, tier_t2 = screener.apply_screening_with_fallback(raw_tier2_candidates)
    assert tier_t2 == "TIER2_GROUP_WATCH", f"預期 TIER2_GROUP_WATCH, 實際: {tier_t2}"
    assert 0 < len(filtered_t2) <= 6, f"Tier-2 應取 Top 6 標的 (<= 6 檔)，實際筆數: {len(filtered_t2)}"

    # 斷言排序：按 score 由高至低嚴格遞減排序
    result_scores = [c["score"] for c in filtered_t2]
    for i in range(len(result_scores) - 1):
        assert result_scores[i] >= result_scores[i + 1], (
            f"Tier-2 候選清單未依 score 由高至低排序: {result_scores}"
        )
    # 第一名必須為得分最高者 (96 分)
    assert filtered_t2[0]["score"] == 96, f"第一名得分應為 96, 實際為: {filtered_t2[0]['score']}"


# ============================================================================
# 5. 盤中雷達雙向到價觸發與防毛刺機制驗證 (Intraday Scanner & Tick Guard)
# ============================================================================
def test_intraday_scanner_dual_direction_and_tick_guard():
    """
    測試 5：
    測試 intraday_scanner.evaluate_intraday_alert 雙向到價判定與防毛刺：
    1. GROUP_IGNITION 多方標的：
       - 破底觸發 STOP_LOSS (price <= sl_price)
       - 突破觸發 ADDON (price >= addon_price)：
         - use_tick_guard=True 時第 1 次觸碰回傳 None (攔截)
         - 第 2 次確認放行回傳 "ADDON"
         - 已在 alerted_set 時不再重複觸發
    2. GROUP_UNWIND 空方標的：
       - 急拉觸發 STOP_LOSS (price >= sl_price)
       - 破底回補觸發 TP1 (price <= tp1_price)：
         - use_tick_guard=False 直接觸發
         - use_tick_guard=True 兩段式確認 (第 1 次攔截，第 2 次放行)
    """
    scanner = load_scanner_module()
    assert hasattr(scanner, "evaluate_intraday_alert"), "scanner 缺少 evaluate_intraday_alert 函式"

    # ----------------------------------------------------
    # 情境 A：多方標的 (GROUP_IGNITION)
    # ----------------------------------------------------
    long_target = {
        "code": "1605",
        "name": "華新",
        "state_code": "GROUP_IGNITION",
        "entry_price": 40.0,
        "addon_price": 41.5,
        "tp1_price": 44.0,
        "tp2_price": 47.0,
        "sl_price": 38.0,
    }
    alerted_set_long = set()

    # 1. 多方停損驗證 (price <= sl_price)
    rt_long_sl = {"price": 37.0, "change_pct": -7.5, "volume": 1200}
    GLOBAL_TICK_GUARD.reset_target("1605")
    ev_sl, title_sl, _ = scanner.evaluate_intraday_alert(
        long_target, rt_long_sl, alerted_set_long, use_tick_guard=False
    )
    assert ev_sl == "STOP_LOSS", f"多方跌破停損應觸發 STOP_LOSS, 實際: {ev_sl}"
    assert any(term in title_sl for term in ["停損", "STOP_LOSS", "風控"]), f"標題缺少停損關鍵詞: {title_sl}"

    # 2. 多方加碼 ADDON 驗證與防毛刺雙重確認 (price >= addon_price = 41.5)
    rt_long_addon = {"price": 42.0, "change_pct": 5.0, "volume": 1800}
    GLOBAL_TICK_GUARD.reset_target("1605")
    alerted_set_long.clear()

    # 第 1 次觸碰防毛刺攔截
    ev_long_tick1, _, _ = scanner.evaluate_intraday_alert(
        long_target, rt_long_addon, alerted_set_long, use_tick_guard=True
    )
    assert ev_long_tick1 is None, "多方第 1 次觸碰 ADDON 必須被防毛刺攔截"

    # 第 2 次確認放行
    ev_long_tick2, title_long_addon, _ = scanner.evaluate_intraday_alert(
        long_target, rt_long_addon, alerted_set_long, use_tick_guard=True
    )
    assert ev_long_tick2 == "ADDON", f"多方第 2 次確認應順利放行 ADDON, 實際: {ev_long_tick2}"
    assert any(term in title_long_addon for term in ["加碼", "ADDON", "突破", "續強"]), (
        f"加碼標題詞義不符: {title_long_addon}"
    )

    # 驗證已推播防重複機制
    alerted_set_long.add("ADDON")
    ev_repeat, _, _ = scanner.evaluate_intraday_alert(
        long_target, rt_long_addon, alerted_set_long, use_tick_guard=True
    )
    assert ev_repeat is None, "已存在於 alerted_set 的事件不應重複推播"

    # ----------------------------------------------------
    # 情境 B：空方標的 (GROUP_UNWIND)
    # ----------------------------------------------------
    short_target = {
        "code": "2344",
        "name": "華邦電",
        "state_code": "GROUP_UNWIND",
        "entry_price": 28.0,
        "addon_price": 27.0,
        "tp1_price": 25.5,
        "tp2_price": 23.5,
        "sl_price": 29.5,
    }
    alerted_set_short = set()

    # 1. 空方停損驗證 (price >= sl_price = 29.5)
    rt_short_sl = {"price": 30.5, "change_pct": 8.0, "volume": 2000}
    GLOBAL_TICK_GUARD.reset_target("2344")
    ev_short_sl, title_short_sl, _ = scanner.evaluate_intraday_alert(
        short_target, rt_short_sl, alerted_set_short, use_tick_guard=False
    )
    assert ev_short_sl == "STOP_LOSS", f"空方急拉破線應觸發 STOP_LOSS, 實際: {ev_short_sl}"
    assert any(term in title_short_sl for term in ["停損", "STOP_LOSS", "風控", "回補"]), (
        f"標題缺少停損關鍵詞: {title_short_sl}"
    )

    # 2. 空方停利 TP1 驗證與防毛刺兩段確認 (price <= tp1_price = 25.5)
    rt_short_tp1 = {"price": 25.0, "change_pct": -6.0, "volume": 2500}

    # (a) 不使用防毛刺 (use_tick_guard=False) 直接放行
    GLOBAL_TICK_GUARD.reset_target("2344")
    alerted_set_short.clear()
    ev_direct, _, _ = scanner.evaluate_intraday_alert(
        short_target, rt_short_tp1, alerted_set_short, use_tick_guard=False
    )
    assert ev_direct == "TP1", f"關閉防毛刺時應直接觸發 TP1, 實際: {ev_direct}"

    # (b) 啟用防毛刺 (use_tick_guard=True): 第 1 次觸碰攔截，第 2 次放行
    GLOBAL_TICK_GUARD.reset_target("2344")
    alerted_set_short.clear()
    ev_short_tick1, _, _ = scanner.evaluate_intraday_alert(
        short_target, rt_short_tp1, alerted_set_short, use_tick_guard=True
    )
    assert ev_short_tick1 is None, "空方第 1 次觸碰 TP1 應被防毛刺攔截"

    ev_short_tick2, title_short_tp1, _ = scanner.evaluate_intraday_alert(
        short_target, rt_short_tp1, alerted_set_short, use_tick_guard=True
    )
    assert ev_short_tick2 == "TP1", f"空方第 2 次確認應順利放行 TP1, 實際: {ev_short_tick2}"
    assert any(term in title_short_tp1 for term in ["TP1", "停利", "回補", "了結"]), (
        f"停利標題詞義不符: {title_short_tp1}"
    )


# ============================================================================
# 6. 全系統調度整合、週優化器與 CSV 規格合約驗證 (System Integration & Registration)
# ============================================================================
def test_system_registration_and_weekly_optimizer():
    """
    測試 6：
    驗證總指揮中心全域調度器、週優化器與前端戰情室已完整掛載策略 08：
    1. run_all.STRATEGIES 包含 "08_group_window_dressing"，指向 screener.py
    2. run_all_intraday.INTRADAY_STRATEGIES 包含 "08_group_window_dressing"，指向 intraday_scanner.py
    3. line_sender.resolve_strategy_meta("08") 正確解析元數據與專屬色系
    4. line_bot_server.py 靜態包含 "08_group_window_dressing"
    5. web/index.html 戰情室靜態包含 "08_group_window_dressing"
    6. optimize_all.py 具備 audit_and_rank_group_pool 函式
    7. screener.CSV_COLUMNS 包含集團作帳與四大防線合約欄位
    """
    # 1. run_all.py 註冊檢驗
    import run_all
    strat_08 = next((s for s in run_all.STRATEGIES if s["id"] == "08_group_window_dressing"), None)
    assert strat_08 is not None, "run_all.STRATEGIES 未註冊 08_group_window_dressing"
    assert any(term in strat_08["name"] for term in ["集團", "作帳"]), f"策略名稱未包含核心特徵: {strat_08['name']}"
    assert Path(strat_08["script"]).name == "screener.py", f"腳本路徑錯誤: {strat_08['script']}"

    # 2. run_all_intraday.py 註冊檢驗
    import run_all_intraday
    intra_08 = next((s for s in run_all_intraday.INTRADAY_STRATEGIES if s["id"] == "08_group_window_dressing"), None)
    assert intra_08 is not None, "run_all_intraday.INTRADAY_STRATEGIES 未註冊 08_group_window_dressing"
    assert Path(intra_08["script"]).name == "intraday_scanner.py", f"盤中雷達腳本路徑錯誤: {intra_08['script']}"

    # 3. line_sender.py 元數據解析檢驗
    from line_sender import resolve_strategy_meta
    meta_08 = resolve_strategy_meta("08")
    assert meta_08["id"] == "08", f"預期 id 08, 實際: {meta_08.get('id')}"
    assert any(term in meta_08["name"] for term in ["集團", "作帳"]), f"元數據名稱錯誤: {meta_08['name']}"
    assert "color_theme" in meta_08 and meta_08["color_theme"].startswith("#"), (
        f"色系未配置或非十六進位色碼: {meta_08.get('color_theme')}"
    )
    assert "entry_label" in meta_08 and meta_08["entry_label"], "缺少 entry_label"
    assert "addon_label" in meta_08 and meta_08["addon_label"], "缺少 addon_label"
    assert "tp_label" in meta_08 and meta_08["tp_label"], "缺少 tp_label"
    assert "sl_label" in meta_08 and meta_08["sl_label"], "缺少 sl_label"

    # 4. line_bot_server.py 靜態與路由整合檢驗
    server_path = ROOT_DIR / "line_bot_server.py"
    assert server_path.exists(), "line_bot_server.py 檔案不存在"
    server_content = server_path.read_text(encoding="utf-8")
    assert "08_group_window_dressing" in server_content, (
        "line_bot_server.py 未包含 08_group_window_dressing 策略註冊"
    )

    # 5. web/index.html 戰情室掛載檢驗
    html_path = ROOT_DIR / "web" / "index.html"
    assert html_path.exists(), "web/index.html 檔案不存在"
    html_content = html_path.read_text(encoding="utf-8")
    assert "08_group_window_dressing" in html_content, (
        "web/index.html 未包含 08_group_window_dressing 頁籤或掛載"
    )

    # 6. optimize_all.py 週優化器函式檢驗
    import optimize_all
    assert hasattr(optimize_all, "audit_and_rank_group_pool"), (
        "optimize_all 缺少 audit_and_rank_group_pool 函式"
    )
    assert callable(optimize_all.audit_and_rank_group_pool), (
        "audit_and_rank_group_pool 必須為可呼叫函式"
    )

    # 7. CSV 規格合約欄位完整性檢驗
    screener = load_screener_module()
    assert hasattr(screener, "CSV_COLUMNS"), "screener 模組必須定義 CSV_COLUMNS 列表"
    required_cols = [
        "證券代號",
        "證券名稱",
        "所屬集團",
        "集團旗艦母股",
        "母子位階角色",
        "同集團共振家數",
        "集團共振率%",
        "同集團轉強兄弟股",
        "作帳日曆階段",
        "收盤價",
        "多空狀態",
        "狀態代碼",
        "門檻層級",
        "5日漲幅%",
        "進場價位",
        "加碼價位",
        "停利TP1",
        "停利TP2",
        "停損價位",
        "評分",
        "星級推薦",
    ]
    for col in required_cols:
        assert col in screener.CSV_COLUMNS, f"screener.CSV_COLUMNS 缺少必要欄位: {col}"


# ============================================================================
# V2.0 升級驗收：真實法人 T86、集團作帳熱度排行、集團聚合排序
# ============================================================================
def _mk_member(code, name, gid, gname, flagship, close, ma20, ret5, inst1, inst5, vol=2000, state=None):
    return {
        "code": code, "name": name, "group_id": gid, "group_name": gname,
        "flagship_code": flagship, "flagship_name": "旗艦",
        "close": close, "ma20": ma20, "ret_5d_pct": ret5,
        "inst_1d_lots": inst1, "inst_5d_lots": inst5, "avg_vol_5d": vol,
        "state_code": state,
    }


def test_parse_t86_payload_and_apply_real_institutional():
    """
    T86 解析：股數→張 (向 0 取整，賣超為負)、非 4 碼普通股略過、壞資料列不拋例外、stat != OK 回傳空。
    apply：命中者被覆蓋並標註 T86，未命中者保留推估值並標註 EST。
    """
    screener = load_screener_module()

    def row(code, total):
        r = [""] * 19
        r[0], r[1], r[18] = code, "X", total
        return r

    payload = {
        "stat": "OK",
        "data": [
            row("2330 ", "5,432,999"),   # 去空白、千分位 → 5432 張
            row("1605", "-1,999"),       # 賣超向 0 取整 → -1 張
            row("00878", "9,000,000"),   # ETF 代號 5 碼，略過
            row("2344", "abc"),          # 壞資料，略過
            ["2303"],                    # 欄位不足，略過
        ],
    }
    parsed = screener.parse_t86_payload(payload)
    assert parsed == {"2330": 5432, "1605": -1}
    assert screener.parse_t86_payload({"stat": "很抱歉，沒有符合條件的資料!"}) == {}
    assert screener.parse_t86_payload(None) == {}

    records = [
        {"code": "2330", "inst_1d_lots": 7, "inst_5d_lots": 7},
        {"code": "9999", "inst_1d_lots": 11, "inst_5d_lots": 22},
    ]
    chips = {"2330": {"inst_1d_lots": 5432, "inst_5d_lots": 9000}}
    hit = screener.apply_real_institutional(records, chips)
    assert hit == 1
    assert records[0]["inst_1d_lots"] == 5432 and records[0]["inst_source"] == "T86"
    assert records[1]["inst_1d_lots"] == 11 and records[1]["inst_source"] == "EST"
    assert screener.apply_real_institutional([{"code": "1"}], {}) == 0


def test_group_ranking_heat_order_and_edge_cases():
    """
    構造兩個集團：
      A 集團 3 檔全數站上月線、5日均漲高、法人大買、2 檔點火 → 熱度高、名次第 1
      B 集團 3 檔全數破月線、法人賣超、2 檔結帳 → 狀態應為「結帳提款」、名次墊底
    斷言排行順序、分項指標精確值，並驗證空輸入與缺 group_id 不崩潰。
    """
    screener = load_screener_module()
    recs = [
        _mk_member("1001", "甲一", "GA", "甲集團", "1001", 110, 100, 6.0, 800, 2000, state="GROUP_IGNITION"),
        _mk_member("1002", "甲二", "GA", "甲集團", "1001", 55, 50, 4.0, 500, 1200, state="GROUP_IGNITION"),
        _mk_member("1003", "甲三", "GA", "甲集團", "1001", 31, 30, 2.0, 100, 300, state=None),
        _mk_member("2001", "乙一", "GB", "乙集團", "2001", 90, 100, -5.0, -900, -2500, state="GROUP_UNWIND"),
        _mk_member("2002", "乙二", "GB", "乙集團", "2001", 45, 50, -4.0, -600, -1500, state="GROUP_UNWIND"),
        _mk_member("2003", "乙三", "GB", "乙集團", "2001", 27, 30, -3.0, -300, -900, state=None),
    ]
    pool = [
        {"group_id": "GA", "historical_weight": 1.0},
        {"group_id": "GB", "historical_weight": 1.5},  # 高權重不得讓結帳集團翻盤
    ]
    ranking = screener.calculate_group_ranking(recs, pool)

    assert [g["group_id"] for g in ranking] == ["GA", "GB"]
    assert [g["rank"] for g in ranking] == [1, 2]
    a, b = ranking
    assert a["member_count"] == 3 and a["active_count"] == 3
    assert a["resonance_ratio"] == pytest.approx(100.0)
    assert a["avg_ret_5d_pct"] == pytest.approx(4.0)
    assert a["net_inst_1d_lots"] == 1400
    assert a["ignition_count"] == 2 and a["unwind_count"] == 0
    assert a["heat_score"] > b["heat_score"]
    assert 0.0 <= b["heat_score"] <= a["heat_score"] <= 100.0
    assert a["status_badge"] == "🔥 作帳主攻"
    assert b["status_badge"] == "⚠️ 結帳提款"
    assert b["active_count"] == 0 and b["resonance_ratio"] == 0.0
    assert a["top_movers"][0].startswith("1001")

    assert screener.calculate_group_ranking([]) == []
    assert screener.calculate_group_ranking(None) == []
    # 缺 group_id 的紀錄直接忽略；avg_vol_5d 全 0 也不得 ZeroDivisionError
    odd = [{"code": "9", "close": 1, "ma20": 1}, _mk_member("3001", "丙", "GC", "丙集團", "3001", 10, 9, 1.0, 5, 5, vol=0)]
    r = screener.calculate_group_ranking(odd)
    assert len(r) == 1 and r[0]["group_id"] == "GC"


def test_cluster_items_by_group_rank_order():
    """入選個股須依集團名次集中排列；同集團內多方在前、領頭羊 > 補漲 > 協同、其後分數高者在前。"""
    screener = load_screener_module()
    ranking = [
        {"group_id": "GA", "rank": 1},
        {"group_id": "GB", "rank": 2},
    ]
    items = [
        {"code": "2001", "group_id": "GB", "state_code": "GROUP_IGNITION", "hierarchy_role": "🟢【集團協同齊漲】", "score": 90},
        {"code": "1003", "group_id": "GA", "state_code": "GROUP_UNWIND", "hierarchy_role": "⚠️【高檔結帳提款】", "score": 99},
        {"code": "1002", "group_id": "GA", "state_code": "GROUP_IGNITION", "hierarchy_role": "🚀【母強子落後補漲】", "score": 80},
        {"code": "1001", "group_id": "GA", "state_code": "GROUP_IGNITION", "hierarchy_role": "👑【集團領頭羊主攻】", "score": 70},
        {"code": "1004", "group_id": "GA", "state_code": "GROUP_IGNITION", "hierarchy_role": "🚀【母強子落後補漲】", "score": 85},
        {"code": "9001", "group_id": "GX", "state_code": "GROUP_IGNITION", "hierarchy_role": "🟢【集團協同齊漲】", "score": 99},  # 未上榜集團墊底
    ]
    out = [i["code"] for i in screener.cluster_items_by_group_rank(items, ranking)]
    assert out == ["1001", "1004", "1002", "1003", "2001", "9001"]
    assert screener.cluster_items_by_group_rank([], ranking) == []


def test_group_rank_bonus_and_report_rendering():
    """
    軟評分：前三名集團的多方標的依名次 +6/+4/+2；空方與第 4 名不加分；未帶 group_rank 行為不變。
    報表：Markdown 與 LINE 文字須同時含排行榜與依名次排列的個股，且 LINE 文字不含 ASCII 長橫線。
    """
    screener = load_screener_module()
    cal = {"season_bonus": 10, "unwind_bonus": 5, "phase_badge": "⏳ 季底作帳衝刺期", "phase_code": "QUARTER_END_SPRINT"}
    item = {"state_code": "GROUP_IGNITION", "group_resonance_ratio": 45.0, "inst_1d_lots": 300,
            "hierarchy_role": "🟢【集團協同齊漲】"}
    base, _ = screener.calculate_group_soft_score(item, cal)
    assert screener.calculate_group_soft_score(item, cal, group_rank=None)[0] == base
    assert screener.calculate_group_soft_score(item, cal, group_rank=1)[0] == base + 6
    assert screener.calculate_group_soft_score(item, cal, group_rank=2)[0] == base + 4
    assert screener.calculate_group_soft_score(item, cal, group_rank=3)[0] == base + 2
    assert screener.calculate_group_soft_score(item, cal, group_rank=4)[0] == base
    short = dict(item, state_code="GROUP_UNWIND", inst_1d_lots=-300)
    assert (screener.calculate_group_soft_score(short, cal, group_rank=1)[0]
            == screener.calculate_group_soft_score(short, cal)[0])

    recs = [
        _mk_member("1001", "甲一", "GA", "甲集團", "1001", 110, 100, 6.0, 800, 2000, state="GROUP_IGNITION"),
        _mk_member("2001", "乙一", "GB", "乙集團", "2001", 90, 100, -5.0, -900, -2500, state="GROUP_UNWIND"),
    ]
    ranking = screener.calculate_group_ranking(recs)
    items = [
        dict(recs[0], state_badge="🟢【作帳點火・母子共振】", hierarchy_role="👑【集團領頭羊主攻】",
             entry_price=110.0, addon_price=113.0, tp1_price=118.0, sl_price=104.0, score=88, stars="★★★★★"),
        dict(recs[1], state_badge="🔴【集團結帳・順勢放空】", hierarchy_role="⚠️【高檔結帳提款】",
             entry_price=90.0, addon_price=87.0, tp1_price=82.0, sl_price=95.0, score=70, stars="★★★☆☆"),
    ]
    md = screener.build_group_markdown_report(ranking, items, cal, "2026-10-19", "TIER1_RESONANCE",
                                              cards_md="CARDS_PLACEHOLDER", inst_real_count=1, inst_total_count=2)
    assert "今日集團作帳熱度排行榜" in md and "CARDS_PLACEHOLDER" in md
    assert md.index("甲集團") < md.index("乙集團")
    assert md.index("1001 甲一") < md.index("2001 乙一")

    txt = screener.format_group_ranking_text(ranking, items, "2026-10-19", cal)
    assert "🥇 甲集團" in txt and "🥈 乙集團" in txt and "入選：1001 甲一" in txt
    assert "━" not in txt and "──" not in txt and "═" not in txt

    # CSV 欄位：新增集團排行欄位且原合約欄位完整保留
    for col in ("集團排名", "集團熱度分", "集團狀態", "所屬集團", "進場價位", "停損價位", "評分"):
        assert col in screener.CSV_COLUMNS
    assert len(screener.CSV_COLUMNS) == len(set(screener.CSV_COLUMNS))
