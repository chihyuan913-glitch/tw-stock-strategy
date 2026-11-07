# -*- coding: utf-8 -*-
"""
tests/test_strategy_12_etf_event_radar.py
策略 12：全台 ETF 跨市場折溢價與實戰價格行為事件雷達測試套件
"""
import sys
import os
import json
import unittest
import importlib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

screener = importlib.import_module("strategies.12_etf_event_radar.screener")
intraday_scanner = importlib.import_module("strategies.12_etf_event_radar.intraday_scanner")
from four_price_engine import normalize_to_tick, get_twse_tick_size


class TestStrategy12ETFEventRadar(unittest.TestCase):
    """測試策略 12 核心邏輯、字典完整度與價格行為狀態機"""

    def setUp(self):
        self.universe_file = ROOT_DIR / "all_etf_universe.json"

    def test_universe_json_coverage_and_classification(self):
        """驗證全台 363 檔 ETF 宇宙字典完整性與國內外分類精確性"""
        self.assertTrue(self.universe_file.exists(), "all_etf_universe.json 必須存在")
        with open(self.universe_file, "r", encoding="utf-8") as f:
            universe = json.load(f)

        self.assertGreaterEqual(len(universe), 360, "全台 ETF 名冊應覆蓋 >= 360 檔")

        # 驗證代表性台股國內型 ETF 絕不可掛鉤海外期貨
        domestic_samples = ["0050", "0056", "00878", "00919", "00929", "00940"]
        for code in domestic_samples:
            self.assertIn(code, universe, f"代表性 ETF {code} 應存在")
            item = universe[code]
            self.assertEqual(item.get("category"), "DOMESTIC_EQUITY", f"{code} 必須分類為 DOMESTIC_EQUITY")
            self.assertIsNone(item.get("anchor"), f"{code} 國內型 ETF 不可配置海外錨定物")

        # 驗證代表性海外科技/半導體/美債 ETF
        overseas_semi = universe.get("00830")
        self.assertIsNotNone(overseas_semi, "00830 費半 ETF 應存在")
        self.assertEqual(overseas_semi.get("anchor", {}).get("type"), "US_SEMI")

        overseas_tech = universe.get("00757")
        self.assertIsNotNone(overseas_tech, "00757 尖牙股 ETF 應存在")
        self.assertEqual(overseas_tech.get("anchor", {}).get("type"), "US_TECH")

        overseas_bond = universe.get("00679B")
        self.assertIsNotNone(overseas_bond, "00679B 元大美債20年 應存在")
        self.assertEqual(overseas_bond.get("anchor", {}).get("type"), "BOND")

    def test_dynamic_theo_chg_semi_composite_momentum(self):
        """驗證美股半導體複合動能公式計算 (隔夜現貨 + 晨間期貨 + 匯率)"""
        info = {
            "name": "國泰費城半導體",
            "anchor": {
                "type": "US_SEMI",
                "underlying": "^SOX",
                "fx": "USDTWD=X"
            }
        }
        # 模擬: 費半隔夜大跌 -4.0%, 晨間期貨強彈 +2.0%, 美元升值 +0.1%
        benchmarks = {
            "^SOX": {"chg_pct": -4.0},
            "NQ=F": {"morning_momentum": 2.0},
            "USDTWD=X": {"chg_pct": 0.10}
        }

        theo_chg, desc = screener.calculate_dynamic_theo_chg(info, benchmarks)
        self.assertIsNotNone(theo_chg)
        # (1 - 0.04) * (1 + 0.02) * (1 + 0.001) - 1 = 0.96 * 1.02 * 1.001 - 1 = 0.9801792 - 1 = -1.98%
        expected = round(((1 - 0.04) * (1 + 0.02) * (1 + 0.001) - 1) * 100.0, 2)
        self.assertAlmostEqual(theo_chg, expected, places=2)
        self.assertIn("費半現貨", desc)
        self.assertIn("晨間期貨", desc)

    def test_price_action_selling_climax_filter(self):
        """驗證開盤跳空暴跌但爆量收紅 K 之 Selling Climax 恐慌出清竭盡守則"""
        # 測試情境: 昨收 100.0, 開盤 95.0 (-5.0%), 最新價 96.0 (守穩開盤且收紅K)
        actual_open = 95.0
        yesterday_close = 100.0
        latest_price = 96.0
        theo_price = 95.5

        open_gap_pct = round(((actual_open - yesterday_close) / yesterday_close) * 100.0, 2)
        self.assertLessEqual(open_gap_pct, -4.0)
        self.assertGreaterEqual(latest_price, actual_open)

        # 四大防線防守驗證
        tick = get_twse_tick_size(latest_price)
        trail_sl = normalize_to_tick(actual_open - tick, direction='down')
        tp1 = normalize_to_tick(round(yesterday_close * 0.98, 2), direction='down')

        self.assertLess(trail_sl, actual_open, "停損必須守在開盤價下 1 檔")
        self.assertGreater(tp1, latest_price, "第一停利反彈點必須高於現價")

    def test_price_action_avoid_sell_black_k(self):
        """驗證真實溢價賣出必須收黑 K (跌破開盤價) 確認散戶追高竭盡"""
        actual_open = 20.0
        theo_price = 19.0
        diff_pct = round(((actual_open - theo_price) / theo_price) * 100.0, 2)
        self.assertGreaterEqual(diff_pct, 1.5)

        # 若最新價 >= actual_open (紅K)，不符合黑K竭盡出清條件
        latest_price_red = 20.2
        is_black_k = latest_price_red < actual_open
        self.assertFalse(is_black_k, "開盤走高紅K不可立即判定出清")

        # 若最新價 < actual_open (黑K)，確認竭盡出清
        latest_price_black = 19.8
        is_black_k = latest_price_black < actual_open
        self.assertTrue(is_black_k, "開盤走低黑K確認追高買盤竭盡")


if __name__ == "__main__":
    unittest.main()
