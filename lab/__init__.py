"""
台股量化策略實驗室 (Taiwan Stock Quant Strategy Lab)
提供事件驅動回測核心、台股原生交割計量、嵌入式存儲與策略適配器。
"""

from .data.storage import MarketStorage
from .data.loader import MarketDataLoader
from .engine.backtest_engine import BacktestEngine, Signal, BacktestResult
from .engine.portfolio import Portfolio, Position, TradeRecord
from .engine.performance import PerformanceEngine
from .strategies.base_strategy import BaseStrategy
from .strategies.adapters import (
    BollingerReversalStrategy,
    InstitutionalMomentumStrategy,
    StrategyRegistry,
)

__all__ = [
    "MarketStorage",
    "MarketDataLoader",
    "BacktestEngine",
    "Signal",
    "BacktestResult",
    "Portfolio",
    "Position",
    "TradeRecord",
    "PerformanceEngine",
    "BaseStrategy",
    "BollingerReversalStrategy",
    "InstitutionalMomentumStrategy",
    "StrategyRegistry",
]
