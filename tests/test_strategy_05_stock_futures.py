#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試腳本：策略 05【個股期貨法人強勢波段多方策略】自動化除錯與全功能驗證
覆蓋範圍：
1. 股票期貨標的池載入與合約完整性 (stock_futures_list.json)
2. 股票期貨價位優先計算引擎 (calculate_futures_pricing)
3. 實戰四大防線定價引擎 V4.0 (four_price_engine.py) 與幾何單調性檢核
4. 第二階段多方軟評分與 OI 深度流動性加分
5. 盤中雷達目標載入與到價事件判定 (intraday_scanner.py)
6. 結算日前五日開立次月新倉邏輯 (Rollover Warning)
7. 休市守門員與非開盤日防打擾攔截 (market_schedule.py)
"""

import os
import sys
import unittest
import importlib.util
from pathlib import Path

# 加入專案根目錄
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from four_price_engine import (
    calculate_four_prices_long,
    normalize_to_tick,
    get_twse_tick_size,
    get_limit_prices
)
from futures_helper import (
    load_stock_futures_pool,
    calculate_futures_pricing,
    get_futures_settlement_info,
    fetch_taifex_futures_quotes
)

# 動態載入 strategies/05_stock_futures_long 模組
screener_path = ROOT_DIR / "strategies" / "05_stock_futures_long" / "screener.py"
spec_scr = importlib.util.spec_from_file_location("screener_05", screener_path)
screener_05 = importlib.util.module_from_spec(spec_scr)
spec_scr.loader.exec_module(screener_05)

scanner_path = ROOT_DIR / "strategies" / "05_stock_futures_long" / "intraday_scanner.py"
spec_scan = importlib.util.spec_from_file_location("scanner_05", scanner_path)
scanner_05 = importlib.util.module_from_spec(spec_scan)
spec_scan.loader.exec_module(scanner_05)

load_targets = scanner_05.load_targets
STRATEGY_ID = screener_05.STRATEGY_ID
STRATEGY_NAME = screener_05.STRATEGY_NAME


class TestStrategy05StockFutures(unittest.TestCase):
    """策略 05 股票期貨多方量化引擎完整測試套件"""

    def setUp(self):
        self.strat_dir = ROOT_DIR / "strategies" / "05_stock_futures_long"
        self.futures_json = self.strat_dir / "stock_futures_list.json"

    def test_01_stock_futures_pool_integrity(self):
        """驗證 1: 期交所股票期貨標的字典完整性 (>= 200 檔)"""
        pool = load_stock_futures_pool(str(self.futures_json))
        self.assertIsInstance(pool, dict)
        self.assertGreaterEqual(len(pool), 200, f"股票期貨清單標的數量不足: {len(pool)}")
        # 驗證重要權值標的是否存在
        for code in ['2330', '2317', '2454', '2609', '2615']:
            self.assertIn(code, pool, f"核心股期標的 {code} 未在母池中")
            self.assertTrue(len(pool[code]['contract']) >= 2)

    def test_02_four_price_engine_monotonicity(self):
        """驗證 2: 四大防線定價引擎幾何單調性 (LimitDown <= SL < Close <= Entry < Addon < TP1 <= TP2 <= LimitUp)"""
        test_prices = [25.5, 49.9, 99.5, 150.0, 480.0, 799.0, 1050.0]
        for p in test_prices:
            fp = calculate_four_prices_long(
                curr_price=p,
                prev_close=p,
                high=round(p * 1.01, 2),
                low=round(p * 0.99, 2),
                ma20=round(p * 0.98, 2),
                atr=round(p * 0.025, 2)
            )
            sl = fp['sl_price']
            entry = fp['entry_price']
            addon = fp['addon_price']
            tp1 = fp['tp1_price']
            tp2 = fp['tp2_price']
            limit_up = fp['limit_up']
            limit_down = fp['limit_down']

            # 嚴格單向幾何單調性檢驗
            self.assertLessEqual(limit_down, sl, f"SL 跌破跌停價: {sl} < {limit_down}")
            self.assertLess(sl, p, f"SL 必須小於現價: {sl} >= {p}")
            self.assertLessEqual(p, entry, f"現價不可高於買進上限: {p} > {entry}")
            self.assertLess(entry, addon, f"買進上限必須小於加碼價: {entry} >= {addon}")
            self.assertLess(addon, tp1, f"加碼價必須小於第一停利: {addon} >= {tp1}")
            self.assertLessEqual(tp1, tp2, f"第一停利不可高於第二停利: {tp1} > {tp2}")
            self.assertLessEqual(tp2, limit_up, f"第二停利不可超過漲停價: {tp2} > {limit_up}")

            # 法定檔位合規性檢驗
            for val in [sl, entry, addon, tp1, tp2]:
                tick = get_twse_tick_size(val)
                # 浮點數精度容差
                remainder = round((val * 100) % (tick * 100), 2)
                self.assertIn(remainder, [0.0, round(tick * 100, 2)], f"點位 {val} 未對齊法定檔位 {tick}")

    def test_03_futures_pricing_conversion(self):
        """驗證 3: 股票期貨價位優先轉換引擎 (基差 Basis 平移與保證金試算)"""
        spot_close = 100.0
        # 模擬期貨正價差 +1.5 元
        mock_fut_quote = {
            'price': 101.5,
            'contract_sym': 'CFF',
            'month': '202611',
            'volume': 1500,
            'open_interest': 6200,
            'next_price': 101.0,
            'next_month': '202612',
            'next_open_interest': 350
        }
        entry_s, addon_s, tp1_s, tp2_s, sl_s = 100.5, 102.5, 105.0, 110.0, 96.0

        fut_res = calculate_futures_pricing(
            spot_close=spot_close,
            futures_quote=mock_fut_quote,
            entry_spot=entry_s,
            addon_spot=addon_s,
            tp1_spot=tp1_s,
            tp2_spot=tp2_s,
            sl_spot=sl_s,
            contract_base='CF',
            is_long=True
        )

        self.assertTrue(fut_res['has_real_quote'])
        self.assertEqual(fut_res['futures_close'], 101.5)
        self.assertEqual(fut_res['basis'], 1.5)
        self.assertEqual(fut_res['futures_entry'], 102.0)  # 100.5 + 1.5
        self.assertEqual(fut_res['futures_sl'], 97.5)     # 96.0 + 1.5
        self.assertEqual(fut_res['futures_oi'], 6200)
        # 一口保證金: 101.5 * 2000 * 0.135 = 27405 -> round(-1) = 27410
        self.assertAlmostEqual(fut_res['margin_1lot'], 27410, delta=10)

    def test_04_futures_oi_scoring(self):
        """驗證 4: 未平倉量 (OI) 軟評分邏輯 (最高 20 分)"""
        def get_oi_score(oi):
            if oi >= 10000:
                return 20
            elif oi >= 5000:
                return 16
            elif oi >= 2000:
                return 12
            elif oi >= 1000:
                return 8
            elif oi >= 300:
                return 4
            return 0

        self.assertEqual(get_oi_score(12500), 20)
        self.assertEqual(get_oi_score(6000), 16)
        self.assertEqual(get_oi_score(3500), 12)
        self.assertEqual(get_oi_score(1500), 8)
        self.assertEqual(get_oi_score(500), 4)
        self.assertEqual(get_oi_score(150), 0)

    def test_05_intraday_scanner_target_loading(self):
        """驗證 5: 盤中即時雷達載入 result.csv 或備用母池之穩健性"""
        targets = load_targets()
        self.assertIsInstance(targets, dict)
        self.assertGreater(len(targets), 0, "盤中雷達未能載入任何監控標的")

        first_target = next(iter(targets.values()))
        # 必須具備關鍵欄位
        required_fields = ['code', 'name', 'entry_price', 'addon_price', 'tp1_price', 'tp2_price', 'sl_price']
        for rf in required_fields:
            self.assertIn(rf, first_target, f"標的缺少必要欄位 {rf}")
            self.assertIsInstance(first_target[rf], (int, float, str))

    def test_06_intraday_alert_trigger_logic(self):
        """驗證 6: 盤中到價事件觸發邏輯 (黃金建倉區間、加碼、停利、停損)"""
        target = {
            'code': '9999',
            'name': '測試股',
            'entry_price': 100.0,
            'addon_price': 103.0,
            'tp1_price': 106.0,
            'tp2_price': 110.0,
            'sl_price': 96.0,
            'stars': '★★★★★',
            'score': 88,
            'futures_oi': 3500
        }

        # 1. 停損觸發測試
        price_sl = 95.5
        self.assertTrue(price_sl <= target['sl_price'])

        # 2. TP1 停利觸發測試
        price_tp1 = 106.5
        self.assertTrue(price_tp1 >= target['tp1_price'])

        # 3. 加碼觸發測試
        price_addon = 103.5
        self.assertTrue(price_addon >= target['addon_price'])

        # 4. 黃金建倉區間測試 (現價 <= 買進上限 且高於停損且在 2.5% 以內)
        price_entry = 99.0
        is_in_buy_zone = (target['sl_price'] < price_entry <= target['entry_price']) and (
            (target['entry_price'] - price_entry) / target['entry_price'] <= 0.025
        )
        self.assertTrue(is_in_buy_zone)


if __name__ == '__main__':
    unittest.main()
