"""
Strategy 11: 營收創高與量價突破成長動能策略 (Volume Breakout Pro 2.0)
單元與整合測試集
"""

import os
import sys
import unittest
import importlib.util
import pandas as pd
import numpy as np

# 加入根目錄至 sys.path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from revenue_helper import fetch_monthly_revenue, get_stock_revenue
from adaptive_regime_manager import get_strategy_adaptive_params
from four_price_engine import calculate_four_prices_long

# 動態加載 11_revenue_volume_breakout 模組
screener_path = os.path.join(ROOT_DIR, "strategies", "11_revenue_volume_breakout", "screener.py")
spec = importlib.util.spec_from_file_location("strategy_11_screener", screener_path)
s11 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s11)

calculate_rsi = s11.calculate_rsi
calculate_obv = s11.calculate_obv
evaluate_vcp_compression = s11.evaluate_vcp_compression
check_market_regime = s11.check_market_regime
STANDARD_COLUMNS = s11.STANDARD_COLUMNS
STRATEGY_ID = s11.STRATEGY_ID


class TestStrategy11RevenueBreakout(unittest.TestCase):
    """測試策略 11 的核心篩選、營收維度、VCP 收斂、OBV 能量潮與四大防線規範"""

    def test_revenue_helper_caching_and_enrichment(self):
        """驗證 revenue_helper 取得上市櫃營收與個股特徵提取"""
        rev_dict = fetch_monthly_revenue(force=False)
        self.assertIsInstance(rev_dict, dict)
        self.assertGreater(len(rev_dict), 1000)

        # 檢驗已知主要權值股具備完整營收欄位
        sample = get_stock_revenue("2330", rev_dict)
        self.assertIsNotNone(sample)
        self.assertIn("yoy_pct", sample)
        self.assertIn("mom_pct", sample)
        self.assertIn("is_new_high_candidate", sample)
        self.assertIn("revenue_tier", sample)
        self.assertIn("rev_acceleration", sample)

    def test_adaptive_regime_params(self):
        """驗證 Strategy 11 在自適應市場位階模組中的註冊與參數派發"""
        params = get_strategy_adaptive_params("11")
        self.assertIn("rvol_min", params)
        self.assertIn("bias_max_pct", params)
        self.assertIn("tp1_pct", params)
        self.assertIn("tp2_pct", params)
        self.assertIn("allow_a_track", params)
        self.assertGreaterEqual(params["rvol_min"], 1.5)
        self.assertLessEqual(params["bias_max_pct"], 10.0)

    def test_vcp_compression_evaluation(self):
        """驗證 VCP 波動收斂計算與量縮判定"""
        # 構造 60 天 K 線數據 (前 30 天窄幅整理，振幅 6%，中段量縮沉澱)
        dates = pd.date_range("2026-01-01", periods=60)
        np.random.seed(42)
        closes = 100 + np.sin(np.linspace(0, 3, 60)) * 2.5
        highs = closes + 0.8
        lows = closes - 0.8
        vols = np.full(60, 1000)
        vols[35:45] = 400  # 量縮沉澱日 (低於 60% 均量)

        df_hist = pd.DataFrame({
            "Open": closes,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": vols
        }, index=dates)

        amp_pct, has_dryup = evaluate_vcp_compression(df_hist)
        self.assertLess(amp_pct, 12.0, "VCP 振幅應小於 12%")
        self.assertTrue(has_dryup, "應檢測出量縮沉澱特徵")

    def test_obv_breakout_detection(self):
        """驗證 OBV 能量潮創高指標"""
        dates = pd.date_range("2026-01-01", periods=30)
        closes = np.linspace(100, 120, 30) # 連續上漲
        highs = closes + 1
        lows = closes - 1
        vols = np.full(30, 2000)

        df_hist = pd.DataFrame({
            "Open": closes,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": vols
        }, index=dates)

        is_obv_breakout = calculate_obv(df_hist)
        self.assertTrue(is_obv_breakout, "持續上揚並帶量應判定 OBV 創新高")

    def test_rsi_calculation(self):
        """驗證 RSI 技術指標計算"""
        series = pd.Series([100, 102, 104, 103, 105, 108, 110, 112, 111, 115, 117, 120, 122, 125, 128])
        rsi_val = calculate_rsi(series, period=14)
        self.assertGreater(rsi_val, 60.0)
        self.assertLessEqual(rsi_val, 100.0)

    def test_four_price_strict_monotonicity_and_ticks(self):
        """驗證四大防線定價引擎 V4.0 在突破策略下的嚴格幾何單調性與法定檔位合規"""
        test_prices = [38.5, 98.6, 185.0, 480.0, 850.0]
        adap_params = get_strategy_adaptive_params("11")

        for close_p in test_prices:
            res = calculate_four_prices_long(
                curr_price=close_p,
                prev_close=close_p * 0.98,
                high=close_p * 1.02,
                low=close_p * 0.97,
                ma20=close_p * 0.96,
                atr=close_p * 0.035,
                regime_params=adap_params
            )
            entry = res["entry_price"]
            addon = res["addon_price"]
            tp1 = res["tp1_price"]
            tp2 = res["tp2_price"]
            sl = res["sl_price"]

            # 驗證嚴格幾何單調性: SL < Close <= Entry < Addon < TP1 <= TP2
            self.assertLess(sl, close_p, f"SL 必須小於現價: SL={sl}, Close={close_p}")
            self.assertGreaterEqual(entry, close_p, f"Entry 必須大於等於現價: Entry={entry}, Close={close_p}")
            self.assertGreater(addon, entry, f"Addon 必須大於 Entry: Addon={addon}, Entry={entry}")
            self.assertGreater(tp1, addon, f"TP1 必須大於 Addon: TP1={tp1}, Addon={addon}")
            self.assertGreaterEqual(tp2, tp1, f"TP2 必須大於等於 TP1: TP2={tp2}, TP1={tp1}")

            # 驗證健康盈虧比
            self.assertGreaterEqual(res["risk_reward_ratio"], 0.7, f"盈虧比應具備實戰性: RR={res['risk_reward_ratio']:.2f}")

    def test_result_columns_integrity(self):
        """驗證報表欄位符合 Master Command Rules 標準規範 (28 欄位)"""
        required_cols = [
            "證券代號", "證券名稱", "收盤價", "進場價位", "加碼價位",
            "停利TP1", "停利TP2", "停損價位", "梯隊標籤", "營收YoY%",
            "營收創高", "均量比Rvol", "月線乖離%", "評分", "隔日沖風控"
        ]
        for col in required_cols:
            self.assertIn(col, STANDARD_COLUMNS, f"STANDARD_COLUMNS 缺少關鍵欄位: {col}")


if __name__ == "__main__":
    unittest.main()
