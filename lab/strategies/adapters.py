"""
Strategy Adapters: 實盤策略回測適配層 (全矩陣升級版)
將現有 01~09 策略與自訂演算法包裝為 BaseStrategy，支援多空雙向回測與參數優化。
"""

from typing import Dict, List, Optional, Any, Type
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy
from ..engine.backtest_engine import Signal


class BollingerReversalStrategy(BaseStrategy):
    """
    策略 01 適配器: 布林通道下軌超跌反彈 + 止跌確認 (多方)
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "std_dev_multiplier": 2.0,
            "min_volume": 1000,
            "lower_dist_max": 3.0,   # 現價距下軌 3% 內
            "lower_dist_min": -5.0,  # 跌破下軌不超過 5%
            "stop_loss_pct": 0.04,   # 4% 停損防守
        }
        default_params.update(kwargs)
        super().__init__(
            name="01_Bollinger_Reversal",
            description="布林通道下軌超跌反彈＋止跌K棒確認 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        ma_period = self.params["ma_period"]
        mult = self.params["std_dev_multiplier"]
        min_vol = self.params["min_volume"]

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

            if vol < min_vol:
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
    策略 02 適配器: 法人起漲動能 + 月線乖離防追高 (多方)
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "min_volume": 1000,
            "max_bias_pct": 8.0,      # 月線正乖離不超過 8% 防追高
            "min_inst_buy_lots": 300, # 三大法人合計買超 >= 300 張
            "stop_loss_pct": 0.05,
        }
        default_params.update(kwargs)
        super().__init__(
            name="02_Institutional_Momentum",
            description="三大法人集中買超＋均線向上起漲動能 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        ma_period = self.params["ma_period"]
        min_vol = self.params["min_volume"]
        max_bias = self.params["max_bias_pct"]

        for sym, df in historical_bars.items():
            if len(df) < ma_period + 5:
                continue

            curr_bar = df.iloc[-1]
            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            l = float(curr_bar["low"])
            vol = int(curr_bar["volume"])

            if vol < min_vol:
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


class ShortMomentumStrategy(BaseStrategy):
    """
    策略 03 適配器: 弱勢破線做空避險 (空方)
    """
    def __init__(self, **kwargs):
        default_params = {
            "ma_period": 20,
            "min_volume": 1000,
            "max_bias_pct": -1.5,     # 跌破月線 -1.5% 以下弱勢空頭
            "stop_loss_pct": 0.04,    # 上漲反彈 4% 停損回補
            "tp1_pct": 0.07,          # 向下獲利 7% 分批停利
            "tp2_pct": 0.14,          # 向下獲利 14% 清倉獲利
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
        ma_period = self.params["ma_period"]
        min_vol = self.params["min_volume"]
        max_bias = self.params["max_bias_pct"]

        for sym, df in historical_bars.items():
            if len(df) < ma_period + 5:
                continue

            curr_bar = df.iloc[-1]
            c = float(curr_bar["close"])
            h = float(curr_bar["high"])
            l = float(curr_bar["low"])
            vol = int(curr_bar["volume"])

            if vol < min_vol:
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
    策略 06 適配器: 爆量長紅動能突破 (多方)
    """
    def __init__(self, **kwargs):
        default_params = {
            "volume_surge_mult": 1.8, # 成交量為 5 日均量 1.8 倍以上
            "min_volume": 1200,
            "min_return_pct": 3.0,    # 當日漲幅 >= 3.0%
            "stop_loss_pct": 0.04,
        }
        default_params.update(kwargs)
        super().__init__(
            name="06_Volume_Surge",
            description="量能倍增突破＋實體長紅多方發動 (做多)",
            params=default_params,
        )

    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        signals: List[Signal] = []
        min_vol = self.params["min_volume"]
        surge_mult = self.params["volume_surge_mult"]
        min_ret = self.params["min_return_pct"]

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

            if vol < min_vol:
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


class StrategyRegistry:
    _registry: Dict[str, Type[BaseStrategy]] = {
        "01": BollingerReversalStrategy,
        "bollinger": BollingerReversalStrategy,
        "02": InstitutionalMomentumStrategy,
        "momentum": InstitutionalMomentumStrategy,
        "03": ShortMomentumStrategy,
        "short": ShortMomentumStrategy,
        "06": VolumeSurgeStrategy,
        "surge": VolumeSurgeStrategy,
    }

    @classmethod
    def register(cls, key: str, strategy_cls: Type[BaseStrategy]):
        cls._registry[key.lower()] = strategy_cls

    @classmethod
    def get(cls, key: str, **kwargs) -> Optional[BaseStrategy]:
        target = cls._registry.get(key.lower())
        if target:
            return target(**kwargs)
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
            })
        return results
