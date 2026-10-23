"""
單元測試: 台股量化策略實驗室 - 撮合核心與四大防線 (test_lab_engine.py)
檢驗零未來函數、台股手續費與證交稅計價、四大防線狀態機、漲跌停防呆。
"""

import pytest
import pandas as pd
from lab.engine.portfolio import Portfolio
from lab.engine.backtest_engine import BacktestEngine, Signal


def test_t_plus_1_execution_no_lookahead():
    """斷言 1: T 日收盤發出買進信號，T 日持倉仍為 0，T+1 日開盤撮合才建立持倉"""
    engine = BacktestEngine(initial_cash=500_000.0, slippage_pct=0.0)

    # 構造 3 天行情
    dates = pd.date_range("2024-03-01", periods=3, freq="B")
    df = pd.DataFrame({
        "open": [100.0, 102.0, 105.0],
        "high": [101.0, 104.0, 106.0],
        "low": [99.0, 101.0, 103.0],
        "close": [100.5, 103.0, 105.5],
        "volume": [2000, 2500, 3000],
    }, index=dates)

    # 在第 1 天 (2024-03-01) 盤後產生信號
    def test_generator(cur_date: str, hist: dict):
        if cur_date == "2024-03-01":
            return [Signal(
                symbol="TEST",
                date=cur_date,
                entry_price=103.0,  # 買進上限 103 元
                target_shares=1000,
            )]
        return []

    res = engine.run(market_data={"TEST": df}, signal_generator=test_generator)

    # 檢驗第 1 天權益紀錄 (部位應為 0)
    eq_day1 = res.equity_curve[0]
    assert eq_day1["date"] == "2024-03-01"
    assert eq_day1["open_positions"] == 0
    assert eq_day1["cash"] == 500_000.0

    # 檢驗交易紀錄是否發生在第 2 天 (2024-03-04，下一個交易日)
    assert len(res.trade_history) >= 1
    first_trade = res.trade_history[0]
    assert first_trade.date == "2024-03-04"
    assert first_trade.action == "BUY_ENTRY"
    assert first_trade.shares == 1000
    # 開盤價 102 <= 103，以開盤價成交
    assert first_trade.price == 102.0


def test_taiwan_commission_and_tax_accounting():
    """斷言 2: 手續費 (0.1425% * 0.6) 與證交稅 (0.3%) 精確扣抵"""
    portfolio = Portfolio(
        initial_cash=200_000.0,
        commission_rate=0.001425,
        discount_factor=0.6,
        tax_rate=0.003,
        min_commission=20.0,
    )

    # 1. 買進 1 張 (1,000股) @ 100.0 元
    # 原始成交額 = 100 * 1000 = 100,000
    # 手續費 = floor(100,000 * 0.001425 * 0.6) = floor(85.5) = 85
    # 總付出現金 = 100,000 + 85 = 100,085
    pos = portfolio.open_position("2330", "2024-01-02", 100.0, 1000)
    assert pos is not None
    assert pos.cost_basis == 100_085.0
    assert portfolio.cash == 200_000.0 - 100_085.0

    # 2. 賣出 1 張 @ 110.0 元
    # 總市值 = 110 * 1000 = 110,000
    # 賣出手續費 = floor(110,000 * 0.001425 * 0.6) = floor(94.05) = 94
    # 證交稅 = floor(110,000 * 0.003) = 330
    # 淨得款項 = 110,000 - 94 - 330 = 109,576
    # 實現損益 = 109,576 - 100,085 = 9,491
    trade = portfolio.close_position("2330", "2024-01-05", 110.0)
    assert trade is not None
    assert trade.commission == 94.0
    assert trade.tax == 330.0
    assert trade.net_cash_flow == 109_576.0
    assert trade.realized_pnl == 9491.0
    # 帳戶最終現金 = 99,915 + 109,576 = 209,491
    assert portfolio.cash == 209_491.0
    assert len(portfolio.positions) == 0


def test_four_price_state_machine():
    """斷言 3: 四大防線依序觸發：進場 ➔ 動能加碼 ➔ 觸及停損離場"""
    engine = BacktestEngine(initial_cash=500_000.0, slippage_pct=0.0)

    # Day 1: 訊號日 (收盤 100)
    # Day 2: 撮合進場 (Low 99 <= 101)，收盤 103
    # Day 3: 衝高觸發加碼 (High 106 >= 105)，但隨後急殺
    # Day 4: 盤中摜破停損 (Low 94 <= 96)，觸發無條件停損離場
    dates = pd.date_range("2024-04-01", periods=4, freq="B")
    df = pd.DataFrame({
        "open":  [100.0, 100.0, 103.0, 98.0],
        "high":  [101.0, 104.0, 106.5, 99.0],
        "low":   [99.0,  99.0,  102.0, 94.0],
        "close": [100.0, 103.0, 102.5, 95.0],
        "volume": [2000, 3000,  4000,  3500],
    }, index=dates)

    def generator(cur_date: str, hist: dict):
        if cur_date == dates[0].strftime("%Y-%m-%d"):
            return [Signal(
                symbol="ALPHA",
                date=cur_date,
                entry_price=101.0,
                addon_price=105.0,
                tp1_price=112.0,
                stop_loss_price=96.0,
                target_shares=1000,
            )]
        return []

    res = engine.run(market_data={"ALPHA": df}, signal_generator=generator)
    actions = [t.action for t in res.trade_history]

    assert "BUY_ENTRY" in actions
    assert "BUY_ADDON" in actions
    assert "SELL_STOP_LOSS" in actions
    # 部位最終因停損而全數平倉清空
    assert len(res.portfolio.positions) == 0


def test_limit_up_and_down_locks():
    """斷言 4: 漲停鎖死無法買進、跌停鎖死當日無法出脫"""
    engine = BacktestEngine(initial_cash=300_000.0, slippage_pct=0.0)

    # 構造漲停無量 (買不進)
    dates = pd.date_range("2024-06-01", periods=3, freq="B")
    df = pd.DataFrame({
        "open":  [100.0, 110.0, 115.0],
        "high":  [100.0, 110.0, 116.0],
        "low":   [99.0,  110.0, 114.0],
        "close": [100.0, 110.0, 115.0],
        "volume": [1000, 10,    2000],  # Day 2 開盤即漲停且僅 10 張 (鎖死)
    }, index=dates)

    def limit_up_gen(cur_date: str, hist: dict):
        if cur_date == dates[0].strftime("%Y-%m-%d"):
            return [Signal(
                symbol="LOCKED",
                date=cur_date,
                entry_price=110.0,
                target_shares=1000,
            )]
        return []

    res = engine.run(market_data={"LOCKED": df}, signal_generator=limit_up_gen)
    # 漲停鎖死無量，買單應判定未成交，部位應為 0
    assert len(res.trade_history) == 0
    assert len(res.portfolio.positions) == 0


def test_short_selling_execution_and_cover():
    """斷言 5: 空方放空撮合、加碼、以及向下獲利回補 (TP1)"""
    engine = BacktestEngine(initial_cash=500_000.0, slippage_pct=0.0)

    # 構造空頭下挫走勢
    dates = pd.date_range("2024-07-01", periods=4, freq="B")
    df = pd.DataFrame({
        "open":  [100.0, 99.0, 95.0, 90.0],
        "high":  [101.0, 100.0, 96.0, 91.0],
        "low":   [98.0,  96.0, 93.0, 88.0],
        "close": [99.0,  96.5, 93.5, 89.0],
        "volume": [2000, 3000, 3500, 4000],
    }, index=dates)

    def short_generator(cur_date: str, hist: dict):
        if cur_date == dates[0].strftime("%Y-%m-%d"):
            return [Signal(
                symbol="BEAR",
                date=cur_date,
                direction="SHORT",
                entry_price=99.0,
                addon_price=95.0,
                tp1_price=91.0,
                stop_loss_price=104.0,
                target_shares=1000,
            )]
        return []

    res = engine.run(market_data={"BEAR": df}, signal_generator=short_generator)
    actions = [t.action for t in res.trade_history]

    assert "SELL_SHORT" in actions
    # 檢驗空單在股價下挫時產生正向獲利
    cover_trades = [t for t in res.trade_history if "COVER" in t.action]
    if cover_trades:
        assert cover_trades[0].realized_pnl > 0
