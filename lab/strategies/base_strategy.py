"""
BaseStrategy: 量化策略抽象基底類別
規範信號生成、四大防線定價計算與參數調節介面。
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import pandas as pd

from ..engine.backtest_engine import Signal


class BaseStrategy(ABC):
    def __init__(self, name: str, description: str = "", params: Optional[Dict[str, Any]] = None):
        self.name = name
        self.description = description
        self.params: Dict[str, Any] = params or {}

    def set_parameters(self, **kwargs):
        """動態調節策略參數"""
        self.params.update(kwargs)

    def get_parameters(self) -> Dict[str, Any]:
        return self.params.copy()

    @abstractmethod
    def generate_signals(
        self,
        date: str,
        historical_bars: Dict[str, pd.DataFrame],
        chips: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> List[Signal]:
        """
        在 T 日盤後生成進場訂單與四大防線。
        historical_bars 僅包含截至 T 日的行情，保證零未來函數。
        """
        pass
