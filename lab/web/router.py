"""
Lab Web Router: 台股量化策略實驗室 FastAPI 路由
提供策略清單、即時回測執行、績效指標、權益曲線圖表與歷史資料管理 API。
"""

import os
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..data.storage import MarketStorage
from ..data.loader import MarketDataLoader
from ..engine.backtest_engine import BacktestEngine
from ..strategies.adapters import StrategyRegistry

router = APIRouter(prefix="/api/lab", tags=["Quant Strategy Lab"])

# 全域單例儲存與數據載入器
_storage = MarketStorage()
_loader = MarketDataLoader(_storage)


@router.get("/dashboard", response_class=HTMLResponse)
def get_lab_dashboard():
    """回傳量化策略實驗室現代深色戰情室儀表板"""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "lab.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Lab Dashboard Template Not Found</h1>", status_code=404)


class BacktestRequest(BaseModel):
    strategy_id: str = Field(default="01", description="策略編號或名稱 (如 01, 02)")
    symbols: List[str] = Field(default=["2330"], description="回測標的代碼清單")
    start_date: Optional[str] = Field(default=None, description="回測起始日期 (YYYY-MM-DD)")
    end_date: Optional[str] = Field(default=None, description="回測結束日期 (YYYY-MM-DD)")
    initial_cash: float = Field(default=1_000_000.0, description="起始資金 (元)")
    commission_discount: float = Field(default=0.6, description="手續費折讓 (預設 6 折)")
    tax_rate: float = Field(default=0.003, description="證券交易稅率")
    params: Dict[str, Any] = Field(default_factory=dict, description="覆蓋策略自訂參數")


@router.get("/strategies")
def get_strategies():
    """取得實驗室目前支援之量化策略清單與預設參數"""
    return {
        "status": "success",
        "strategies": StrategyRegistry.list_strategies(),
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
    """
    同步或產生指定標的行情
    """
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
    """
    執行量化策略回測並輸出完整績效度量
    """
    strategy = StrategyRegistry.get(req.strategy_id, **req.params)
    if not strategy:
        raise HTTPException(
            status_code=404,
            detail=f"找不到策略代碼 [{req.strategy_id}]。可用策略請參見 /api/lab/strategies",
        )

    # 載入所選標的的日 K 線
    market_data = {}
    for sym in req.symbols:
        df = _storage.get_daily_bars(sym, start_date=req.start_date, end_date=req.end_date)
        if df.empty:
            # 若資料庫暫無該標的，自動生成合理測試行情，確保隨開即用
            _loader.generate_mock_series(symbol=sym, scenario="bb_reversal" if req.strategy_id == "01" else "trend_up")
            df = _storage.get_daily_bars(sym, start_date=req.start_date, end_date=req.end_date)
        if not df.empty:
            market_data[sym] = df

    if not market_data:
        raise HTTPException(status_code=400, detail="無有效行情數據可供回測。")

    # 建立回測引擎
    engine = BacktestEngine(
        initial_cash=req.initial_cash,
        discount_factor=req.commission_discount,
        tax_rate=req.tax_rate,
    )

    # 執行回測 (包含大盤加權指數 Benchmark 運算)
    bm_series = _storage.get_benchmark_series(start_date=req.start_date, end_date=req.end_date)
    res = engine.run(
        market_data=market_data,
        signal_generator=strategy.generate_signals,
        start_date=req.start_date,
        end_date=req.end_date,
        benchmark_series=bm_series,
    )

    # 格式化輸出
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
        "summary": {
            "initial_cash": req.initial_cash,
            "final_equity": res.metrics.get("final_equity", req.initial_cash),
            "total_return_pct": res.metrics.get("total_return_pct", 0.0),
            "sharpe_ratio": res.metrics.get("sharpe_ratio", 0.0),
            "max_drawdown_pct": res.metrics.get("max_drawdown_pct", 0.0),
            "total_trades": len(trades),
        },
    }
