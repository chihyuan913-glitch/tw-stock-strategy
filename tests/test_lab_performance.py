"""
單元測試: 台股量化策略實驗室 - 績效度量指標 (test_lab_performance.py)
檢驗 Sharpe Ratio、Sortino、Max Drawdown 精算與零除異常防護。
"""

import pytest
from lab.engine.performance import PerformanceEngine
from lab.engine.portfolio import TradeRecord


def test_max_drawdown_calculation():
    """斷言 1: 最大回撤 MDD 精確性 (100萬 ➔ 150萬 ➔ 120萬 ➔ 180萬，MDD 必為 20.0%)"""
    equity_curve = [
        {"date": "2024-01-01", "total_equity": 1_000_000.0},
        {"date": "2024-01-02", "total_equity": 1_500_000.0},  # 波段高點
        {"date": "2024-01-03", "total_equity": 1_200_000.0},  # 回撤 30 萬 / 150 萬 = 20%
        {"date": "2024-01-04", "total_equity": 1_800_000.0},  # 創新高
    ]
    metrics = PerformanceEngine.calculate_metrics(
        initial_cash=1_000_000.0,
        equity_curve=equity_curve,
        trade_history=[],
    )
    assert metrics["max_drawdown_pct"] == 20.0
    assert metrics["total_return_pct"] == 80.0


def test_sharpe_and_sortino_math():
    """斷言 2: 夏普值與索提諾值年化計算具備數學嚴密性"""
    # 穩定向上曲線 (每日穩定 +0.5%)
    eq_curve = []
    val = 1_000_000.0
    for i in range(30):
        val *= 1.005
        eq_curve.append({"date": f"2024-01-{i+1:02d}", "total_equity": round(val, 2)})

    metrics = PerformanceEngine.calculate_metrics(
        initial_cash=1_000_000.0,
        equity_curve=eq_curve,
        trade_history=[],
    )
    # 穩健正收益無下行回撤時，夏普值必為顯著正值
    assert metrics["sharpe_ratio"] > 5.0
    assert metrics["sortino_ratio"] >= metrics["sharpe_ratio"]


def test_edge_cases_zero_division_guard():
    """斷言 3: 極端邊界條件 (零交易、全獲利、全虧損) 安全無報錯"""
    # 1. 完全零交易
    empty_metrics = PerformanceEngine.calculate_metrics(1_000_000.0, [], [])
    assert empty_metrics["total_closed_trades"] == 0
    assert empty_metrics["win_rate"] == 0.0
    assert empty_metrics["profit_factor"] == 0.0
    assert empty_metrics["max_drawdown_pct"] == 0.0

    # 2. 全勝交易 (無虧損，虧損為 0，Profit Factor 必須防 ZeroDivisionError)
    win_trades = [
        TradeRecord(
            trade_id="T001",
            symbol="2330",
            date="2024-01-05",
            action="SELL_CLOSE",
            shares=1000,
            price=110.0,
            commission=50.0,
            tax=330.0,
            net_cash_flow=109_620.0,
            realized_pnl=9620.0,
            pnl_pct=9.6,
        )
    ]
    eq_win = [
        {"date": "2024-01-01", "total_equity": 100_000.0},
        {"date": "2024-01-05", "total_equity": 109_620.0},
    ]
    win_metrics = PerformanceEngine.calculate_metrics(100_000.0, eq_win, win_trades)
    assert win_metrics["win_rate"] == 100.0
    assert win_metrics["profit_factor"] == 999.0  # 安全上限常數

    # 3. 全敗交易 (勝場為 0)
    loss_trades = [
        TradeRecord(
            trade_id="T002",
            symbol="2454",
            date="2024-01-05",
            action="SELL_STOP_LOSS",
            shares=1000,
            price=90.0,
            commission=50.0,
            tax=270.0,
            net_cash_flow=89_680.0,
            realized_pnl=-10320.0,
            pnl_pct=-10.3,
        )
    ]
    loss_metrics = PerformanceEngine.calculate_metrics(100_000.0, eq_win, loss_trades)
    assert loss_metrics["win_rate"] == 0.0
    assert loss_metrics["profit_factor"] == 0.0


def test_calmar_and_alpha_beta_benchmark():
    """斷言 4: 卡瑪比率與大盤 Benchmark 運算 (Alpha / Beta)"""
    import pandas as pd
    import numpy as np

    dates = [f"2024-01-{i+1:02d}" for i in range(20)]
    eq_curve = []
    val = 1_000_000.0
    for d in dates:
        val *= 1.002
        eq_curve.append({"date": d, "total_equity": round(val, 2)})

    # 大盤基準指數序列 (同步上揚)
    bm_vals = [20000.0 * (1.001 ** i) for i in range(20)]
    bm_series = pd.Series(bm_vals, index=pd.to_datetime(dates))

    metrics = PerformanceEngine.calculate_metrics(
        initial_cash=1_000_000.0,
        equity_curve=eq_curve,
        trade_history=[],
        benchmark_series=bm_series,
    )

    assert "calmar_ratio" in metrics
    assert "alpha_pct" in metrics
    assert "beta" in metrics
    assert "benchmark_return_pct" in metrics
    assert metrics["beta"] > 0
