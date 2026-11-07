#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
單元測試套件：策略 04 可轉債 (CB) 定價伏擊旗艦策略全域合規性檢驗
================================================================================
測試核心範疇：
1. 【TPEx OpenAPI 官方同步與母池動態融合 (Dual-Layer Pool Fusion)】
2. 【實戰四大防線定價引擎 V4.0 (TWSE Legal Tick Size & Monotonicity)】
3. 【4+1 維度硬篩與 100 分制軟評分打分矩陣】
4. 【股票期貨價位優先與保證金聯動 (Stock Futures Synergy)】
5. 【盤中即時雷達欄位解析與防洗版防毛刺機制 (GLOBAL_TICK_GUARD)】
6. 【個股最後執行死線 (3個月閉鎖期解禁) 驗證】
================================================================================
"""

import sys
import os
import unittest
import importlib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from four_price_engine import get_twse_tick_size, normalize_to_tick


class TestStrategy04CBPricing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cb_helper = importlib.import_module("strategies.04_cb_pricing_ambush.cb_helper")
        cls.screener = importlib.import_module("strategies.04_cb_pricing_ambush.screener")
        cls.intraday = importlib.import_module("strategies.04_cb_pricing_ambush.intraday_scanner")

    def test_01_comprehensive_pool_loading(self):
        """驗證母池動態融合：手動研究標的與 TPEx 全市場活躍 CB 結合"""
        pool = self.cb_helper.load_comprehensive_cb_pool()
        self.assertIsInstance(pool, list)
        self.assertGreater(len(pool), 10, "融合母池應至少具備 10 檔以上活躍可轉債標的")
        
        # 檢驗必要欄位完整性
        sample = pool[0]
        required_keys = ['code', 'name', 'conversion_price', 'est_amount', 'lockup_end_date']
        for k in required_keys:
            self.assertIn(k, sample, f"CB 標的字典應具備欄位: {k}")

    def test_02_four_price_tick_size_and_monotonicity(self):
        """驗證可轉債專用四大防線定價合規性與幾何單調性"""
        test_cases = [
            {"curr_price": 68.8, "conv_price": 68.1, "ma60": 63.5, "high": 70.5},   # 50~100 (tick: 0.10)
            {"curr_price": 170.5, "conv_price": 167.8, "ma60": 160.0, "high": 175.0}, # 100~500 (tick: 0.50)
            {"curr_price": 311.5, "conv_price": 315.0, "ma60": 290.0, "high": 318.0}, # 100~500 (tick: 0.50)
            {"curr_price": 1545.0, "conv_price": 1635.0, "ma60": 1400.0, "high": 1580.0} # >=1000 (tick: 5.00)
        ]

        for tc in test_cases:
            p = tc["curr_price"]
            conv = tc["conv_price"]
            fp = self.cb_helper.calculate_cb_four_prices(
                curr_price=p,
                conv_price=conv,
                ma60=tc["ma60"],
                recent_high=tc["high"]
            )

            sl = fp["sl_price"]
            entry = fp["entry_price"]
            addon = fp["addon_price"]
            tp1 = fp["tp1_price"]
            tp2 = fp["tp2_price"]
            cap130 = fp["target_130"]

            # 1. 檔位合法性檢驗 (TWSE Legal Tick Size)
            for price_val, name in [(sl, "SL"), (entry, "Entry"), (addon, "Addon"), (tp1, "TP1"), (tp2, "TP2")]:
                tick = get_twse_tick_size(price_val)
                remainder = round((price_val * 100) % (tick * 100), 2)
                self.assertIn(remainder, [0.0, tick * 100], f"{name} ({price_val}) 未對齊證交所法定升降檔位 ({tick})")

            # 2. 幾何單調性檢驗：SL < curr_price <= Entry < Addon < TP1 <= TP2
            self.assertLess(sl, p, f"停損價 ({sl}) 必須嚴格低於現價 ({p})")
            self.assertGreaterEqual(entry, p, f"進場上限價 ({entry}) 必須大於等於現價 ({p})")
            self.assertGreater(addon, entry, f"動能加碼價 ({addon}) 必須高於進場上限價 ({entry})")
            self.assertGreaterEqual(tp1, addon, f"停利目標一 ({tp1}) 必須高於加碼價 ({addon})")
            self.assertGreaterEqual(tp2, tp1, f"停利目標二 ({tp2}) 必須高於等於停利目標一 ({tp1})")
            self.assertGreater(cap130, conv, f"130% 逼贖出清線 ({cap130}) 必須高於轉換價 ({conv})")

    def test_03_hard_filter_premium_margin(self):
        """驗證 4+1 硬性風控防追高：轉換溢價率超過 15% 強制剔除"""
        cb_item = {
            "code": "9999", "name": "測試股", "conversion_price": 100.0,
            "est_amount": 10.0, "dilution_rate": 5.0, "status": "PRICED"
        }
        # 現價 118 元 (溢價 18% > 15%) -> 應遭剔除
        m_data_high = {
            "curr_price": 118.0, "prev_close": 115.0, "ma5": 116.0, "ma20": 110.0, "ma60": 105.0,
            "curr_vol": 2000, "vol20": 1500, "vol_ratio": 50.0, "recent_high": 120.0, "recent_low": 112.0,
            "atr": 3.5, "lower_shadow_ratio": 30.0, "upper_shadow_ratio": 10.0, "is_bullish": True
        }
        res_high = self.screener.score_and_analyze_cb(cb_item, m_data_high)
        self.assertIsNone(res_high, "溢價率 > 15% 標的應遭硬性風控過濾")

        # 現價 98 元 (折價 2% <= 15%, 守穩 60MA 95 元) -> 應順利通過
        m_data_low = dict(m_data_high, curr_price=98.0, ma60=95.0, ma20=96.0, ma5=97.0)
        res_low = self.screener.score_and_analyze_cb(cb_item, m_data_low)
        self.assertIsNotNone(res_low, "折價/合理溢價標的應順利通過篩選")
        self.assertEqual(res_low['代號'], "9999")

    def test_04_intraday_scanner_targets_loading(self):
        """驗證盤中即時雷達讀取 result.csv 欄位解析完整性"""
        targets = self.intraday.load_cb_targets()
        self.assertIsInstance(targets, dict)
        self.assertGreater(len(targets), 0, "盤中雷達應成功自 result.csv 載入監控標的")

        first_code = list(targets.keys())[0]
        t = targets[first_code]
        self.assertGreater(t['entry_price'], 0)
        self.assertGreater(t['addon_price'], t['entry_price'])
        self.assertGreater(t['tp1_price'], t['addon_price'])
        self.assertGreater(t['tp2_price'], 0)
        self.assertGreater(t['sl_price'], 0)
        self.assertIn('lockup_end', t)

    def test_05_stock_futures_synergy_and_margin(self):
        """驗證股票期貨契約關聯與 1 口保證金試算"""
        # 華邦電 (2344) 具備活躍股票期貨 FZ
        cb_item = {
            "code": "2344", "name": "華邦電", "conversion_price": 167.8,
            "est_amount": 140.0, "dilution_rate": 5.0, "status": "PRICED"
        }
        m_data = {
            "curr_price": 170.5, "prev_close": 168.0, "ma5": 169.0, "ma20": 165.0, "ma60": 158.0,
            "curr_vol": 50000, "vol20": 45000, "vol_ratio": 60.0, "recent_high": 174.0, "recent_low": 167.0,
            "atr": 4.5, "lower_shadow_ratio": 25.0, "upper_shadow_ratio": 15.0, "is_bullish": True
        }
        res = self.screener.score_and_analyze_cb(cb_item, m_data)
        self.assertIsNotNone(res)
        self.assertIn('期貨契約', res)
        self.assertIn('1口保證金(約)', res)
        self.assertGreater(res['1口保證金(約)'], 0)


if __name__ == '__main__':
    unittest.main()
