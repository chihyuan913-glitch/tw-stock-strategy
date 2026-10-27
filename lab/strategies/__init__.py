"""
台股量化策略實驗室 - 策略層模組 (Strategy Layer)
"""

from .base_strategy import BaseStrategy
from .adapters import (
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
