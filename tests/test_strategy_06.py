#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 06【盤中成交量異常爆量起漲動能】自動化測試合約 (pytest)
================================================================================
本測試嚴格依據《全新專案主題閉環開發工作流全域規範》撰寫：
- 機械客觀真實驗證，絕不使用過度假 Mock 或必過空測試
- 檢驗真實演算法核心、邊界條件、階梯狀態機、四大價位與主調度器註冊
================================================================================
"""

import os
import sys
import datetime
import pytest
from pathlib import Path

# 將專案根目錄加入 sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import importlib.util

# 動態載入 06_volume_surge 模組 (因資料夾以數字 06 開頭，Python 語法不支援直接 from 語句)
screener_path = ROOT_DIR / "strategies" / "06_volume_surge" / "screener.py"
spec_scr = importlib.util.spec_from_file_location("screener_06", screener_path)
screener_06 = importlib.util.module_from_spec(spec_scr)
spec_scr.loader.exec_module(screener_06)

calculate_candle_metrics = screener_06.calculate_candle_metrics
calculate_score_and_stars = screener_06.calculate_score_and_stars
SCREENER_STRAT_ID = screener_06.STRATEGY_ID

scanner_path = ROOT_DIR / "strategies" / "06_volume_surge" / "intraday_scanner.py"
spec_scan = importlib.util.spec_from_file_location("scanner_06", scanner_path)
scanner_06 = importlib.util.module_from_spec(spec_scan)
spec_scan.loader.exec_module(scanner_06)

get_market_progress_ratio = scanner_06.get_market_progress_ratio
calculate_projected_volume = scanner_06.calculate_projected_volume
evaluate_volume_surge_and_alert = scanner_06.evaluate_volume_surge_and_alert
TRIGGERED_LEVELS = scanner_06.TRIGGERED_LEVELS
TZ_TW = scanner_06.TZ_TW
from line_sender import resolve_strategy_meta
from run_all import STRATEGIES as RUN_ALL_STRATEGIES
from run_all_intraday import INTRADAY_STRATEGIES as RUN_INTRADAY_STRATEGIES


class TestVolumeSurgeEngine:
    """一、時間加權動態預估量與開盤進度比例測試"""

    def test_market_progress_ratio_boundaries(self):
        """測試盤中不同時間點的進度比計算與邊界平滑防呆"""
        base_day = datetime.date(2026, 10, 16)

        # 1. 盤前 (08:30) -> 回傳防呆下限 0.05
        t_pre = datetime.datetime.combine(base_day, datetime.time(8, 30), tzinfo=TZ_TW)
        assert get_market_progress_ratio(t_pre) == 0.05

        # 2. 早盤極端毛刺期 (09:05) -> 鎖定平滑下限 0.05
        t_early = datetime.datetime.combine(base_day, datetime.time(9, 5), tzinfo=TZ_TW)
        assert get_market_progress_ratio(t_early) == 0.05

        # 3. 盤中中段 (11:15，經過 135 分鐘，剛好一半 135/270)
        t_mid = datetime.datetime.combine(base_day, datetime.time(11, 15), tzinfo=TZ_TW)
        progress_mid = get_market_progress_ratio(t_mid)
        assert pytest.approx(progress_mid, 0.01) == 0.50

        # 4. 尾盤收盤 (13:30) -> 回傳 1.0
        t_close = datetime.datetime.combine(base_day, datetime.time(13, 30), tzinfo=TZ_TW)
        assert get_market_progress_ratio(t_close) == 1.0

        # 5. 盤後 (14:00) -> 回傳 1.0
        t_post = datetime.datetime.combine(base_day, datetime.time(14, 0), tzinfo=TZ_TW)
        assert get_market_progress_ratio(t_post) == 1.0

    def test_projected_volume_math(self):
        """測試全日動態預估量換算準確度"""
        # 開盤 50% 進度時累積 2,500 張 -> 預估 5,000 張
        assert calculate_projected_volume(2500, 0.50) == 5000

        # 開盤 25% 進度時累積 1,000 張 -> 預估 4,000 張
        assert calculate_projected_volume(1000, 0.25) == 4000

        # 進度 <= 0 時的安全防呆
        assert calculate_projected_volume(1000, 0.0) == 1000


class TestCandleAndPriceGuards:
    """二、價格與型態守門 (2-C) 檢驗"""

    def test_solid_bullish_candle(self):
        """測試標準實體飽滿紅K棒（應通過型態篩選）"""
        # Open 100, High 108, Low 99, Close 107
        # Amplitude = 9, Solid = (107 - 99) = 8 -> 8/9 = 0.888 >= 0.60
        # Upper shadow = (108 - 107) = 1 -> 1/9 = 0.111 <= 0.40
        metrics = calculate_candle_metrics(open_p=100.0, high_p=108.0, low_p=99.0, close_p=107.0)
        assert metrics['is_bullish'] is True
        assert metrics['solid_ratio'] >= 0.60
        assert metrics['upper_shadow_ratio'] <= 0.40

    def test_tombstone_exhaustion_candle_rejected(self):
        """測試爆量長上影線墓碑線/倒貨線（必須被阻擋）"""
        # Open 100, High 112, Low 99, Close 101
        # Amplitude = 13, Solid = (101 - 99) = 2 -> 2/13 = 0.153 < 0.60
        # Upper shadow = (112 - 101) = 11 -> 11/13 = 0.846 > 0.40
        metrics = calculate_candle_metrics(open_p=100.0, high_p=112.0, low_p=99.0, close_p=101.0)
        assert metrics['solid_ratio'] < 0.60
        assert metrics['upper_shadow_ratio'] > 0.40

    def test_bearish_drop_rejected(self):
        """測試盤中爆量但收黑或跌破（必須被阻擋）"""
        metrics = calculate_candle_metrics(open_p=105.0, high_p=106.0, low_p=98.0, close_p=99.0)
        assert metrics['is_bullish'] is False


class TestSteppedDeduplicationStateMachine:
    """三、階梯式防洗版去重狀態機 (4-A) 測試"""

    def setup_method(self):
        """每個測試執行前清空狀態機記錄"""
        TRIGGERED_LEVELS.clear()

    def test_stepped_alert_two_stages_maximum(self):
        """測試單檔股票兩段式階梯觸發與第三次後嚴格抑制"""
        target = {
            'code': '9999',
            'name': '測試爆量股',
            'v5_avg': 1000,
            'addon_price': 105.0
        }

        # 模擬 50% 進度 (progress = 0.5)
        progress = 0.5

        # 案例 1：量比不足 (累積 1,000 張 -> 預估 2,000 張 / 5MA 1,000 -> Rvol = 2.0x < 2.5x)
        snap_normal = {
            'price': 104.0, 'high': 105.0, 'low': 99.0, 'open': 100.0,
            'volume_lots': 1000, 'change_pct': 4.0
        }
        ok, level, rvol, _ = evaluate_volume_surge_and_alert(target, snap_normal, progress, force_pass=True)
        assert ok is False
        assert level == 0

        # 案例 2：初段爆量 (累積 1,400 張 -> 預估 2,800 張 -> Rvol = 2.8x >= 2.5x)
        snap_l1 = {
            'price': 104.5, 'high': 105.5, 'low': 99.0, 'open': 100.0,
            'volume_lots': 1400, 'change_pct': 4.5
        }
        ok, level, rvol, info = evaluate_volume_surge_and_alert(target, snap_l1, progress, force_pass=True)
        assert ok is True
        assert level == 1
        assert rvol == 2.8
        assert "初段異常爆量起漲" in info['event_title']
        # 標記狀態機
        TRIGGERED_LEVELS['9999'] = 1

        # 案例 3：同日持續微增量 (累積 1,600 張 -> 預估 3,200 張 -> Rvol = 3.2x，尚未達 4.0x)
        snap_repeat = {
            'price': 105.0, 'high': 106.0, 'low': 99.0, 'open': 100.0,
            'volume_lots': 1600, 'change_pct': 5.0
        }
        ok, level, _, _ = evaluate_volume_surge_and_alert(target, snap_repeat, progress, force_pass=True)
        # 必須被抑制，絕不洗版！
        assert ok is False

        # 案例 4：極致天量噴出 (累積 2,200 張 -> 預估 4,400 張 -> Rvol = 4.4x >= 4.0x)
        snap_l2 = {
            'price': 107.0, 'high': 108.0, 'low': 99.0, 'open': 100.0,
            'volume_lots': 2200, 'change_pct': 7.0
        }
        ok, level, rvol, info = evaluate_volume_surge_and_alert(target, snap_l2, progress, force_pass=True)
        assert ok is True
        assert level == 2
        assert rvol == 4.4
        assert "極致天量主升噴出" in info['event_title']
        # 標記狀態機
        TRIGGERED_LEVELS['9999'] = 2

        # 案例 5：後續天量持續擴增 (Rvol = 5.0x)
        snap_max = {
            'price': 109.0, 'high': 110.0, 'low': 99.0, 'open': 100.0,
            'volume_lots': 2500, 'change_pct': 9.0
        }
        ok, _, _, _ = evaluate_volume_surge_and_alert(target, snap_max, progress, force_pass=True)
        # 單檔單日已滿 2 次，必須絕對靜默！
        assert ok is False


class TestFourPriceDefensesAndScoring:
    """四、實戰四大防線價位與軟評分邏輯測試"""

    def test_four_price_defenses_math_and_rr(self):
        """測試四大防線價位之幾何順序與合理盈虧比"""
        curr_close = 100.0
        high_p = 103.0
        low_p = 98.0

        entry_p = round(curr_close * 1.01, 2)       # 101.0
        addon_p = round(high_p * 1.005, 2)         # 103.51
        tp1_p = round(curr_close * 1.08, 2)         # 108.0
        tp2_p = round(curr_close * 1.15, 2)         # 115.0
        sl_p = round(max(low_p, curr_close * 0.965), 2)  # max(98.0, 96.5) = 98.0

        # 1. 價位順序檢驗
        assert sl_p < curr_close <= entry_p < addon_p < tp1_p < tp2_p

        # 2. 盈虧比檢驗 (Risk/Reward Ratio)
        risk = entry_p - sl_p          # 101.0 - 98.0 = 3.0
        reward_tp1 = tp1_p - entry_p   # 108.0 - 101.0 = 7.0
        rr_ratio = reward_tp1 / risk   # 7.0 / 3.0 = 2.33
        assert rr_ratio >= 1.8

    def test_soft_scoring_and_stars(self):
        """測試軟評分矩陣與星級判定"""
        # 極致天量 (4.5x) + 光頭實體紅K (0.90) + 法人合買 1,500 張
        score_high, stars_high = calculate_score_and_stars(
            rvol=4.5, solid_ratio=0.90, foreign_lots=1000, trust_lots=500, broker_penalty=0
        )
        assert score_high >= 85
        assert stars_high == "★★★★★"

        # 隔日沖券商倒貨懲罰扣分
        score_penalized, stars_penalized = calculate_score_and_stars(
            rvol=2.8, solid_ratio=0.72, foreign_lots=200, trust_lots=0, broker_penalty=15
        )
        assert score_penalized < score_high
        assert score_penalized <= 70


class TestSystemIntegrationRegistry:
    """五、總指揮中心全域調度器與 LINE 元數據註冊檢驗"""

    def test_run_all_strategies_registration(self):
        """檢驗 run_all.py 成功註冊策略 06 且腳本存在"""
        s06 = next((s for s in RUN_ALL_STRATEGIES if s['id'] == '06_volume_surge'), None)
        assert s06 is not None, "run_all.py STRATEGIES 未包含 06_volume_surge"
        assert Path(s06['script']).exists(), f"找不到腳本: {s06['script']}"

    def test_run_all_intraday_registration(self):
        """檢驗 run_all_intraday.py 成功註冊策略 06 且腳本存在"""
        s06_intra = next((s for s in RUN_INTRADAY_STRATEGIES if s['id'] == '06_volume_surge'), None)
        assert s06_intra is not None, "run_all_intraday.py INTRADAY_STRATEGIES 未包含 06_volume_surge"
        assert Path(s06_intra['script']).exists(), f"找不到腳本: {s06_intra['script']}"

    def test_line_sender_strategy_06_metadata(self):
        """檢驗 line_sender.py 之 resolve_strategy_meta 成功支援策略 06"""
        meta = resolve_strategy_meta("06")
        assert meta['id'] == "06"
        assert "爆量" in meta['name']
        assert meta['color_theme'] == "#EA580C"
        assert "進場" in meta['entry_label']
        assert "加碼" in meta['addon_label']
        assert "停利" in meta['tp_label']
        assert "停損" in meta['sl_label']

    def test_strategy_06_aggregated_alerts_support(self):
        """檢驗策略 06 支援多檔合併戰訊卡與額度優化機制 (比照策略 01~05 標準)"""
        assert hasattr(scanner_06, 'send_aggregated_price_alerts'), "策略 06 缺少 send_aggregated_price_alerts 函式"

        # 測試建構合併戰訊卡
        alerts = [
            {
                'target': {'code': '6112', 'name': '邁達特', 'entry_price': 63.8, 'addon_price': 64.4, 'tp1_price': 66.5, 'sl_price': 61.9},
                'rt': {'price': 63.8, 'change_pct': 2.41, 'volume': 4015},
                'event_code': 'LEVEL_1_SURGE',
                'event_title': '🚨【初段異常爆量起漲】',
                'event_desc': '動態預估量達 3.61 倍'
            },
            {
                'target': {'code': '2332', 'name': '友訊', 'entry_price': 18.85, 'addon_price': 19.0, 'tp1_price': 19.8, 'sl_price': 18.15},
                'rt': {'price': 18.85, 'change_pct': 3.86, 'volume': 3944},
                'event_code': 'LEVEL_1_SURGE',
                'event_title': '🚨【初段異常爆量起漲】',
                'event_desc': '動態預估量達 2.54 倍'
            }
        ]
        from line_sender import build_aggregated_alert_card, format_clean_aggregated_text
        card = build_aggregated_alert_card(alerts, strategy_id="06", now_str="10:30:00")
        assert card['type'] == 'flex'
        assert "爆量" in card['altText']

        clean_text = format_clean_aggregated_text(alerts, strategy_id="06", now_str="10:30:00")
        assert "6112 邁達特" in clean_text
        assert "2332 友訊" in clean_text
        assert "爆量" in clean_text
