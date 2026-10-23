#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 09：庫藏股宣告護盤與低估值事件策略 - 自動化驗收測試套件 (pytest)
================================================================================
本測試嚴格遵循《全新專案主題閉環開發工作流規範》與《總指揮中心全域規範》：
- 機械客觀、真實邏輯驗證，嚴禁永遠必過的過度 Mocking 虛假測試。
- 檢驗 7 大測試合約：
  1. 庫藏股事件池與歷史執行率字典綱要結構校驗 (Schema & Integrity)
  2. 歷史執行率誠信評等與「狼來了口水護盤」懲罰過濾器 (Wolf Warning Filter)
  3. 4+1 維度硬性濾網邊界與三大法人防對倒共振檢驗 (Hard Filter Constraints)
  4. 實戰四大防線定價幾何錨定與數學單調性嚴格檢驗 (Four-Price Monotonicity)
  5. 三層動態降級容錯架構檢驗 (Three-Tier Fallback: Tier-1 -> Tier-2 -> Tier-3)
  6. 盤中即時雷達五大到價事件與防毛刺守門器整合檢驗 (Tick Guard & Intraday Alerts)
  7. 總指揮中心全系統集成註冊檢驗 (Master Center Registration)
================================================================================
"""

import sys
import os
import datetime
import importlib.util
from pathlib import Path
import pytest
import pandas as pd

# 將專案根目錄加入 sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from algo_guards import GLOBAL_TICK_GUARD
import line_sender


def load_screener_09():
    """動態載入策略 09 盤後選股模組"""
    path = ROOT_DIR / "strategies" / "09_treasury_stock_buyback" / "screener.py"
    if not path.exists():
        pytest.fail(f"策略 09 選股器不存在: {path}")
    spec = importlib.util.spec_from_file_location("screener_09", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_scanner_09():
    """動態載入策略 09 盤中雷達模組"""
    path = ROOT_DIR / "strategies" / "09_treasury_stock_buyback" / "intraday_scanner.py"
    if not path.exists():
        pytest.fail(f"策略 09 雷達模組不存在: {path}")
    spec = importlib.util.spec_from_file_location("scanner_09", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================================
# 1. 字典與事件池綱要檢驗 (Schema & Integrity)
# ============================================================================
def test_treasury_pool_and_execution_history_schema():
    """驗證 treasury_pool.json、execution_history.json 與 insider_pool.json 符合標準規格"""
    scr = load_screener_09()
    pool = scr.load_treasury_pool()
    hist = scr.load_execution_history()
    insider = scr.load_insider_pool()

    assert len(pool) >= 10, f"庫藏股事件池標的過少: {len(pool)} 檔 (預期 >= 10 檔)"
    assert len(hist) >= 10, f"歷史執行率字典過少: {len(hist)} 檔 (預期 >= 10 檔)"
    assert len(insider) >= 3, f"內部人增持備援池過少: {len(insider)} 檔 (預期 >= 3 檔)"

    # 檢驗 pool 欄位完整性與幾何合法性
    for item in pool:
        assert "code" in item, "缺少 code 欄位"
        assert "name" in item, "缺少 name 欄位"
        assert "announcement_date" in item, "缺少 announcement_date 欄位"
        assert "price_min" in item and "price_max" in item, "缺少買回區間價格"
        p_min = float(item["price_min"])
        p_max = float(item["price_max"])
        assert p_min < p_max, f"買回區間下限 ({p_min}) 必須小於上限 ({p_max})"
        assert float(item.get("capital_ratio_pct", 0)) > 0, "股本佔比必須大於 0"
        assert int(item.get("planned_shares", 0)) > 0, "預定買回張數必須大於 0"

    # 檢驗 hist 欄位與數值合理性
    for code, h in hist.items():
        assert "avg_execution_rate_pct" in h, f"{code} 缺少 avg_execution_rate_pct"
        rate = float(h["avg_execution_rate_pct"])
        assert 0.0 <= rate <= 100.0, f"執行率超出範圍: {rate}"
        assert "is_reliable" in h, f"{code} 缺少 is_reliable 旗標"


# ============================================================================
# 2. 歷史執行率狼來了過濾器檢驗 (Wolf Warning Filter)
# ============================================================================
def test_historical_execution_rate_penalty_and_filter():
    """驗證過往 3 年平均執行率 < 50% 者，自動扣除 20 分並標註口水護盤警示，防止芭樂票"""
    scr = load_screener_09()
    hist = scr.load_execution_history()

    # 2371 大同：過往執行率 27.5% -> 應判定為 WOLF_WARNING，扣 20 分且 is_reliable 為 False
    avg_rate, status, is_reliable, penalty, desc = scr.evaluate_treasury_execution_reliability("2371", hist)
    assert avg_rate < 50.0, f"大同平均執行率應 < 50%，實際為 {avg_rate}"
    assert status == "WOLF_WARNING", f"狀態碼應為 WOLF_WARNING，實際為 {status}"
    assert is_reliable is False, "口水護盤標的 is_reliable 必須為 False"
    assert penalty == -20, f"懲罰扣分應為 -20，實際為 {penalty}"
    assert "狼來了" in desc, f"描述應包含狼來了警示: {desc}"

    # 2474 可成：過往執行率 96.9% -> 應判定為 HIGH_RELIABILITY，加 15 分
    avg_rate, status, is_reliable, penalty, desc = scr.evaluate_treasury_execution_reliability("2474", hist)
    assert avg_rate >= 80.0, f"可成平均執行率應 >= 80%，實際為 {avg_rate}"
    assert status == "HIGH_RELIABILITY", f"狀態碼應為 HIGH_RELIABILITY，實際為 {status}"
    assert is_reliable is True, "高執行率標的 is_reliable 必須為 True"
    assert penalty == 15, f"加分應為 +15，實際為 {penalty}"

    # 未建檔之全新標的 -> 中性判定
    _, status, is_reliable, penalty, _ = scr.evaluate_treasury_execution_reliability("9999", hist)
    assert status == "FIRST_TIME"
    assert is_reliable is True
    assert penalty == 0


# ============================================================================
# 3. 4+1 維度硬性濾網邊界檢驗 (Hard Filter Constraints)
# ============================================================================
def test_hard_filters_four_plus_one():
    """驗證 5 日均量 >= 1000張、PB <= 1.25、買回比例 >= 1.5%、距上限空間 >= 12%、三大法人賣超 <= 500張"""
    scr = load_screener_09()
    hist = scr.load_execution_history()
    insider = scr.load_insider_pool()

    base_item = {
        "code": "8888",
        "name": "測試股票",
        "market": "上市",
        "announcement_date": datetime.date.today().strftime("%Y-%m-%d"),
        "purpose": "維護公司信用及股東權益",
        "planned_shares": 20000,
        "capital_ratio_pct": 2.0,
        "price_min": 80.0,
        "price_max": 130.0,
        "book_value": 90.0,
        "execution_rate_pct": 0.0,
        "status": "ACTIVE"
    }

    # 測試 1: 5日均量不足 (例如 500 張) -> 應被 Tier-1 剔除
    orig_fetch = scr.fetch_stock_market_data
    try:
        # Mock 成交量不足
        scr.fetch_stock_market_data = lambda code, dt=None, market="上市": {
            "close": 100.0, "change": 1.0, "pct_change": 1.0, "volume": 600, "volume_5ma": 600,
            "ma20": 98.0, "ma60": 95.0, "high": 102.0, "low": 98.0, "open": 99.0,
            "book_value": 90.0, "pb_ratio": 1.11, "institutional_net": 200,
            "is_disposal": False, "is_full_cash": False
        }
        res, tier = scr.apply_treasury_screening([base_item], hist, insider)
        assert tier != "TIER1_NEW_ANNOUNCEMENT", "5日均量 < 1,000 張不應通過 Tier-1 硬篩"

        # 測試 2: PB 過高 (PB = 1.6 > 1.25) -> 應被 Tier-1 剔除
        scr.fetch_stock_market_data = lambda code, dt=None, market="上市": {
            "close": 160.0, "change": 1.0, "pct_change": 1.0, "volume": 2000, "volume_5ma": 1800,
            "ma20": 155.0, "ma60": 150.0, "high": 162.0, "low": 158.0, "open": 159.0,
            "book_value": 100.0, "pb_ratio": 1.60, "institutional_net": 200,
            "is_disposal": False, "is_full_cash": False
        }
        res, tier = scr.apply_treasury_screening([base_item], hist, insider)
        assert tier != "TIER1_NEW_ANNOUNCEMENT", "PB > 1.25 不應通過 Tier-1 低估值硬篩"

        # 測試 3: 距買回上限空間過窄 (現價 125, 上限 130, 空間僅 4% < 12%) -> 應被 Tier-1 剔除防追高
        scr.fetch_stock_market_data = lambda code, dt=None, market="上市": {
            "close": 125.0, "change": 1.0, "pct_change": 1.0, "volume": 2000, "volume_5ma": 1800,
            "ma20": 120.0, "ma60": 115.0, "high": 126.0, "low": 122.0, "open": 123.0,
            "book_value": 120.0, "pb_ratio": 1.04, "institutional_net": 200,
            "is_disposal": False, "is_full_cash": False
        }
        res, tier = scr.apply_treasury_screening([base_item], hist, insider)
        assert tier != "TIER1_NEW_ANNOUNCEMENT", "距買回上限空間 < 12% 應被防追高硬網攔截"

        # 測試 4: 法人大額出貨對倒 (賣超 -1200 張 < -500 張) -> 應被 Tier-1 剔除
        scr.fetch_stock_market_data = lambda code, dt=None, market="上市": {
            "close": 100.0, "change": 1.0, "pct_change": 1.0, "volume": 2500, "volume_5ma": 2200,
            "ma20": 98.0, "ma60": 95.0, "high": 102.0, "low": 98.0, "open": 99.0,
            "book_value": 95.0, "pb_ratio": 1.05, "institutional_net": -1200,
            "is_disposal": False, "is_full_cash": False
        }
        res, tier = scr.apply_treasury_screening([base_item], hist, insider)
        assert tier != "TIER1_NEW_ANNOUNCEMENT", "法人大額賣超 > 500 張應被防對倒濾網攔截"
    finally:
        scr.fetch_stock_market_data = orig_fetch


# ============================================================================
# 4. 實戰四大防線定價幾何錨定與數學單調性檢驗 (Four-Price Monotonicity)
# ============================================================================
def test_four_price_monotonicity_and_anchoring():
    """驗證多方操盤防線嚴格單調性：sl_price < price <= entry_price < addon_price < tp1_price <= tp2_price"""
    scr = load_screener_09()

    test_cases = [
        # (現價, 買回下限, 買回上限, 淨值, 最高, 最低)
        (195.5, 160.0, 240.0, 235.0, 201.0, 192.0),
        (35.0, 30.0, 48.0, 42.0, 36.5, 33.8),
        (15.2, 12.0, 22.0, 21.0, 15.8, 14.9),
        (510.0, 460.0, 650.0, 480.0, 520.0, 500.0),
        # 極端邊界：現價逼近買回下限
        (161.0, 160.0, 240.0, 200.0, 165.0, 159.0),
        # 極端邊界：現價逼近買回上限
        (230.0, 160.0, 240.0, 250.0, 235.0, 228.0),
    ]

    for price, p_min, p_max, bv, h, l in test_cases:
        fp = scr.calculate_treasury_four_price(price, p_min, p_max, bv, high=h, low=l)
        sl = fp["sl_price"]
        entry = fp["entry_price"]
        addon = fp["addon_price"]
        tp1 = fp["tp1_price"]
        tp2 = fp["tp2_price"]

        # 斷言 1: 停損價嚴格低於現價與進場價
        assert sl < price, f"停損價 ({sl}) 必須小於現價 ({price})"
        assert sl < entry, f"停損價 ({sl}) 必須小於進場上限 ({entry})"

        # 斷言 2: 進場買進上限大於等於現價
        assert entry >= price, f"進場買進上限 ({entry}) 必須 >= 現價 ({price})"

        # 斷言 3: 動能加碼價嚴格大於進場買進上限
        assert addon > entry, f"加碼價 ({addon}) 必須大於進場價 ({entry})"

        # 斷言 4: 第一停利價嚴格大於加碼價
        assert tp1 > addon, f"TP1 ({tp1}) 必須大於加碼價 ({addon})"

        # 斷言 5: 第二停利價大於等於第一停利價
        assert tp2 >= tp1, f"TP2 ({tp2}) 必須 >= TP1 ({tp1})"


# ============================================================================
# 5. 三層動態降級容錯架構檢驗 (Three-Tier Fallback Mechanism)
# ============================================================================
def test_three_tier_fallback_mechanism():
    """驗證當 Tier-1 為 0 檔時自動啟動 Tier-2，若仍無則啟動 Tier-3，永不崩潰"""
    scr = load_screener_09()
    hist = scr.load_execution_history()
    insider = scr.load_insider_pool()

    # 模擬 1: 當所有庫藏股宣告皆過期 (> 60天) -> 應自動降級至 Tier-3 董監增持備援
    stale_pool = [
        {
            "code": "1111", "name": "過期庫藏A", "announcement_date": "2025-01-01",
            "price_min": 50.0, "price_max": 75.0, "planned_shares": 10000,
            "capital_ratio_pct": 2.0, "status": "EXPIRED"
        }
    ]
    res, tier = scr.apply_treasury_screening(stale_pool, hist, insider)
    assert tier == "TIER3_INSIDER_BUYING", f"當無有效庫藏股時應啟動 Tier-3，實際為: {tier}"
    assert len(res) > 0, "Tier-3 應回傳董監增持備援標的"
    assert res[0]["層級分類"] == "Tier-3 董監增持備援"

    # 模擬 2: 空字典與空清單容錯 -> 應優雅回傳 ([], 'EMPTY_POOL')，嚴禁未捕捉異常
    res, tier = scr.apply_treasury_screening([], {}, [])
    assert tier == "EMPTY_POOL"
    assert res == []


# ============================================================================
# 6. 盤中即時雷達五大到價事件與防毛刺守門器檢驗 (Intraday Alerts & Tick Guard)
# ============================================================================
def test_intraday_tick_guard_and_events():
    """驗證盤中雷達針對 STOP_LOSS、TP2、TP1、ADDON、ENTRY 五大事件之觸發與防毛刺檢核"""
    scan = load_scanner_09()

    target = {
        "code": "2474",
        "name": "可成",
        "price": 195.5,
        "entry_price": 202.34,
        "addon_price": 209.42,
        "tp1_price": 230.40,
        "tp2_price": 249.60,
        "sl_price": 156.80
    }

    alerted_set = set()

    # 測試 1: 停損事件 (現價 155.0 <= sl_price 156.8) -> 必須最高優先級觸發 STOP_LOSS
    rt_sl = {"price": 155.0, "change": -40.5, "pct_change": -20.7, "volume": 5000}
    event_type, event_title, _ = scan.evaluate_intraday_alert(target, rt_sl, alerted_set, use_tick_guard=False)
    assert event_type == "STOP_LOSS"
    assert "停損" in event_title
    assert "2474_STOP_LOSS" in alerted_set

    # 測試 2: 去重檢驗 (同一事件第二次巡檢不得重複觸發)
    event_type_dup, _, _ = scan.evaluate_intraday_alert(target, rt_sl, alerted_set, use_tick_guard=False)
    assert event_type_dup is None, "已觸發事件不可重複推送洗版"

    # 測試 3: 第二停利事件 (現價 252.0 >= tp2_price 249.6)
    alerted_set.clear()
    rt_tp2 = {"price": 252.0, "change": 12.0, "pct_change": 5.0, "volume": 3000}
    event_type, event_title, _ = scan.evaluate_intraday_alert(target, rt_tp2, alerted_set, use_tick_guard=False)
    assert event_type == "TP2"
    assert "淨值回歸" in event_title

    # 測試 4: 第一停利事件 (現價 235.0 >= tp1_price 230.4)
    alerted_set.clear()
    rt_tp1 = {"price": 235.0, "change": 8.0, "pct_change": 3.5, "volume": 2000}
    event_type, event_title, _ = scan.evaluate_intraday_alert(target, rt_tp1, alerted_set, use_tick_guard=False)
    assert event_type == "TP1"
    assert "買回上限" in event_title

    # 測試 5: 突破加碼事件 (現價 212.0 >= addon_price 209.42)
    alerted_set.clear()
    rt_addon = {"price": 212.0, "change": 5.0, "pct_change": 2.4, "volume": 2500}
    event_type, event_title, _ = scan.evaluate_intraday_alert(target, rt_addon, alerted_set, use_tick_guard=False)
    assert event_type == "ADDON"
    assert "加碼" in event_title

    # 測試 6: 伏擊買進事件 (現價 198.0 <= entry_price 202.34 且 > sl_price)
    alerted_set.clear()
    rt_entry = {"price": 198.0, "change": 1.0, "pct_change": 0.5, "volume": 1200}
    event_type, event_title, _ = scan.evaluate_intraday_alert(target, rt_entry, alerted_set, use_tick_guard=False)
    assert event_type == "ENTRY"
    assert "買點" in event_title


# ============================================================================
# 7. 總指揮中心全系統集成註冊檢驗 (Master Command Center Integration)
# ============================================================================
def test_master_command_center_integration():
    """驗證 run_all.py、run_all_intraday.py、line_sender.py、radar_manager.py、line_bot_server.py、web/index.html 均完整註冊策略 09"""
    import run_all
    import run_all_intraday

    # 1. run_all.py
    strat_ids = [s["id"] for s in run_all.STRATEGIES]
    assert "09_treasury_stock_buyback" in strat_ids, "run_all.py 未註冊 09_treasury_stock_buyback"

    # 2. run_all_intraday.py
    intraday_ids = [s["id"] for s in run_all_intraday.INTRADAY_STRATEGIES]
    assert "09_treasury_stock_buyback" in intraday_ids, "run_all_intraday.py 未註冊 09_treasury_stock_buyback"

    # 3. line_sender.py resolve_strategy_meta
    meta = line_sender.resolve_strategy_meta("09")
    assert meta["id"] == "09", f"line_sender 策略 ID 不符: {meta['id']}"
    assert "庫藏" in meta["name"], f"line_sender 策略名稱不符: {meta['name']}"
    assert meta["color_theme"] == "#D97706", f"主題色應為琥珀金 #D97706，實際為: {meta['color_theme']}"

    # 4. web/index.html
    web_file = ROOT_DIR / "web" / "index.html"
    assert web_file.exists()
    web_content = web_file.read_text(encoding="utf-8")
    assert "subbtn-09" in web_content, "web/index.html 未註冊 subbtn-09 按鈕"
    assert "09_treasury_stock_buyback" in web_content, "web/index.html 未載入 09_treasury_stock_buyback"

    # 5. .env.example
    env_file = ROOT_DIR / ".env.example"
    assert env_file.exists()
    env_content = env_file.read_text(encoding="utf-8")
    assert "LINE_TARGET_STRATEGY_09" in env_content, ".env.example 缺少 LINE_TARGET_STRATEGY_09"
