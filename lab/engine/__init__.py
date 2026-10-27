"""
台股量化策略實驗室 - 事件驅動撮合、統計驗證、配置與實戰訊號核心 (Engine Layer)
"""

from .portfolio import Portfolio, Position, TradeRecord
from .performance import PerformanceEngine
from .backtest_engine import BacktestEngine, Signal, BacktestResult
from .verification import VerificationEngine
from .allocator import PortfolioAllocator, LiveSignalEngine
from .fusion import ScreenerFusionBridge

__all__ = [
    "Portfolio",
    "Position",
    "TradeRecord",
    "PerformanceEngine",
    "BacktestEngine",
    "Signal",
    "BacktestResult",
    "VerificationEngine",
    "PortfolioAllocator",
    "LiveSignalEngine",
    "ScreenerFusionBridge",
]
