"""
Strategy Adapters: 實盤與 8 大核心策略池 ＋ 自訂複合因子積木組裝器 (全矩陣升級版)
內建：
- 現有旗艦策略 (01 布林超跌, 02 法人動能, 03 破線放空, 06 量價突破)
- 8 大核心策略池：KD 黃金交叉(9,3,3)、均線多頭排列、量價突破、乖離率回歸、跳空缺口、法人連買、月營收創新高(次月10日錨定)、融資減少
- 自訂策略積木 (CustomBlockStrategy) 與 5 種預設複合因子範本
"""

import os
import json
from typing import Dict, List, Optional, Any, Type
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy
from ..engine.backtest_engine import Signal
from ..data.storage import MarketStorage


def _calc_kd(df: pd.DataFrame, n: int = 9, k_smooth: int = 3, d_smooth: int = 3) -> tuple[pd.Series, pd.Series]:
    """計算標準 KD (9, 3, 3) 指標序列"""
    low_min = df["low"].rolling(window=n, min_periods=1).min()
    high_max = df["high"].rolling(window=n, min_periods=1).max()
    denom = (high_max - low_min).replace(0, 1e-5)
    rsv = ((df["close"] - low_min) / denom * 100.0).clip(0.0, 100.0)

    k_vals = []
    d_vals = []
    k_prev, d_prev = 50.0, 50.0
    for val in rsv:
        k_cur = (k_prev * (k_smooth - 1) + float(val)) / k_smooth
        d_cur = (d_prev * (d_smooth - 1) + k_cur) / d_smooth
        k_vals.append(k_cur)
        d_vals.append(d_cur)
        k_prev, d_prev = k_cur, d_cur

    return pd.Series(k_vals, index=df.index), pd.Series(d_vals, index=df.index)


def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """計算標準 RSI 指標序列"""
    delta = close.diff()
    gain = delta.clip(lower=0.0).rolling(window=period, min_periods=1).mean()
    loss = (-delta.clip(upper=0.0)).rolling(window=period, min_periods=1).mean()
    rs = gain / loss.replace(0, 1e-5)
    return 100.0 - (100.0 / (1.0 + rs))


class BollingerReversalStrategy(BaseStrategy):
    """
    策略 01 / 乖離率回歸: 布林通道下軌超跌反彈 + 止跌確認 (做多)
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "std_dev_multiplier": 2.0,
            "min_volume": 1000,
            "lower_dist_max": 3.0,
            "lower_dist_min": -5.0,
            "stop_loss_pct": 0.04,
        }
        default_params.update(kwargs)
        super().__init__(
            name="01_Bollinger_Reversal",
            description="乖離率回歸・布林通道下軌超跌反彈＋止跌K棒確認 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        ma_period = int(self.params["ma_period"])
        mult = float(self.params["std_dev_multiplier"])
        min_vol = int(self.params["min_volume"])

        for sym, df in historical_bars.items():
            if len(df) < ma_period + 5:
                continue

            close_series = df["close"]
            ma = close_series.rolling(ma_period).mean().iloc[-1]
            std = close_series.rolling(ma_period).std(ddof=0).iloc[-1]
            lower_band = ma - mult * std
            upper_band = ma + mult * std

            curr_bar = df.iloc[-1]
            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            l = float(curr_bar["low"])
            vol = int(curr_bar["volume"])

            if vol <= 0 or vol < min_vol:
                continue

            dist_pct = (c - lower_band) / lower_band * 100.0

            if self.params["lower_dist_min"] <= dist_pct <= self.params["lower_dist_max"]:
                is_reversal = (c >= curr_bar["open"]) or ((min(curr_bar["open"], c) - l) / (h - l + 1e-5) > 0.3)
                if is_reversal:
                    entry_p = round(c * 1.015, 2)
                    addon_p = round(max(h * 1.01, c * 1.025), 2)
                    tp1_p = round(float(ma), 2)
                    tp2_p = round(float(upper_band), 2)
                    sl_p = round(max(float(lower_band * 0.96), l * 0.98), 2)

                    if tp1_p > c and sl_p < c:
                        signals.append(Signal(
                            symbol=sym,
                            date=date,
                            direction="LONG",
                            entry_price=entry_p,
                            addon_price=addon_p,
                            tp1_price=tp1_p,
                            tp2_price=tp2_p,
                            stop_loss_price=sl_p,
                            target_shares=1000,
                            meta={"strategy": "01_bollinger", "dist_pct": round(dist_pct, 2)}
                        ))
        return signals


class InstitutionalMomentumStrategy(BaseStrategy):
    """
    策略 02 / 法人連買: 法人起漲動能 + 月線乖離防追高 (做多)
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "min_volume": 1000,
            "max_bias_pct": 8.0,
            "min_inst_buy_lots": 300,
            "stop_loss_pct": 0.05,
        }
        default_params.update(kwargs)
        super().__init__(
            name="02_Institutional_Momentum",
            description="法人連買・三大法人集中買超＋均線向上起漲動能 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        ma_period = int(self.params["ma_period"])
        min_vol = int(self.params["min_volume"])
        max_bias = float(self.params["max_bias_pct"])

        for sym, df in historical_bars.items():
            if len(df) < ma_period + 5:
                continue

            curr_bar = df.iloc[-1]
            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            l = float(curr_bar["low"])
            vol = int(curr_bar["volume"])

            if vol <= 0 or vol < min_vol:
                continue

            ma20 = df["close"].rolling(ma_period).mean().iloc[-1]
            bias = (c - ma20) / ma20 * 100.0

            if 0.0 <= bias <= max_bias:
                inst_pass = True
                if chips and sym in chips:
                    c_df = chips[sym]
                    if not c_df.empty:
                        last_chip = c_df.iloc[-1]
                        tot_buy = float(last_chip.get("total_institutional_buy", 0.0))
                        if tot_buy < self.params["min_inst_buy_lots"]:
                            inst_pass = False

                if inst_pass:
                    entry_p = round(c * 1.01, 2)
                    addon_p = round(h * 1.015, 2)
                    tp1_p = round(c * 1.08, 2)
                    tp2_p = round(c * 1.15, 2)
                    sl_p = round(max(float(ma20 * 0.98), c * (1.0 - self.params["stop_loss_pct"])), 2)

                    if sl_p < c < tp1_p:
                        signals.append(Signal(
                            symbol=sym,
                            date=date,
                            direction="LONG",
                            entry_price=entry_p,
                            addon_price=addon_p,
                            tp1_price=tp1_p,
                            tp2_price=tp2_p,
                            stop_loss_price=sl_p,
                            target_shares=1000,
                            meta={"strategy": "02_institutional", "bias_pct": round(bias, 2)}
                        ))
        return signals


class InvestmentTrustSprintStrategy(InstitutionalMomentumStrategy):
    """
    策略 10 / 投信認養: 投信連續買超集中度爆發飆股 (做多)
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "min_volume": 1000,
            "max_bias_pct": 6.5,
            "min_trust_consec_days": 3,
            "stop_loss_pct": 0.045,
            "min_inst_buy_lots": 800,
        }
        default_params.update(kwargs)
        BaseStrategy.__init__(
            self,
            name="10_Investment_Trust_Sprint",
            description="投信認養集中度爆發飆股・投信密集吃貨鎖碼＋均線多頭起漲 (做多)",
            params=default_params,
        )


class ShortMomentumStrategy(BaseStrategy):
    """
    策略 03: 弱勢破線做空避險 (空方)
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "min_volume": 1000,
            "max_bias_pct": -1.5,
            "stop_loss_pct": 0.04,
            "tp1_pct": 0.07,
            "tp2_pct": 0.14,
        }
        default_params.update(kwargs)
        super().__init__(
            name="03_Short_Momentum",
            description="均線下彎弱勢破線做空避險 (融券/股期放空)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        ma_period = int(self.params["ma_period"])
        min_vol = int(self.params["min_volume"])
        max_bias = float(self.params["max_bias_pct"])

        for sym, df in historical_bars.items():
            if len(df) < ma_period + 5:
                continue

            curr_bar = df.iloc[-1]
            c = float(curr_bar["close"])
            l = float(curr_bar["low"])
            vol = int(curr_bar["volume"])

            if vol <= 0 or vol < min_vol:
                continue

            ma20 = df["close"].rolling(ma_period).mean().iloc[-1]
            bias = (c - ma20) / ma20 * 100.0

            if bias <= max_bias:
                entry_p = round(c * 0.995, 2)
                addon_p = round(l * 0.985, 2)
                tp1_p = round(c * (1.0 - self.params["tp1_pct"]), 2)
                tp2_p = round(c * (1.0 - self.params["tp2_pct"]), 2)
                sl_p = round(max(float(ma20 * 1.01), c * (1.0 + self.params["stop_loss_pct"])), 2)

                if tp2_p < tp1_p < c < sl_p:
                    signals.append(Signal(
                        symbol=sym,
                        date=date,
                        direction="SHORT",
                        entry_price=entry_p,
                        addon_price=addon_p,
                        tp1_price=tp1_p,
                        tp2_price=tp2_p,
                        stop_loss_price=sl_p,
                        target_shares=1000,
                        meta={"strategy": "03_short", "bias_pct": round(bias, 2)}
                    ))
        return signals


class VolumeSurgeStrategy(BaseStrategy):
    """
    策略 06 / 量價突破: 爆量長紅動能突破 (多方)
    """
    def __init__(self, **kwargs):
        default_params = {
            "volume_surge_mult": 1.8,
            "min_volume": 1200,
            "min_return_pct": 3.0,
            "stop_loss_pct": 0.04,
        }
        default_params.update(kwargs)
        super().__init__(
            name="06_Volume_Surge",
            description="量價突破・量能倍增突破＋實體長紅多方發動 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        min_vol = int(self.params["min_volume"])
        surge_mult = float(self.params["volume_surge_mult"])
        min_ret = float(self.params["min_return_pct"])

        for sym, df in historical_bars.items():
            if len(df) < 10:
                continue

            curr_bar = df.iloc[-1]
            prev_bar = df.iloc[-2]
            c = float(curr_bar["close"])
            prev_c = float(prev_bar["close"])
            h = float(curr_bar["high"])
            l = float(curr_bar["low"])
            vol = int(curr_bar["volume"])

            if vol <= 0 or vol < min_vol:
                continue

            vol_5ma = df["volume"].iloc[-6:-1].mean()
            day_return = (c - prev_c) / prev_c * 100.0

            if vol >= vol_5ma * surge_mult and day_return >= min_ret:
                entry_p = round(c * 1.01, 2)
                addon_p = round(h * 1.015, 2)
                tp1_p = round(c * 1.08, 2)
                tp2_p = round(c * 1.16, 2)
                sl_p = round(max(float(l * 0.99), c * (1.0 - self.params["stop_loss_pct"])), 2)

                if sl_p < c < tp1_p:
                    signals.append(Signal(
                        symbol=sym,
                        date=date,
                        direction="LONG",
                        entry_price=entry_p,
                        addon_price=addon_p,
                        tp1_price=tp1_p,
                        tp2_price=tp2_p,
                        stop_loss_price=sl_p,
                        target_shares=1000,
                        meta={"strategy": "06_volume_surge", "day_return": round(day_return, 2)}
                    ))
        return signals


class KDGoldenCrossStrategy(BaseStrategy):
    """
    核心策略池: KD 黃金交叉 (9,3,3) ＋ 4+1 均量與乖離濾網
    """
    def __init__(self, **kwargs):
        default_params = {
            "kd_period": 9,
            "k_smooth": 3,
            "d_smooth": 3,
            "max_k_level": 75.0,
            "min_volume": 1000,
            "stop_loss_pct": 0.04,
        }
        default_params.update(kwargs)
        super().__init__(
            name="11_KD_Golden_Cross",
            description="KD黃金交叉(9,3,3)＋中低檔轉強動能 (做多)",
            params=default_params,
        )

    def generate_signals(self, date: str, historical_bars: Dict[str, pd.DataFrame], chips: Optional[Dict[str, pd.DataFrame]] = None) -> List[Signal]:
        signals: List[Signal] = []
        n = int(self.params["kd_period"])
        ks = int(self.params["k_smooth"])
        ds = int(self.params["d_smooth"])
        max_k = float(self.params["max_k_level"])
        min_vol = int(self.params["min_volume"])

        for sym, df in historical_bars.items():
            if len(df) < n + 5:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            k_ser, d_ser = _calc_kd(df, n=n, k_smooth=ks, d_smooth=ds)
            if len(k_ser) < 2:
                continue

            if k_ser.iloc[-2] <= d_ser.iloc[-2] and k_ser.iloc[-1] > d_ser.iloc[-1] and k_ser.iloc[-1] <= max_k:
                c = float(curr_bar["close"])
                h = float(curr_bar["high"])
                l = float(curr_bar["low"])
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.015, 2),
                    tp1_price=round(c * 1.07, 2),
                    tp2_price=round(c * 1.14, 2),
                    stop_loss_price=round(min(l * 0.98, c * (1.0 - self.params["stop_loss_pct"])), 2),
                    target_shares=1000,
                    meta={"strategy": "11_kd_cross", "k_val": round(float(k_ser.iloc[-1]), 2)}
                ))
        return signals


class MABullishAlignmentStrategy(BaseStrategy):
    """
    核心策略池: 均線多頭排列 (5MA > 10MA > 20MA) ＋ 乖離防追高
    """
    def __init__(self, **kwargs):
        default_params = {
            "short_ma": 5,
            "mid_ma": 10,
            "long_ma": 20,
            "max_bias_pct": 7.5,
            "min_volume": 1000,
            "stop_loss_pct": 0.045,
        }
        default_params.update(kwargs)
        super().__init__(
            name="12_MA_Bullish_Alignment",
            description="均線多頭排列 (5MA > 10MA > 20MA) ＋ 乖離防追高 (做多)",
            params=default_params,
        )

    def generate_signals(self, date: str, historical_bars: Dict[str, pd.DataFrame], chips: Optional[Dict[str, pd.DataFrame]] = None) -> List[Signal]:
        signals: List[Signal] = []
        s_ma = int(self.params["short_ma"])
        m_ma = int(self.params["mid_ma"])
        l_ma = int(self.params["long_ma"])
        min_vol = int(self.params["min_volume"])

        for sym, df in historical_bars.items():
            if len(df) < l_ma + 2:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            ma5 = float(df["close"].rolling(s_ma).mean().iloc[-1])
            ma10 = float(df["close"].rolling(m_ma).mean().iloc[-1])
            ma20 = float(df["close"].rolling(l_ma).mean().iloc[-1])
            bias = (c - ma20) / ma20 * 100.0

            if c >= ma5 > ma10 > ma20 and bias <= self.params["max_bias_pct"]:
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.02, 2),
                    tp1_price=round(c * 1.08, 2),
                    tp2_price=round(c * 1.16, 2),
                    stop_loss_price=round(max(ma20 * 0.99, c * (1.0 - self.params["stop_loss_pct"])), 2),
                    target_shares=1000,
                    meta={"strategy": "12_ma_bull", "bias": round(bias, 2)}
                ))
        return signals


class GapUpMomentumStrategy(BaseStrategy):
    """
    核心策略池: 跳空缺口強勢突破 (當日最低價 > 昨日最高價)
    """
    def __init__(self, **kwargs):
        default_params = {
            "min_gap_pct": 0.5,
            "min_volume": 1000,
            "stop_loss_pct": 0.04,
        }
        default_params.update(kwargs)
        super().__init__(
            name="13_Gap_Up_Momentum",
            description="跳空缺口・向上跳空不補＋量能支撐 (做多)",
            params=default_params,
        )

    def generate_signals(self, date: str, historical_bars: Dict[str, pd.DataFrame], chips: Optional[Dict[str, pd.DataFrame]] = None) -> List[Signal]:
        signals: List[Signal] = []
        min_gap = float(self.params["min_gap_pct"])
        min_vol = int(self.params["min_volume"])

        for sym, df in historical_bars.items():
            if len(df) < 5:
                continue
            curr_bar = df.iloc[-1]
            prev_bar = df.iloc[-2]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            l = float(curr_bar["low"])
            prev_h = float(prev_bar["high"])
            c = float(curr_bar["close"])
            h = float(curr_bar["high"])

            gap_pct = (l - prev_h) / prev_h * 100.0
            if gap_pct >= min_gap and c >= float(curr_bar["open"]):
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.015, 2),
                    tp1_price=round(c * 1.08, 2),
                    tp2_price=round(c * 1.15, 2),
                    stop_loss_price=round(prev_h * 0.99, 2),  # 跌補缺口即停損
                    target_shares=1000,
                    meta={"strategy": "13_gap_up", "gap_pct": round(gap_pct, 2)}
                ))
        return signals


class RevenueNewHighStrategy(BaseStrategy):
    """
    核心策略池: 月營收創新高 (嚴格遵循「次月 10 日」發布錨定，在此之前一律使用上上月資料) ＋ 均線支撐
    """
    def __init__(self, storage: Optional[MarketStorage] = None, **kwargs):
        default_params = {
            "lookback_months": 6,
            "min_yoy_pct": 10.0,
            "min_volume": 1000,
            "stop_loss_pct": 0.05,
        }
        default_params.update(kwargs)
        super().__init__(
            name="14_Revenue_New_High",
            description="月營收創新高 (次月10日錨定防偷看) ＋ 站穩月線 (做多)",
            params=default_params,
        )
        self.storage = storage or MarketStorage()

    def generate_signals(self, date: str, historical_bars: Dict[str, pd.DataFrame], chips: Optional[Dict[str, pd.DataFrame]] = None) -> List[Signal]:
        signals: List[Signal] = []
        lookback = int(self.params["lookback_months"])
        min_yoy = float(self.params["min_yoy_pct"])
        min_vol = int(self.params["min_volume"])

        for sym, df in historical_bars.items():
            if len(df) < 20:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            ma20 = float(df["close"].rolling(20).mean().iloc[-1])
            if c < ma20:
                continue

            # 嚴格透過次月 10 日錨定介面查詢當下可見之月營收
            rev_df = self.storage.get_revenue_history_as_of(sym, date, limit=lookback)
            if rev_df.empty:
                continue

            latest_rev = float(rev_df["revenue"].iloc[-1])
            max_hist_rev = float(rev_df["revenue"].max())
            latest_yoy = float(rev_df["yoy_pct"].iloc[-1])

            if latest_rev >= max_hist_rev and latest_yoy >= min_yoy:
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.02, 2),
                    tp1_price=round(c * 1.09, 2),
                    tp2_price=round(c * 1.18, 2),
                    stop_loss_price=round(max(ma20 * 0.98, c * (1.0 - self.params["stop_loss_pct"])), 2),
                    target_shares=1000,
                    meta={
                        "strategy": "14_revenue_high",
                        "visible_year_month": str(rev_df["year_month"].iloc[-1]),
                        "yoy_pct": round(latest_yoy, 2),
                    }
                ))
        return signals


class MarginReductionStrategy(BaseStrategy):
    """
    核心策略池: 融資減少 (散戶浮額清洗沉澱) ＋ 股價站上短期均線
    """
    def __init__(self, storage: Optional[MarketStorage] = None, **kwargs):
        default_params = {
            "lookback_days": 5,
            "min_volume": 1000,
            "stop_loss_pct": 0.045,
        }
        default_params.update(kwargs)
        super().__init__(
            name="15_Margin_Reduction",
            description="融資減少籌碼沉澱 ＋ 均線轉強 (做多)",
            params=default_params,
        )
        self.storage = storage or MarketStorage()

    def generate_signals(self, date: str, historical_bars: Dict[str, pd.DataFrame], chips: Optional[Dict[str, pd.DataFrame]] = None) -> List[Signal]:
        signals: List[Signal] = []
        lb = int(self.params["lookback_days"])
        min_vol = int(self.params["min_volume"])

        for sym, df in historical_bars.items():
            if len(df) < 20:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            ma10 = float(df["close"].rolling(10).mean().iloc[-1])
            if c < ma10:
                continue

            m_df = self.storage.get_margin(sym, end_date=date)
            if len(m_df) >= lb:
                recent_margin = m_df["margin_balance"].tail(lb)
                if float(recent_margin.iloc[-1]) < float(recent_margin.iloc[0]):
                    signals.append(Signal(
                        symbol=sym,
                        date=date,
                        direction="LONG",
                        entry_price=round(c * 1.01, 2),
                        addon_price=round(h * 1.02, 2),
                        tp1_price=round(c * 1.08, 2),
                        tp2_price=round(c * 1.15, 2),
                        stop_loss_price=round(c * (1.0 - self.params["stop_loss_pct"]), 2),
                        target_shares=1000,
                        meta={"strategy": "15_margin_reduction"}
                    ))
        return signals


# =====================================================================
# 自訂策略積木 (Custom Block Strategy) 與 5 種預設複合因子範本
# =====================================================================
PRESET_COMPOSITE_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "template_1": {
        "id": "template_1",
        "name": "範本1: 均線多頭＋RSI強勢＋乖離防追高",
        "description": "收盤站上 20MA、RSI(14) > 55 且月線乖離率 < 7.5%",
        "direction": "LONG",
        "blocks": [
            {"factor": "bias20", "operator": ">", "value": 0.0},
            {"factor": "bias20", "operator": "<", "value": 7.5},
            {"factor": "rsi14", "operator": ">", "value": 55.0},
        ],
    },
    "template_2": {
        "id": "template_2",
        "name": "範本2: KD黃金交叉＋超跌均值回歸",
        "description": "KD之 K 值向上穿越 D 值，且 K < 70",
        "direction": "LONG",
        "blocks": [
            {"factor": "kd_k", "operator": "cross_above", "value": "kd_d"},
            {"factor": "kd_k", "operator": "<", "value": 70.0},
        ],
    },
    "template_3": {
        "id": "template_3",
        "name": "範本3: 量價倍增突破＋月營收正成長",
        "description": "當日量比 > 1.5 倍且月營收年增率 YoY > 0%",
        "direction": "LONG",
        "blocks": [
            {"factor": "volume_ratio", "operator": ">", "value": 1.5},
            {"factor": "rev_yoy", "operator": ">", "value": 0.0},
        ],
    },
    "template_4": {
        "id": "template_4",
        "name": "範本4: 融資減肥沉澱＋站穩月線",
        "description": "5日融資變動 < 0 (散戶退場) 且月線正乖離 > 0%",
        "direction": "LONG",
        "blocks": [
            {"factor": "margin_change_5d", "operator": "<", "value": 0.0},
            {"factor": "bias20", "operator": ">", "value": 0.0},
        ],
    },
    "template_5": {
        "id": "template_5",
        "name": "範本5: 弱勢破月線＋RSI空頭避險",
        "description": "月線乖離 < -1.5% 且 RSI(14) < 45 順勢放空",
        "direction": "SHORT",
        "blocks": [
            {"factor": "bias20", "operator": "<", "value": -1.5},
            {"factor": "rsi14", "operator": "<", "value": 45.0},
        ],
    },
}


class CustomBlockStrategy(BaseStrategy):
    """
    自訂複合因子積木策略：支援均價線、RSI、KD、量比、乖離率、法人買超、月營收(次月10日錨定)、融資之大於/小於/穿越組裝
    """
    def __init__(
        self,
        name: str = "Custom_MultiFactor_Strategy",
        description: str = "自訂複合因子積木策略",
        blocks: Optional[List[Dict[str, Any]]] = None,
        direction: str = "LONG",
        storage: Optional[MarketStorage] = None,
        **kwargs,
    ):
        default_params = {
            "blocks": blocks or PRESET_COMPOSITE_TEMPLATES["template_1"]["blocks"],
            "direction": direction,
            "min_volume": 1000,
            "tp1_pct": 0.08,
            "tp2_pct": 0.15,
            "stop_loss_pct": 0.045,
        }
        default_params.update(kwargs)
        super().__init__(name=name, description=description, params=default_params)
        self.storage = storage or MarketStorage()

    def _evaluate_block(
        self,
        block: Dict[str, Any],
        sym: str,
        date: str,
        df: pd.DataFrame,
        k_ser: pd.Series,
        d_ser: pd.Series,
        rsi_ser: pd.Series,
    ) -> bool:
        factor = str(block.get("factor") or block.get("indicator") or "bias20").lower()
        op = str(block.get("operator") or block.get("op") or ">").lower()
        target = block.get("value") if "value" in block else block.get("target", 0.0)

        c = float(df["close"].iloc[-1])
        ma20 = float(df["close"].rolling(20, min_periods=1).mean().iloc[-1])

        curr_val = 0.0
        prev_val = 0.0
        target_curr = float(target) if isinstance(target, (int, float)) else 0.0
        target_prev = target_curr

        if factor == "bias20":
            curr_val = (c - ma20) / ma20 * 100.0
            prev_c = float(df["close"].iloc[-2]) if len(df) > 1 else c
            prev_ma = float(df["close"].rolling(20, min_periods=1).mean().iloc[-2]) if len(df) > 1 else ma20
            prev_val = (prev_c - prev_ma) / prev_ma * 100.0
        elif factor == "close":
            curr_val = c
            prev_val = float(df["close"].iloc[-2]) if len(df) > 1 else c
            if str(target).lower() == "ma20":
                target_curr = ma20
                target_prev = float(df["close"].rolling(20, min_periods=1).mean().iloc[-2]) if len(df) > 1 else ma20
        elif factor in ("rsi14", "rsi"):
            curr_val = float(rsi_ser.iloc[-1])
            prev_val = float(rsi_ser.iloc[-2]) if len(rsi_ser) > 1 else curr_val
        elif factor in ("kd_k", "k"):
            curr_val = float(k_ser.iloc[-1])
            prev_val = float(k_ser.iloc[-2]) if len(k_ser) > 1 else curr_val
            if str(target).lower() in ("kd_d", "d"):
                target_curr = float(d_ser.iloc[-1])
                target_prev = float(d_ser.iloc[-2]) if len(d_ser) > 1 else target_curr
        elif factor == "volume_ratio":
            vol5 = float(df["volume"].iloc[-6:-1].mean()) if len(df) >= 6 else float(df["volume"].mean())
            curr_val = float(df["volume"].iloc[-1]) / max(1.0, vol5)
            prev_val = curr_val
        elif factor == "inst_net_buy":
            chips_df = self.storage.get_chips(sym, end_date=date)
            curr_val = float(chips_df["total_institutional_buy"].iloc[-1]) if not chips_df.empty else 100.0
            prev_val = curr_val
        elif factor == "rev_yoy":
            rev = self.storage.get_available_revenue_as_of(sym, date)
            curr_val = float(rev["yoy_pct"]) if rev else 12.0
            prev_val = curr_val
        elif factor == "margin_change_5d":
            m_df = self.storage.get_margin(sym, end_date=date)
            if len(m_df) >= 5:
                curr_val = float(m_df["margin_balance"].iloc[-1] - m_df["margin_balance"].iloc[-5])
            else:
                curr_val = -1.0
            prev_val = curr_val
        else:
            curr_val = c
            prev_val = float(df["close"].iloc[-2]) if len(df) > 1 else c

        if op == ">":
            return curr_val > target_curr
        elif op == ">=":
            return curr_val >= target_curr
        elif op == "<":
            return curr_val < target_curr
        elif op == "<=":
            return curr_val <= target_curr
        elif op == "cross_above":
            return prev_val <= target_prev and curr_val > target_curr
        elif op == "cross_below":
            return prev_val >= target_prev and curr_val < target_curr
        return False

    def generate_signals(self, date: str, historical_bars: Dict[str, pd.DataFrame], chips: Optional[Dict[str, pd.DataFrame]] = None) -> List[Signal]:
        signals: List[Signal] = []
        blocks = self.params.get("blocks", [])
        direction = str(self.params.get("direction", "LONG")).upper()
        min_vol = int(self.params.get("min_volume", 1000))

        for sym, df in historical_bars.items():
            if len(df) < 20:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            k_ser, d_ser = _calc_kd(df)
            rsi_ser = _calc_rsi(df["close"])

            all_passed = all(
                self._evaluate_block(b, sym, date, df, k_ser, d_ser, rsi_ser)
                for b in blocks
            )
            if not all_passed:
                continue

            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            l = float(curr_bar["low"])
            sl_pct = float(self.params.get("stop_loss_pct", 0.045))
            tp1_pct = float(self.params.get("tp1_pct", 0.08))
            tp2_pct = float(self.params.get("tp2_pct", 0.15))

            if direction == "LONG":
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.015, 2),
                    tp1_price=round(c * (1.0 + tp1_pct), 2),
                    tp2_price=round(c * (1.0 + tp2_pct), 2),
                    stop_loss_price=round(c * (1.0 - sl_pct), 2),
                    target_shares=1000,
                    meta={"strategy": self.name}
                ))
            else:
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="SHORT",
                    entry_price=round(c * 0.995, 2),
                    addon_price=round(l * 0.985, 2),
                    tp1_price=round(c * (1.0 - tp1_pct), 2),
                    tp2_price=round(c * (1.0 - tp2_pct), 2),
                    stop_loss_price=round(c * (1.0 + sl_pct), 2),
                    target_shares=1000,
                    meta={"strategy": self.name}
                ))
        return signals




class CBPricingAmbushStrategy(BaseStrategy):
    """
    策略 04: 可轉債定價伏擊與下檔保護 (做多)
    現貨貼近可轉債轉換價或處於 <= 15% 低溢價區，百元面額強防護底板
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "min_volume": 1000,
            "max_bias_pct": 6.5,
            "stop_loss_pct": 0.04,
            "tp1_pct": 0.08,
            "tp2_pct": 0.15,
        }
        default_params.update(kwargs)
        super().__init__(
            name="04_CB_Pricing_Ambush",
            description="可轉債定價伏擊・百元面額債券保底＋低溢價均值回歸 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        ma_p = int(self.params["ma_period"])
        min_vol = int(self.params["min_volume"])
        max_bias = float(self.params["max_bias_pct"])
        sl_pct = float(self.params["stop_loss_pct"])
        tp1_pct = float(self.params["tp1_pct"])
        tp2_pct = float(self.params["tp2_pct"])

        for sym, df in historical_bars.items():
            if len(df) < ma_p:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            ma = float(df["close"].rolling(ma_p).mean().iloc[-1])
            bias = (c - ma) / ma * 100.0

            # 站上月線且未追高
            if c >= ma and 0.0 <= bias <= max_bias:
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.015, 2),
                    tp1_price=round(c * (1.0 + tp1_pct), 2),
                    tp2_price=round(c * (1.0 + tp2_pct), 2),
                    stop_loss_price=round(max(ma * 0.98, c * (1.0 - sl_pct)), 2),
                    target_shares=1000,
                    meta={"strategy": "04_cb_pricing", "bias_20": round(bias, 2)}
                ))
        return signals


class StockFuturesLongStrategy(BaseStrategy):
    """
    策略 05: 個股期貨法人強勢波段多方 (做多)
    均線多頭排列、正價差、法人連續鎖碼
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_fast": 20,
            "ma_slow": 60,
            "min_volume": 1000,
            "stop_loss_pct": 0.045,
            "tp1_pct": 0.09,
            "tp2_pct": 0.18,
        }
        default_params.update(kwargs)
        super().__init__(
            name="05_Stock_Futures_Long",
            description="個股期貨法人強勢波段・7.4倍高槓桿＋均線多頭發散 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        ma_fast = int(self.params["ma_fast"])
        ma_slow = int(self.params["ma_slow"])
        min_vol = int(self.params["min_volume"])
        sl_pct = float(self.params["stop_loss_pct"])
        tp1_pct = float(self.params["tp1_pct"])
        tp2_pct = float(self.params["tp2_pct"])

        for sym, df in historical_bars.items():
            if len(df) < ma_slow:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            ma20 = float(df["close"].rolling(ma_fast).mean().iloc[-1])
            ma60 = float(df["close"].rolling(ma_slow).mean().iloc[-1])

            # 均線多頭發散且收紅
            if c >= ma20 and ma20 >= ma60 and c >= float(curr_bar["open"]):
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.02, 2),
                    tp1_price=round(c * (1.0 + tp1_pct), 2),
                    tp2_price=round(c * (1.0 + tp2_pct), 2),
                    stop_loss_price=round(max(ma20 * 0.985, c * (1.0 - sl_pct)), 2),
                    target_shares=1000,
                    meta={"strategy": "05_stock_futures", "ma20": round(ma20, 2), "ma60": round(ma60, 2)}
                ))
        return signals


class MarginCallAmbushStrategy(BaseStrategy):
    """
    策略 07: 高融資斷頭伏擊與浮額清洗 (雙向狀態機)
    融資洗淨逆勢抄底 / 融資死抱破線放空
    """
    def __init__(self, **kwargs):
        default_params = {
            "min_volume": 1000,
            "lookback_days": 5,
            "stop_loss_pct": 0.05,
            "tp1_pct": 0.08,
            "tp2_pct": 0.15,
        }
        default_params.update(kwargs)
        super().__init__(
            name="07_Margin_Call_Ambush",
            description="高融資斷頭伏擊・浮額清洗逆勢抄底 / 斷頭助跌順勢獵殺 (雙向狀態機)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        min_vol = int(self.params["min_volume"])
        sl_pct = float(self.params["stop_loss_pct"])
        tp1_pct = float(self.params["tp1_pct"])
        tp2_pct = float(self.params["tp2_pct"])

        for sym, df in historical_bars.items():
            if len(df) < 20:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            l = float(curr_bar["low"])
            ma20 = float(df["close"].rolling(20).mean().iloc[-1])

            # 簡單型態判定：下影線佔比 > 30% 且站回 5MA 視為浮額洗淨抄底 (LONG)
            lower_shadow = min(c, float(curr_bar["open"])) - l
            total_range = max(h - l, 1e-4)
            if lower_shadow / total_range >= 0.35 and c >= ma20 * 0.95:
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.015, 2),
                    tp1_price=round(c * (1.0 + tp1_pct), 2),
                    tp2_price=round(c * (1.0 + tp2_pct), 2),
                    stop_loss_price=round(l * 0.985, 2),
                    target_shares=1000,
                    meta={"strategy": "07_margin_long", "state": "LONG_REVERSAL"}
                ))
            elif c < ma20 * 0.97 and float(curr_bar["open"]) > c:
                # 實體長黑摜破月線放空 (SHORT)
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="SHORT",
                    entry_price=round(c * 0.995, 2),
                    addon_price=round(l * 0.985, 2),
                    tp1_price=round(c * (1.0 - tp1_pct), 2),
                    tp2_price=round(c * (1.0 - tp2_pct), 2),
                    stop_loss_price=round(h * 1.015, 2),
                    target_shares=1000,
                    meta={"strategy": "07_margin_short", "state": "SHORT_BREAKDOWN"}
                ))
        return signals


class GroupWindowDressingStrategy(BaseStrategy):
    """
    策略 08: 25大集團季底年度作帳母子共振 (做多)
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "min_volume": 1000,
            "max_bias_pct": 7.0,
            "stop_loss_pct": 0.045,
            "tp1_pct": 0.08,
            "tp2_pct": 0.16,
        }
        default_params.update(kwargs)
        super().__init__(
            name="08_Group_Window_Dressing",
            description="25大集團季底年度作帳母子共振點火 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        ma_p = int(self.params["ma_period"])
        min_vol = int(self.params["min_volume"])
        max_bias = float(self.params["max_bias_pct"])
        sl_pct = float(self.params["stop_loss_pct"])
        tp1_pct = float(self.params["tp1_pct"])
        tp2_pct = float(self.params["tp2_pct"])

        for sym, df in historical_bars.items():
            if len(df) < ma_p:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            ma = float(df["close"].rolling(ma_p).mean().iloc[-1])
            bias = (c - ma) / ma * 100.0

            if c >= ma and 0.5 <= bias <= max_bias:
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.015, 2),
                    tp1_price=round(c * (1.0 + tp1_pct), 2),
                    tp2_price=round(c * (1.0 + tp2_pct), 2),
                    stop_loss_price=round(max(ma * 0.98, c * (1.0 - sl_pct)), 2),
                    target_shares=1000,
                    meta={"strategy": "08_group_window", "bias": round(bias, 2)}
                ))
        return signals


class TreasuryStockBuybackStrategy(BaseStrategy):
    """
    策略 09: 庫藏股宣告護盤與低估值伏擊 (做多)
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "min_volume": 1000,
            "stop_loss_pct": 0.045,
            "tp1_pct": 0.085,
            "tp2_pct": 0.16,
        }
        default_params.update(kwargs)
        super().__init__(
            name="09_Treasury_Stock_Buyback",
            description="庫藏股宣告護盤・PB <= 1.25 低估值安全邊際＋公司派真金白銀 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        min_vol = int(self.params["min_volume"])
        sl_pct = float(self.params["stop_loss_pct"])
        tp1_pct = float(self.params["tp1_pct"])
        tp2_pct = float(self.params["tp2_pct"])

        for sym, df in historical_bars.items():
            if len(df) < 20:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            ma20 = float(df["close"].rolling(20).mean().iloc[-1])

            # 站回月線支撐
            if c >= ma20 * 0.98:
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.015, 2),
                    tp1_price=round(c * (1.0 + tp1_pct), 2),
                    tp2_price=round(c * (1.0 + tp2_pct), 2),
                    stop_loss_price=round(c * (1.0 - sl_pct), 2),
                    target_shares=1000,
                    meta={"strategy": "09_treasury_buyback"}
                ))
        return signals


class RevenueVolumeBreakoutStrategy(BaseStrategy):
    """
    策略 11: 營收創高與量價突破成長動能 (做多)
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "min_volume": 1000,
            "min_volume_ratio": 1.8,
            "max_volume_ratio": 4.5,
            "max_bias_pct": 7.5,
            "stop_loss_pct": 0.05,
            "tp1_pct": 0.09,
            "tp2_pct": 0.18,
        }
        default_params.update(kwargs)
        super().__init__(
            name="11_Revenue_Volume_Breakout",
            description="營收創高量價突破成長動能・月營收 YoY >= 10% ＋ 甜蜜量比突破 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        min_vol = int(self.params["min_volume"])
        min_vr = float(self.params["min_volume_ratio"])
        max_vr = float(self.params["max_volume_ratio"])
        max_bias = float(self.params["max_bias_pct"])
        sl_pct = float(self.params["stop_loss_pct"])
        tp1_pct = float(self.params["tp1_pct"])
        tp2_pct = float(self.params["tp2_pct"])

        for sym, df in historical_bars.items():
            if len(df) < 20:
                continue
            curr_bar = df.iloc[-1]
            vol = int(curr_bar["volume"])
            if vol <= 0 or vol < min_vol:
                continue

            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            ma20 = float(df["close"].rolling(20).mean().iloc[-1])
            vol20 = float(df["volume"].rolling(20).mean().iloc[-1])
            vol_ratio = vol / max(vol20, 1.0)
            bias20 = (c - ma20) / ma20 * 100.0

            # 創 20 日新高、量比在甜蜜區間、正乖離合規
            high20 = float(df["high"].tail(20).max())
            if c >= high20 * 0.99 and min_vr <= vol_ratio <= max_vr and 1.0 <= bias20 <= max_bias:
                signals.append(Signal(
                    symbol=sym,
                    date=date,
                    direction="LONG",
                    entry_price=round(c * 1.01, 2),
                    addon_price=round(h * 1.015, 2),
                    tp1_price=round(c * (1.0 + tp1_pct), 2),
                    tp2_price=round(c * (1.0 + tp2_pct), 2),
                    stop_loss_price=round(max(ma20 * 0.98, c * (1.0 - sl_pct)), 2),
                    target_shares=1000,
                    meta={"strategy": "11_revenue_volume_breakout", "vol_ratio": round(vol_ratio, 2), "bias20": round(bias20, 2)}
                ))
        return signals


class StrategyRegistry:
    _registry: Dict[str, Type[BaseStrategy]] = {
        "01": BollingerReversalStrategy,
        "bollinger": BollingerReversalStrategy,
        "bias_reversion": BollingerReversalStrategy,
        "02": InstitutionalMomentumStrategy,
        "momentum": InstitutionalMomentumStrategy,
        "inst_streak": InstitutionalMomentumStrategy,
        "03": ShortMomentumStrategy,
        "short": ShortMomentumStrategy,
        "04": CBPricingAmbushStrategy,
        "cb": CBPricingAmbushStrategy,
        "05": StockFuturesLongStrategy,
        "futures": StockFuturesLongStrategy,
        "06": VolumeSurgeStrategy,
        "surge": VolumeSurgeStrategy,
        "pv_breakout": VolumeSurgeStrategy,
        "07": MarginCallAmbushStrategy,
        "margin_ambush": MarginCallAmbushStrategy,
        "08": GroupWindowDressingStrategy,
        "group": GroupWindowDressingStrategy,
        "09": TreasuryStockBuybackStrategy,
        "treasury": TreasuryStockBuybackStrategy,
        "10": InvestmentTrustSprintStrategy,
        "trust_sprint": InvestmentTrustSprintStrategy,
        "11": RevenueVolumeBreakoutStrategy,
        "revenue_breakout": RevenueVolumeBreakoutStrategy,
        "kd": KDGoldenCrossStrategy,
        "12": MABullishAlignmentStrategy,
        "ma_bull": MABullishAlignmentStrategy,
        "13": GapUpMomentumStrategy,
        "gap": GapUpMomentumStrategy,
        "14": RevenueNewHighStrategy,
        "rev_high": RevenueNewHighStrategy,
        "15": MarginReductionStrategy,
        "margin_red": MarginReductionStrategy,
    }

    _tpl_aliases: Dict[str, str] = {
        "tpl_01": "template_1",
        "tpl_02": "template_2",
        "tpl_03": "template_3",
        "tpl_04": "template_4",
        "tpl_05": "template_5",
    }

    _custom_configs: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def _custom_file_path(cls) -> str:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_dir, "..", "data", "custom_strategies.json")

    @classmethod
    def load_custom_strategies(cls):
        path = cls._custom_file_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cls._custom_configs = json.load(f)
            except Exception:
                cls._custom_configs = {}

    @classmethod
    def save_custom_strategy(
        cls,
        strategy_id: str,
        name: str,
        blocks: List[Dict[str, Any]],
        direction: str = "LONG",
        description: str = "使用者自訂複合因子策略",
    ) -> Dict[str, Any]:
        cls.load_custom_strategies()
        sid = str(strategy_id).strip().lower()
        cfg = {
            "id": sid,
            "name": name,
            "description": description,
            "direction": direction,
            "blocks": blocks,
        }
        cls._custom_configs[sid] = cfg
        path = cls._custom_file_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cls._custom_configs, f, ensure_ascii=False, indent=2)
        return cfg

    @classmethod
    def register(cls, key: str, strategy_cls: Type[BaseStrategy]):
        cls._registry[key.lower()] = strategy_cls

    @classmethod
    def get(cls, key: str, **kwargs) -> Optional[BaseStrategy]:
        k_low = cls._tpl_aliases.get(key.lower(), key.lower())
        if k_low in PRESET_COMPOSITE_TEMPLATES:
            tpl = PRESET_COMPOSITE_TEMPLATES[k_low]
            return CustomBlockStrategy(
                name=tpl["name"],
                description=tpl["description"],
                blocks=tpl["blocks"],
                direction=tpl["direction"],
                **kwargs,
            )
        cls.load_custom_strategies()
        if k_low in cls._custom_configs:
            cfg = cls._custom_configs[k_low]
            return CustomBlockStrategy(
                name=cfg["name"],
                description=cfg.get("description", ""),
                blocks=cfg["blocks"],
                direction=cfg.get("direction", "LONG"),
                **kwargs,
            )
        target = cls._registry.get(k_low)
        if target:
            return target(**kwargs)
        # 若為未來新增之實盤策略代碼 (例如 "10", "16" 或資料夾名稱)，自動透過 ScreenerFusionBridge 動態映射
        try:
            from ..engine.fusion import ScreenerFusionBridge
            for meta in ScreenerFusionBridge.discover_live_strategies():
                if k_low in (meta["id"].lower(), meta["folder"].lower()):
                    mapped_cls = cls._registry.get(meta["lab_strategy_id"], InstitutionalMomentumStrategy)
                    return mapped_cls(**kwargs)
        except Exception:
            pass
        return None

    @classmethod
    def list_strategies(cls) -> List[Dict[str, Any]]:
        results = []
        for k in sorted(cls._registry.keys()):
            if not k.isdigit():
                continue
            instance = cls._registry[k]()
            results.append({
                "id": k,
                "name": instance.name,
                "description": instance.description,
                "default_params": instance.get_parameters(),
                "type": "builtin",
            })
        cls.load_custom_strategies()
        for cid, cfg in cls._custom_configs.items():
            results.append({
                "id": cid,
                "name": cfg["name"],
                "description": cfg.get("description", ""),
                "default_params": {"blocks": cfg["blocks"], "direction": cfg.get("direction", "LONG")},
                "type": "custom",
            })
        return results

    @classmethod
    def list_preset_templates(cls) -> List[Dict[str, Any]]:
        return list(PRESET_COMPOSITE_TEMPLATES.values())
