"""
台股量化策略實驗室 (Taiwan Stock Quant Strategy Lab)
提供事件驅動回測核心、台股原生交割計量、嵌入式存儲、8 大核心策略、自訂積木策略、進階統計驗證與多策略組合配置。
"""

from .data.storage import MarketStorage
from .data.loader import MarketDataLoader
from .engine.backtest_engine import BacktestEngine, Signal, BacktestResult
from .engine.portfolio import Portfolio, Position, TradeRecord
from .engine.performance import PerformanceEngine
from .engine.verification import VerificationEngine
from .engine.allocator import PortfolioAllocator, LiveSignalEngine
from .engine.fusion import ScreenerFusionBridge
from .strategies.base_strategy import BaseStrategy
from .strategies.adapters import (
    BollingerReversalStrategy,
    InstitutionalMomentumStrategy,
    ShortMomentumStrategy,
    VolumeSurgeStrategy,
    KDGoldenCrossStrategy,
    MABullishAlignmentStrategy,
    GapUpMomentumStrategy,
    RevenueNewHighStrategy,
    MarginReductionStrategy,
    CustomBlockStrategy,
    PRESET_COMPOSITE_TEMPLATES,
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
    "VerificationEngine",
    "PortfolioAllocator",
    "LiveSignalEngine",
    "BaseStrategy",
    "BollingerReversalStrategy",
    "InstitutionalMomentumStrategy",
    "ShortMomentumStrategy",
    "VolumeSurgeStrategy",
    "KDGoldenCrossStrategy",
    "MABullishAlignmentStrategy",
    "GapUpMomentumStrategy",
    "RevenueNewHighStrategy",
    "MarginReductionStrategy",
    "CustomBlockStrategy",
    "PRESET_COMPOSITE_TEMPLATES",
    "StrategyRegistry",
]
