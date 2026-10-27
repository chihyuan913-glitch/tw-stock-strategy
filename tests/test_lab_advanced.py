"""
台股量化策略實驗室 - P0+P1 進階複合因子、統計驗證、動態部位與資產配置測試套件
"""

import os
import tempfile
import pandas as pd
import numpy as np
import pytest
from fastapi.testclient import TestClient

from lab.data.storage import MarketStorage
from lab.data.loader import MarketDataLoader
from lab.engine.portfolio import Portfolio, TradeRecord
from lab.engine.backtest_engine import BacktestEngine, Signal
from lab.engine.verification import VerificationEngine
from lab.engine.allocator import PortfolioAllocator, LiveSignalEngine
from lab.strategies.adapters import (
    StrategyRegistry,
    CustomBlockStrategy,
    PRESET_COMPOSITE_TEMPLATES,
)
from line_bot_server import app


@pytest.fixture
def temp_storage():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    storage = MarketStorage(db_path=db_path)
    yield storage
    for ext in ["", "-wal", "-shm"]:
        try:
            if os.path.exists(db_path + ext):
                os.remove(db_path + ext)
        except Exception:
            pass


def test_monthly_revenue_10th_anchor_no_lookahead(temp_storage: MarketStorage):
    """
    檢驗月營收「次月 10 日」發布日錨定鐵律：
    - 2024-03 月營收於 2024-04-10 發布
    - 2024-04 月營收於 2024-05-10 發布
    在 2024-05-09 查詢時，系統嚴格只能看到 2024-03 之舊資料；
    在 2024-05-10 查詢時，才可看到 2024-04 之新資料。
    """
    assert MarketStorage.compute_revenue_announce_date("2024-04") == "2024-05-10"
    assert MarketStorage.compute_revenue_announce_date("2024-12") == "2025-01-10"

    rev_df = pd.DataFrame([
        {"year_month": "2024-03", "revenue": 100_000.0, "yoy_pct": 8.5, "mom_pct": 2.0},
        {"year_month": "2024-04", "revenue": 150_000.0, "yoy_pct": 25.0, "mom_pct": 50.0},
    ])
    saved = temp_storage.save_monthly_revenues("2330", rev_df)
    assert saved == 2

    # 5月9日查詢：尚未到次月10日，必須回傳 2024-03 資料 (100,000)
    before_10th = temp_storage.get_available_revenue_as_of("2330", "2024-05-09")
    assert before_10th is not None
    assert before_10th["year_month"] == "2024-03"
    assert before_10th["revenue"] == 100_000.0

    # 5月10日查詢：已達次月10日，回傳 2024-04 最新資料 (150,000)
    on_10th = temp_storage.get_available_revenue_as_of("2330", "2024-05-10")
    assert on_10th is not None
    assert on_10th["year_month"] == "2024-04"
    assert on_10th["revenue"] == 150_000.0


def test_finmind_parser_integration(temp_storage: MarketStorage):
    """檢驗 FinMind 三大資料集 (法人買賣超、融資餘額、月營收) 解析與寫入"""
    loader = MarketDataLoader(temp_storage)

    raw_inst = [
        {"date": "2024-05-02", "name": "Foreign_Investor", "buy": 5000000, "sell": 2000000},
        {"date": "2024-05-02", "name": "Investment_Trust", "buy": 1000000, "sell": 0},
        {"date": "2024-05-02", "name": "Dealer_self", "buy": 0, "sell": 500000},
    ]
    raw_rev = [
        {"date": "2024-04-01", "revenue_year": 2024, "revenue_month": 4, "revenue": 200000, "revenue_year_on_year": 18.5},
    ]
    res_counts = loader.parse_finmind_data(
        symbol="2330",
        inst_records=raw_inst,
        revenue_records=raw_rev,
    )
    assert res_counts["chips"] == 1
    assert res_counts["revenues"] == 1

    chips = temp_storage.get_chips("2330")
    assert chips.iloc[0]["foreign_buy"] == 3000.0  # 張數
    assert chips.iloc[0]["investment_trust_buy"] == 1000.0
    assert chips.iloc[0]["total_institutional_buy"] == 3500.0

    rev_check = temp_storage.get_available_revenue_as_of("2330", "2024-05-10")
    assert rev_check["year_month"] == "2024-04"


def test_suspended_zero_volume_and_market_60ma_filter_blocking():
    """
    檢驗：
    1. 當日成交量為 0 (暫停交易狀態) 強制攔截進場訊號並計入 blocked_by_filter_count
    2. 大盤跌破均線時凍結新倉建立，並計入 blocked_by_filter_count
    """
    dates = pd.bdate_range("2024-01-01", periods=10)
    bars = []
    for i, d in enumerate(dates):
        # 第 2 天 (index=2) 成交量為 0 (暫停交易)
        vol = 0 if i == 2 else 5000
        bars.append({
            "date": d.strftime("%Y-%m-%d"),
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 100.0,
            "volume": vol,
        })
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)

    # 大盤指數：前 4 天高於均線 (100)，第 5 天起暴跌至 50 (跌破均線)
    bm_vals = [100.0, 100.0, 100.0, 100.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0]
    bm_series = pd.Series(bm_vals, index=dates)

    target_dates = {dates[2].strftime("%Y-%m-%d"), dates[5].strftime("%Y-%m-%d")}

    def selective_buy_signal(date_str, hist_map):
        if date_str in target_dates:
            return [
                Signal(
                    symbol="2330",
                    date=date_str,
                    direction="LONG",
                    entry_price=102.0,
                    addon_price=108.0,
                    tp1_price=110.0,
                    tp2_price=120.0,
                    stop_loss_price=90.0,
                    target_shares=1000,
                )
            ]
        return []

    engine = BacktestEngine(
        initial_cash=1_000_000.0,
        enable_market_filter=True,
        market_ma_period=3,
    )
    res = engine.run(
        market_data={"2330": df},
        signal_generator=selective_buy_signal,
        benchmark_series=bm_series,
    )

    assert res.blocked_by_filter_count == 2
    reasons = [b["reason"] for b in res.blocked_signals]
    assert any("SUSPENDED_ZERO_VOLUME" in r for r in reasons)
    assert any("MARKET_BELOW_MA3" in r for r in reasons)


def test_dynamic_position_sizing_and_trailing_stop():
    """檢驗 ATR 風險定額部位控管、單筆最大虧損上限與移動停利 (Trailing Stop)"""
    p = Portfolio(initial_cash=1_000_000.0)
    # 股價 100 元，停損 90 元 (每股風險 10 元)，單筆最大風險 1.5% (15,000 元) -> 應買 1,500 股
    shares_atr = p.calculate_position_size(
        sizing_mode="atr_risk",
        price=100.0,
        stop_loss_price=90.0,
        atr_risk_pct=0.015,
        max_single_loss_pct=0.02,
    )
    assert shares_atr == 1500

    # 測試移動停利 (Trailing Stop): 進場後創高 120，隨後回檔超過 5% (跌破 114) 觸發 SELL_TRAILING_STOP
    dates = pd.bdate_range("2024-03-01", periods=6)
    prices = [
        (100, 102, 99, 100),   # Day 0: 產生訊號
        (100, 103, 99, 102),   # Day 1: T+1 開盤 100 建倉
        (105, 120, 104, 118),  # Day 2: 創波段高點 120 (移動停利線推進至 120 * 0.95 = 114)
        (115, 116, 110, 111),  # Day 3: 最低價 110 跌破 114，觸發移動停利出場！
        (110, 112, 108, 109),
        (109, 110, 105, 106),
    ]
    rows = []
    for d, (o, h, l, c) in zip(dates, prices):
        rows.append({"date": d.strftime("%Y-%m-%d"), "open": o, "high": h, "low": l, "close": c, "volume": 3000})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)

    first_day = dates[0].strftime("%Y-%m-%d")
    def sig_gen(d_str, m):
        if d_str == first_day:
            return [
                Signal(
                    symbol="2330",
                    date=d_str,
                    entry_price=101.0,
                    direction="LONG",
                    addon_price=130.0,
                    tp1_price=140.0,
                    tp2_price=150.0,
                    stop_loss_price=85.0,
                    target_shares=1000,
                )
            ]
        return []

    eng = BacktestEngine(initial_cash=1_000_000.0, enable_trailing_stop=True, trailing_stop_pct=0.05)
    res = eng.run(market_data={"2330": df}, signal_generator=sig_gen)
    actions = [t.action for t in res.trade_history]
    assert "SELL_TRAILING_STOP" in actions


def test_eight_core_strategies_and_custom_block_templates(temp_storage: MarketStorage):
    """檢驗 8 大核心策略 (含 11~15) 與 5 種預設複合因子積木範本"""
    loader = MarketDataLoader(temp_storage)
    df = loader.generate_mock_series("2330", days=120, scenario="trend_up")

    core_ids = ["01", "02", "03", "06", "11", "12", "13", "14", "15"]
    for sid in core_ids:
        strat = StrategyRegistry.get(sid, storage=temp_storage) if sid in ["14", "15"] else StrategyRegistry.get(sid)
        assert strat is not None, f"Strategy {sid} should be registered"

    # 測試 5 種複合因子範本
    assert len(PRESET_COMPOSITE_TEMPLATES) == 5
    for tpl_id in PRESET_COMPOSITE_TEMPLATES.keys():
        tpl_strat = StrategyRegistry.get(tpl_id)
        assert isinstance(tpl_strat, CustomBlockStrategy)
        sigs = tpl_strat.generate_signals("2024-06-15", {"2330": df})
        assert isinstance(sigs, list)


def test_verification_engine_wfa_monte_carlo_and_plateau(temp_storage: MarketStorage):
    """
    檢驗統計驗證模組：
    1. 蒙地卡羅模擬 (Shuffle 與 Resample)
    2. 參數高原熱力圖 (★ 穩定高原標註 ＋ 孤島過擬合峰值排除)
    3. 前進分析 (Walk-Forward Analysis)
    """
    # 1. 蒙地卡羅模擬
    mock_trades = [
        TradeRecord(i, "2330", f"2024-01-{i+1:02d}", "SELL_TP1", 1000, 110.0, 100.0, 300.0, 109600.0, "LONG", 8000.0 if i % 2 == 0 else -3000.0, 8.0 if i % 2 == 0 else -3.0, "TP", 5)
        for i in range(1, 15)
    ]
    mc_shuffle = VerificationEngine.run_monte_carlo_simulation(mock_trades, iterations=500, mode="shuffle")
    mc_resample = VerificationEngine.run_monte_carlo_simulation(mock_trades, iterations=500, mode="resample")
    assert mc_shuffle["iterations"] == 500
    assert mc_shuffle["mdd_distribution"]["p95"] >= mc_shuffle["mdd_distribution"]["p50"]
    assert "p50" in mc_resample["return_distribution"]

    # 2. 參數高原掃描與 ★ 標記
    loader = MarketDataLoader(temp_storage)
    df = loader.generate_mock_series("2330", days=90, scenario="bb_reversal")
    plateau = VerificationEngine.run_parameter_plateau_scan(
        market_data={"2330": df},
        strategy_factory=lambda p: StrategyRegistry.get("01", **p),
        param_x_name="bb_period",
        param_x_values=[15, 20, 25],
        param_y_name="stop_loss_pct",
        param_y_values=[0.03, 0.045, 0.06],
    )
    assert len(plateau["grid"]) == 3
    assert len(plateau["grid"][0]) == 3
    assert plateau["plateau_stars_count"] >= 1
    assert any("★" in cell["label"] for row in plateau["grid"] for cell in row)

    # 3. 前進分析 (WFA)
    wfa = VerificationEngine.run_walk_forward_analysis(
        market_data={"2330": df},
        strategy_factory=lambda p: StrategyRegistry.get("01", **p),
        param_candidates=[{"bb_period": 15}, {"bb_period": 20}],
        n_splits=2,
    )
    assert wfa["status"] == "success"
    assert len(wfa["windows"]) >= 1
    assert "wfe_pct" in wfa


def test_portfolio_allocator_and_live_signals_and_api(temp_storage: MarketStorage):
    """檢驗多策略資產配置 (相關係數矩陣、利潤貢獻度)、每日訊號 CSV 匯出、K線重播解說與 FastAPI 端點"""
    loader = MarketDataLoader(temp_storage)
    df = loader.generate_mock_series("2330", days=90, scenario="trend_up")

    alloc = PortfolioAllocator.allocate(
        market_data={"2330": df},
        strategy_ids=["01", "02", "11"],
        method="sharpe_weighted",
        total_capital=1_000_000.0,
    )
    assert "correlation_matrix" in alloc
    assert alloc["correlation_matrix"]["01"]["01"] == 1.0
    assert len(alloc["profit_contribution"]) == 3
    assert abs(sum(alloc["weights"].values()) - 1.0) < 0.01

    # 測試每日訊號矩陣與 CSV
    sig_matrix = LiveSignalEngine.generate_daily_signal_matrix(
        market_data={"2330": df},
        watchlist=["2330"],
        strategy_ids=["01", "12"],
        holding_symbols=["2330"],
    )
    assert sig_matrix["count"] == 2
    assert "symbol,strategy_id" in sig_matrix["csv_content"]

    # 測試 FastAPI 路由
    client = TestClient(app)
    r_tpl = client.get("/api/lab/templates")
    assert r_tpl.status_code == 200
    assert len(r_tpl.json()["templates"]) == 5

    r_mc = client.post("/api/lab/verify/monte-carlo", json={"strategy_id": "01", "symbols": ["2330"], "iterations": 500, "mode": "shuffle"})
    assert r_mc.status_code == 200
    assert r_mc.json()["monte_carlo"]["iterations"] == 500

    r_alloc = client.post("/api/lab/portfolio/allocate", json={"strategy_ids": ["01", "02"], "symbols": ["2330"], "method": "inverse_volatility"})
    assert r_alloc.status_code == 200
    assert "correlation_matrix" in r_alloc.json()["allocation"]

    r_live = client.post("/api/lab/live/signals", json={"watchlist": ["2330"], "strategy_ids": ["01", "11"]})
    assert r_live.status_code == 200
    assert "csv_content" in r_live.json()

    # 測試 tpl_01 ~ tpl_05 複合範本回測與 01~09 實盤選股融合中心 API
    for tpl_code in ["tpl_01", "tpl_02", "tpl_03", "tpl_04", "tpl_05", "01", "02", "03"]:
        r_bt = client.post("/api/lab/backtest", json={"strategy_id": tpl_code, "symbols": ["2330"]})
        assert r_bt.status_code == 200, f"Backtest failed for {tpl_code}"
        bt_json = r_bt.json()
        assert bt_json["summary"]["total_trades"] > 0, f"Expected > 0 trades for {tpl_code}"

    r_fusion = client.get("/api/lab/fusion/summary")
    assert r_fusion.status_code == 200
    fusion_data = r_fusion.json()
    assert fusion_data["total_strategies"] >= 9
    assert fusion_data["active_strategies"] >= 1
    assert len(fusion_data["strategies"]) >= 9
