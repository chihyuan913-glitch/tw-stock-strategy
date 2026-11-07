# -*- coding: utf-8 -*-
"""
tests/test_market_analyst.py
台股盤後宏觀與信用交易全戰報分析引擎測試套件
"""
import sys
import os
import json
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import market_analyst


class TestMarketAnalyst(unittest.TestCase):
    """測試盤後宏觀與信用交易分析模組之解析函數與戰報結構"""

    def test_to_roc_date(self):
        """驗證西元 YYYYMMDD 轉民國年 ROC 格式轉換"""
        self.assertEqual(market_analyst.to_roc_date("20261106"), "115/11/06")
        self.assertEqual(market_analyst.to_roc_date("20240101"), "113/01/01")

    def test_clean_num(self):
        """驗證字串數值清理 (移除逗號、百分比與空白)"""
        self.assertEqual(market_analyst.clean_num("1,234,567.89"), 1234567.89)
        self.assertEqual(market_analyst.clean_num("-12.5%"), -12.5)
        self.assertEqual(market_analyst.clean_num("  98.5 "), 98.5)
        self.assertEqual(market_analyst.clean_num(None), 0.0)
        self.assertEqual(market_analyst.clean_num("invalid"), 0.0)

    def test_report_artifacts_generation(self):
        """驗證 market_analysis_report.md 與 json 結構完整性"""
        res = market_analyst.run_market_analysis(send_line=False, force=True)
        self.assertIsNotNone(res)
        self.assertIn("index", res)
        self.assertIn("margin", res)
        self.assertIn("institutions", res)
        self.assertIn("regime", res)

        # 驗證實體檔案是否成功產出
        report_md = ROOT_DIR / "market_analysis_report.md"
        report_json = ROOT_DIR / "market_analysis_report.json"
        self.assertTrue(report_md.exists(), "market_analysis_report.md 必須存在")
        self.assertTrue(report_json.exists(), "market_analysis_report.json 必須存在")

        with open(report_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("index", data)
        self.assertIn("margin", data)
        self.assertIn("institutions", data)
        self.assertIn("regime", data)


if __name__ == "__main__":
    unittest.main()
