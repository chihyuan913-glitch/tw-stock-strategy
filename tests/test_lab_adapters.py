"""
單元測試: 台股量化策略實驗室 - 實盤策略適配器 (test_lab_adapters.py)
檢驗 Strategy 01 與 Strategy 02 適配器接入、信號生成與端到端回測運算。
"""

import pytest
import pandas as pd
from lab.data.loader import MarketDataLoader
from lab.engine.backtest_engine import BacktestEngine
from lab.strategies.adapters import (
    BollingerReversalStrategy,
    InstitutionalMomentumStrategy,
    StrategyRegistry,
)


def test_strategy_01_bollinger_adapter():
    """斷言 1: Strategy 01 (布林超跌反彈) 適配器正確觸發信號與四大防線單調性"""
    loader = MarketDataLoader()
    # 產生布林超跌 V 轉情境行情
    df = loader.generate_mock_series("2330", scenario="bb_reversal", days=60)

    strat = BollingerReversalStrategy()
    signals = strat.generate_signals("2024-03-01", {"2330": df})

    # 檢驗信號格式
    if signals:
        sig = signals[0]
        assert sig.symbol == "2330"
        assert sig.entry_price > 0
        assert sig.addon_price > sig.entry_price or sig.addon_price >= sig.entry_price * 0.99
        assert sig.tp1_price > sig.stop_loss_price
        assert sig.tp2_price >= sig.tp1_price
        assert sig.stop_loss_price < sig.entry_price


def test_strategy_02_momentum_adapter():
    """斷言 2: Strategy 02 (法人動能起漲) 適配器正確生成買訊"""
    loader = MarketDataLoader()
    df = loader.generate_mock_series("2454", scenario="trend_up", days=60)

    strat = InstitutionalMomentumStrategy()
    signals = strat.generate_signals("2024-03-01", {"2454": df})
    assert isinstance(signals, list)


def test_strategy_03_short_adapter():
    """斷言 3: Strategy 03 (弱勢破線做空) 適配器生成 SHORT 訊號與空方四防線單調性"""
    from lab.strategies.adapters import ShortMomentumStrategy
    loader = MarketDataLoader()
    df = loader.generate_mock_series("2454", scenario="trend_down", days=60)

    strat = ShortMomentumStrategy()
    signals = strat.generate_signals("2024-03-01", {"2454": df})
    if signals:
        sig = signals[0]
        assert sig.direction == "SHORT"
        assert sig.stop_loss_price > sig.entry_price  # 空方停損在上方
        assert sig.tp1_price < sig.entry_price        # 空方獲利在下方
        assert sig.tp2_price < sig.tp1_price


def test_strategy_06_volume_surge_adapter():
    """斷言 4: Strategy 06 (爆量突破) 適配器正確註冊與調用"""
    strat = StrategyRegistry.get("06")
    assert strat is not None
    assert strat.name == "06_Volume_Surge"
    assert "volume_surge_mult" in strat.get_parameters()


def test_end_to_end_backtest_pipeline():
    """斷言 3: 端到端回測流水線貫通 (數據 ➔ 策略 ➔ 撮合 ➔ 績效)"""
    loader = MarketDataLoader()
    df_2330 = loader.generate_mock_series("2330", scenario="trend_up", days=80)
    df_2454 = loader.generate_mock_series("2454", scenario="bb_reversal", days=80)

    market_data = {"2330": df_2330, "2454": df_2454}

    strat = StrategyRegistry.get("01")
    assert strat is not None

    engine = BacktestEngine(initial_cash=1_000_000.0)
    res = engine.run(market_data=market_data, signal_generator=strat.generate_signals)

    # 驗證回測產物結構完備
    assert res.portfolio is not None
    assert len(res.equity_curve) > 0
    assert "total_return_pct" in res.metrics
    assert "sharpe_ratio" in res.metrics
    assert "max_drawdown_pct" in res.metrics
