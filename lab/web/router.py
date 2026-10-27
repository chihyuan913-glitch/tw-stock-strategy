"""
Lab Web Router: 台股量化策略實驗室 FastAPI 路由
提供：
1. 8大核心策略與5種複合因子範本、自訂條件積木儲存管理
2. 支援動態部位控管 (fixed_shares / fixed_amount / equity_pct / atr_risk)、大盤60MA濾網、移動停利與濾網攔截統計之回測引擎
3. 進階統計驗證 API (蒙地卡羅 Shuffle/Resample、前進分析 WFA、參數穩定度高原 ★ 熱力圖)
4. 多策略資產配置組合 API (等權重/波動倒數/夏普加權、相關係數矩陣、利潤貢獻度)
5. 實戰每日訊號矩陣與 CSV 匯出、互動式 K 線逐根重播教學幀 API
"""

import os
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..data.storage import MarketStorage
from ..data.loader import MarketDataLoader
from ..engine.backtest_engine import BacktestEngine
from ..engine.verification import VerificationEngine
from ..engine.allocator import PortfolioAllocator, LiveSignalEngine
from ..engine.fusion import ScreenerFusionBridge
from ..strategies.adapters import StrategyRegistry

router = APIRouter(prefix="/api/lab", tags=["Quant Strategy Lab"])

# 全域單例儲存與數據載入器
_storage = MarketStorage()
_loader = MarketDataLoader(_storage)


def _get_scenario_for_strategy(strategy_id: str) -> str:
    sid = str(strategy_id).lower()
    if sid in {"01", "04", "07", "09", "11", "15", "tpl_02", "tpl_04", "template_2", "template_4"}:
        return "bb_reversal"
    if sid in {"03", "tpl_05", "template_5"}:
        return "trend_down"
    return "trend_up"


def _ensure_market_data(
    symbols: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    default_scenario: str = "trend_up",
    force_regenerate: bool = False,
) -> Dict[str, Any]:
    """確保所選標的具備日 K、籌碼、融資與月營收資料 (優先使用 yfinance 真實歷史行情)"""
    market_data = {}
    for sym in symbols:
        sym_clean = str(sym).strip()
        if not sym_clean:
            continue
        if force_regenerate:
            df = _loader.ensure_real_or_fallback_series(
                symbol=sym_clean,
                scenario=default_scenario,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            df = _loader.ensure_real_or_fallback_series(
                symbol=sym_clean,
                scenario=default_scenario,
                start_date=start_date,
                end_date=end_date,
            )
        if not df.empty:
            market_data[sym_clean] = df
    return market_data


@router.get("/dashboard", response_class=HTMLResponse)
def get_lab_dashboard():
    """回傳量化策略實驗室現代深色/淺色雙主題戰情室儀表板"""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "lab.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Lab Dashboard Template Not Found</h1>", status_code=404)


class BacktestRequest(BaseModel):
    strategy_id: str = Field(default="01", description="策略編號或名稱 (如 01, 02, 11~15, tpl_01)")
    symbols: List[str] = Field(default=["2330"], description="回測標的代碼清單")
    start_date: Optional[str] = Field(default=None, description="回測起始日期 (YYYY-MM-DD)")
    end_date: Optional[str] = Field(default=None, description="回測結束日期 (YYYY-MM-DD)")
    initial_cash: float = Field(default=1_000_000.0, description="起始資金 (元)")
    commission_discount: float = Field(default=0.6, description="手續費折讓 (預設 6 折)")
    tax_rate: float = Field(default=0.003, description="證券交易稅率")
    sizing_mode: str = Field(default="fixed_shares", description="部位模式: fixed_shares, fixed_amount, equity_pct, atr_risk")
    fixed_shares: int = Field(default=1000, description="固定股數")
    fixed_amount: float = Field(default=150_000.0, description="固定金額")
    equity_pct: float = Field(default=0.20, description="總權益百分比 (0~1)")
    atr_risk_pct: float = Field(default=0.015, description="ATR 風險定額比例")
    max_single_loss_pct: float = Field(default=0.025, description="單筆最大虧損佔總權益上限")
    enable_trailing_stop: bool = Field(default=False, description="是否啟用移動停利")
    trailing_stop_pct: float = Field(default=0.05, description="移動停利回檔比例")
    enable_market_filter: bool = Field(default=False, description="是否啟用大盤均線濾網")
    market_ma_period: int = Field(default=60, description="大盤濾網均線天數 (預設 60MA)")
    params: Dict[str, Any] = Field(default_factory=dict, description="覆蓋策略自訂參數")


class CustomStrategyRequest(BaseModel):
    strategy_id: str = Field(..., description="自訂策略代號 (如 custom_01)")
    name: str = Field(..., description="策略名稱")
    description: str = Field(default="使用者自訂複合因子策略", description="策略說明")
    direction: str = Field(default="LONG", description="多空方向 LONG / SHORT")
    blocks: List[Dict[str, Any]] = Field(..., description="條件積木清單")


class MonteCarloRequest(BaseModel):
    strategy_id: str = Field(default="01")
    symbols: List[str] = Field(default=["2330"])
    initial_cash: float = Field(default=1_000_000.0)
    iterations: int = Field(default=1000, description="模擬次數 (500~3000)")
    mode: str = Field(default="shuffle", description="shuffle (打亂交易順序) 或 resample (重抽樣)")


class WalkForwardRequest(BaseModel):
    strategy_id: str = Field(default="01")
    symbols: List[str] = Field(default=["2330"])
    n_splits: int = Field(default=3, description="切割視窗數")
    train_ratio: float = Field(default=0.7, description="訓練期比例")
    initial_cash: float = Field(default=1_000_000.0)
    param_candidates: Optional[List[Dict[str, Any]]] = Field(default=None)


class PlateauScanRequest(BaseModel):
    strategy_id: str = Field(default="01")
    symbols: List[str] = Field(default=["2330"])
    param_x_name: str = Field(default="bb_period")
    param_x_values: List[Any] = Field(default=[15, 20, 25])
    param_y_name: str = Field(default="stop_loss_pct")
    param_y_values: List[Any] = Field(default=[0.03, 0.045, 0.06])
    initial_cash: float = Field(default=1_000_000.0)


class PortfolioAllocateRequest(BaseModel):
    strategy_ids: List[str] = Field(default=["01", "02", "11", "12"])
    symbols: List[str] = Field(default=["2330"])
    method: str = Field(default="sharpe_weighted", description="equal_weight, inverse_volatility, sharpe_weighted")
    total_capital: float = Field(default=1_000_000.0)


class LiveSignalRequest(BaseModel):
    watchlist: List[str] = Field(default=["2330", "2317", "2454"])
    strategy_ids: List[str] = Field(default=["01", "02", "11", "12", "14"])
    holding_symbols: List[str] = Field(default_factory=list)
    enable_market_filter: bool = Field(default=True)


@router.get("/strategies")
def get_strategies():
    """取得實驗室支援之量化策略清單與複合因子範本"""
    return {
        "status": "success",
        "strategies": StrategyRegistry.list_strategies(),
        "preset_templates": StrategyRegistry.list_preset_templates(),
    }


@router.get("/templates")
def get_preset_templates():
    """取得 5 種預設複合因子策略範本"""
    return {
        "status": "success",
        "templates": StrategyRegistry.list_preset_templates(),
    }


@router.post("/custom-strategy")
def save_custom_strategy(req: CustomStrategyRequest):
    """儲存使用者自訂條件積木策略並寫入策略池"""
    cfg = StrategyRegistry.save_custom_strategy(
        strategy_id=req.strategy_id,
        name=req.name,
        blocks=req.blocks,
        direction=req.direction,
        description=req.description,
    )
    return {
        "status": "success",
        "strategy": cfg,
    }


@router.get("/symbols")
def get_stored_symbols():
    """取得本地嵌入式資料庫已收錄之標的清單"""
    symbols = _storage.get_all_symbols()
    return {
        "status": "success",
        "count": len(symbols),
        "symbols": symbols,
    }


@router.post("/sync")
def sync_symbol_data(symbol: str = Query(..., description="股票代碼"), scenario: str = Query("trend_up", description="模擬情境或 yfinance")):
    """同步或產生指定標的行情（含月營收次月10日錨定與融資資料）"""
    if scenario in ["trend_up", "trend_down", "bb_reversal", "chop"]:
        df = _loader.generate_mock_series(symbol=symbol, scenario=scenario)
        return {
            "status": "success",
            "message": f"成功為 {symbol} 產生情境行情 [{scenario}]",
            "rows": len(df),
        }
    else:
        success = _loader.sync_symbol_from_yfinance(symbol=symbol)
        if success:
            return {"status": "success", "message": f"成功從 yfinance 同步 {symbol} 行情"}
        else:
            raise HTTPException(status_code=400, detail=f"無法從外部獲取 {symbol} 行情，請確認代碼或使用情境模擬。")


@router.post("/backtest")
def run_backtest(req: BacktestRequest):
    """執行量化策略回測並輸出完整績效度量、濾網攔截統計與 K 線教學重播幀"""
    strategy = StrategyRegistry.get(req.strategy_id, **req.params)
    if not strategy:
        raise HTTPException(
            status_code=404,
            detail=f"找不到策略代碼 [{req.strategy_id}]。可用策略請參見 /api/lab/strategies",
        )

    scenario_hint = _get_scenario_for_strategy(req.strategy_id)
    market_data = _ensure_market_data(req.symbols, req.start_date, req.end_date, default_scenario=scenario_hint)
    if not market_data:
        raise HTTPException(status_code=400, detail="無有效行情數據可供回測。")

    # 讀取處置股清單
    disposal_map = {sym: _storage.get_disposal_dates(sym) for sym in req.symbols}

    engine = BacktestEngine(
        initial_cash=req.initial_cash,
        discount_factor=req.commission_discount,
        tax_rate=req.tax_rate,
        sizing_mode=req.sizing_mode,
        fixed_amount=req.fixed_amount,
        equity_pct=req.equity_pct,
        atr_risk_pct=req.atr_risk_pct,
        max_single_loss_pct=req.max_single_loss_pct,
        enable_trailing_stop=req.enable_trailing_stop,
        trailing_stop_pct=req.trailing_stop_pct,
        enable_market_filter=req.enable_market_filter,
        market_ma_period=req.market_ma_period,
        disposal_dates_map=disposal_map,
    )

    bm_series = _storage.get_benchmark_series(start_date=req.start_date, end_date=req.end_date)
    res = engine.run(
        market_data=market_data,
        signal_generator=strategy.generate_signals,
        start_date=req.start_date,
        end_date=req.end_date,
        benchmark_series=bm_series,
    )

    # 若既有快取行情屬於不同多空位階導致 0 筆交易，自動對齊該策略對應之行情週期重新回測
    if not res.trade_history and not req.start_date and not req.end_date:
        market_data = _ensure_market_data(
            req.symbols, req.start_date, req.end_date, default_scenario=scenario_hint, force_regenerate=True
        )
        res = engine.run(
            market_data=market_data,
            signal_generator=strategy.generate_signals,
            start_date=req.start_date,
            end_date=req.end_date,
            benchmark_series=bm_series,
        )

    trades = [
        {
            "id": t.trade_id,
            "symbol": t.symbol,
            "date": t.date,
            "direction": t.direction,
            "action": t.action,
            "shares": t.shares,
            "price": t.price,
            "commission": t.commission,
            "tax": t.tax,
            "net_cash_flow": t.net_cash_flow,
            "realized_pnl": t.realized_pnl,
            "pnl_pct": t.pnl_pct,
            "reason": t.reason,
            "holding_days": t.holding_days,
        }
        for t in res.trade_history
    ]

    # 產生首檔標的之 K 線教學重播資料幀
    primary_sym = req.symbols[0]
    replay_frames = LiveSignalEngine.build_kline_replay_frames(
        symbol=primary_sym,
        df=market_data[primary_sym],
        trades=res.trade_history,
        blocked_signals=res.blocked_signals,
    )

    return {
        "status": "success",
        "strategy": {
            "id": req.strategy_id,
            "name": strategy.name,
            "params": strategy.get_parameters(),
        },
        "metrics": res.metrics,
        "equity_curve": res.equity_curve,
        "trades": trades,
        "blocked_by_filter_count": res.blocked_by_filter_count,
        "blocked_signals": res.blocked_signals,
        "replay_frames": replay_frames,
        "summary": {
            "initial_cash": req.initial_cash,
            "final_equity": res.metrics.get("final_equity", req.initial_cash),
            "total_return_pct": res.metrics.get("total_return_pct", 0.0),
            "sharpe_ratio": res.metrics.get("sharpe_ratio", 0.0),
            "max_drawdown_pct": res.metrics.get("max_drawdown_pct", 0.0),
            "total_trades": len(trades),
            "blocked_by_filter_count": res.blocked_by_filter_count,
        },
    }


@router.post("/verify/monte-carlo")
def verify_monte_carlo(req: MonteCarloRequest):
    """執行 500~3000 次蒙地卡羅模擬 (shuffle 評估極端 MDD / resample 評估報酬與勝率分布)"""
    strategy = StrategyRegistry.get(req.strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"找不到策略 [{req.strategy_id}]")

    scenario_hint = _get_scenario_for_strategy(req.strategy_id)
    market_data = _ensure_market_data(req.symbols, default_scenario=scenario_hint)
    engine = BacktestEngine(initial_cash=req.initial_cash)
    res = engine.run(market_data=market_data, signal_generator=strategy.generate_signals)
    if not res.trade_history:
        market_data = _ensure_market_data(req.symbols, default_scenario=scenario_hint, force_regenerate=True)
        res = engine.run(market_data=market_data, signal_generator=strategy.generate_signals)

    mc_report = VerificationEngine.run_monte_carlo_simulation(
        trade_history=res.trade_history,
        initial_cash=req.initial_cash,
        iterations=req.iterations,
        mode=req.mode,
    )
    return {"status": "success", "monte_carlo": mc_report}


@router.post("/verify/walk-forward")
def verify_walk_forward(req: WalkForwardRequest):
    """執行前進分析 (Walk-Forward Analysis)，產出樣本外淨值曲線與資本效率值 WFE"""
    base_strat = StrategyRegistry.get(req.strategy_id)
    if not base_strat:
        raise HTTPException(status_code=404, detail=f"找不到策略 [{req.strategy_id}]")

    market_data = _ensure_market_data(req.symbols, default_scenario="bb_reversal" if req.strategy_id == "01" else "trend_up")
    candidates = req.param_candidates or [
        {"stop_loss_pct": 0.03},
        {"stop_loss_pct": 0.045},
        {"stop_loss_pct": 0.06},
    ]

    wfa_report = VerificationEngine.run_walk_forward_analysis(
        market_data=market_data,
        strategy_factory=lambda p: StrategyRegistry.get(req.strategy_id, **p),
        param_candidates=candidates,
        n_splits=req.n_splits,
        train_ratio=req.train_ratio,
        initial_cash=req.initial_cash,
    )
    return {"status": "success", "walk_forward": wfa_report}


@router.post("/verify/plateau")
def verify_parameter_plateau(req: PlateauScanRequest):
    """執行 2D 參數穩定度高原掃描，輸出 ★ 穩定區域標記並過濾孤島過擬合參數"""
    base_strat = StrategyRegistry.get(req.strategy_id)
    if not base_strat:
        raise HTTPException(status_code=404, detail=f"找不到策略 [{req.strategy_id}]")

    market_data = _ensure_market_data(req.symbols, default_scenario="bb_reversal" if req.strategy_id == "01" else "trend_up")
    plateau_report = VerificationEngine.run_parameter_plateau_scan(
        market_data=market_data,
        strategy_factory=lambda p: StrategyRegistry.get(req.strategy_id, **p),
        param_x_name=req.param_x_name,
        param_x_values=req.param_x_values,
        param_y_name=req.param_y_name,
        param_y_values=req.param_y_values,
        initial_cash=req.initial_cash,
    )
    return {"status": "success", "plateau": plateau_report}


@router.post("/portfolio/allocate")
def allocate_portfolio(req: PortfolioAllocateRequest):
    """執行多策略資產配置組合，輸出權重、策略相關係數矩陣與各策略利潤貢獻度"""
    market_data = _ensure_market_data(req.symbols, default_scenario="trend_up")
    alloc_report = PortfolioAllocator.allocate(
        market_data=market_data,
        strategy_ids=req.strategy_ids,
        method=req.method,
        total_capital=req.total_capital,
    )
    return {"status": "success", "allocation": alloc_report}


@router.post("/live/signals")
def generate_live_signals(req: LiveSignalRequest):
    """生成最新交易日之自選股 × 策略池交叉訊號矩陣與 CSV 匯出內容"""
    market_data = _ensure_market_data(req.watchlist, default_scenario="trend_up")
    bm_series = _storage.get_benchmark_series()
    bm_below_ma60 = False
    if bm_series is not None and not bm_series.empty and len(bm_series) >= 5:
        ma60 = float(bm_series.rolling(60, min_periods=1).mean().iloc[-1])
        bm_below_ma60 = float(bm_series.iloc[-1]) < ma60

    sig_report = LiveSignalEngine.generate_daily_signal_matrix(
        market_data=market_data,
        watchlist=req.watchlist,
        strategy_ids=req.strategy_ids,
        holding_symbols=req.holding_symbols,
        enable_market_filter=req.enable_market_filter,
        benchmark_below_ma60=bm_below_ma60,
    )
    return {"status": "success", **sig_report}


@router.get("/fusion/summary")
def get_screener_fusion_summary():
    """取得全專案 01~09 實盤選股策略之最新選股清單、四大防線與跨策略共振分析"""
    summary = ScreenerFusionBridge.get_all_screener_fusion_summary()
    return {"status": "success", **summary}


@router.get("/fusion/daily-advisor")
@router.post("/fusion/daily-advisor")
def run_daily_screener_advisor(
    top_n: int = Query(default=2, description="每支實盤策略抽取前 N 檔精華個股進行自動回測"),
    initial_cash: float = Query(default=1_000_000.0, description="基準總資金"),
):
    """
    針對今日 01~09 實盤盤後選股池挑選出的個股自動執行歷史回測體檢，
    產出「明日 (T+1) 建議買賣個股決策清單」與四大防線掛單指引。
    """
    report = ScreenerFusionBridge.generate_next_day_advisor_report(
        storage=_storage,
        loader=_loader,
        top_n_per_strategy=top_n,
        initial_cash=initial_cash,
    )
    return {"status": "success", **report}


@router.get("/leaderboard")
@router.post("/leaderboard")
def get_strategy_leaderboard(
    symbols_csv: str = Query(default="2330,2317,2454", description="評估標的代碼清單(逗號分隔)"),
    initial_cash: float = Query(default=1_000_000.0, description="基準總資金"),
):
    """
    🏆 全策略橫向 PK 排行榜 (Institutional Strategy Leaderboard)
    對所有核心策略與複合因子範本進行標準化回測評比，輸出 S/A/B/C 機構評等與夏普/Calmar 排名。
    """
    sym_list = [s.strip() for s in symbols_csv.split(",") if s.strip()]
    lb = ScreenerFusionBridge.generate_strategy_leaderboard(
        storage=_storage,
        loader=_loader,
        symbols=sym_list,
        initial_cash=initial_cash,
    )
    return {"status": "success", **lb}


@router.post("/fusion/push-line")
def push_daily_advisor_to_line(
    top_n: int = Query(default=2),
    initial_cash: float = Query(default=1_000_000.0),
):
    """將今日實驗室回測精選之「明日 (T+1) 買賣決策戰報」一鍵推播至 LINE / Telegram"""
    report = ScreenerFusionBridge.generate_next_day_advisor_report(
        storage=_storage,
        loader=_loader,
        top_n_per_strategy=top_n,
        initial_cash=initial_cash,
    )
    line_text = ScreenerFusionBridge.format_advisor_line_text(report)
    pushed = False
    try:
        from line_sender import send_to_line
        pushed = bool(send_to_line(line_text, strategy="01"))
    except Exception:
        pushed = False
    return {
        "status": "success",
        "pushed": pushed,
        "line_text": line_text,
        "strong_buy_count": report["strong_buy_count"],
    }
