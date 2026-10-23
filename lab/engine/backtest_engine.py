"""
BacktestEngine: 台股原生事件驅動回測撮合核心 (雙向多空升級版)
支援零未來函數 (T+0 訊號 / T+1 撮合)、多方 (LONG) 與空方 (SHORT) 雙向撮合、四大防線狀態機、漲跌停鎖死防呆與交易成本精算。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Callable
import pandas as pd

from .portfolio import Portfolio, TradeRecord
from .performance import PerformanceEngine


@dataclass
class Signal:
    symbol: str
    date: str
    entry_price: float
    direction: str = "LONG"  # "LONG" (做多) 或 "SHORT" (做空)
    addon_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    stop_loss_price: float = 0.0
    target_shares: int = 1000
    meta: Dict[str, Any] = None


@dataclass
class BacktestResult:
    metrics: Dict[str, Any]
    equity_curve: List[Dict[str, Any]]
    trade_history: List[TradeRecord]
    portfolio: Portfolio


class BacktestEngine:
    def __init__(
        self,
        initial_cash: float = 1_000_000.0,
        commission_rate: float = 0.001425,
        discount_factor: float = 0.6,
        tax_rate: float = 0.003,
        short_margin_rate: float = 0.9,
        min_commission: float = 20.0,
        slippage_pct: float = 0.001,  # 預設 0.1% 滑價考量
    ):
        self.initial_cash = initial_cash
        self.commission_rate = commission_rate
        self.discount_factor = discount_factor
        self.tax_rate = tax_rate
        self.short_margin_rate = short_margin_rate
        self.min_commission = min_commission
        self.slippage_pct = slippage_pct

    def run(
        self,
        market_data: Dict[str, pd.DataFrame],
        signal_generator: Callable[[str, Dict[str, pd.DataFrame]], List[Signal]],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        benchmark_series: Optional[pd.Series] = None,
    ) -> BacktestResult:
        """
        執行事件驅動撮合主迴圈
        market_data: {symbol: df_bars (index 為 DatetimeIndex 或包含 'date' 欄位)}
        signal_generator: 簽名為 func(date, historical_data_up_to_today) -> List[Signal]
        benchmark_series: 大盤基準指數收盤價序列 (例如加權指數 ^TWII)
        """
        portfolio = Portfolio(
            initial_cash=self.initial_cash,
            commission_rate=self.commission_rate,
            discount_factor=self.discount_factor,
            tax_rate=self.tax_rate,
            short_margin_rate=self.short_margin_rate,
            min_commission=self.min_commission,
        )

        all_dates = set()
        clean_market_data: Dict[str, pd.DataFrame] = {}
        for sym, df in market_data.items():
            df_c = df.copy()
            if not isinstance(df_c.index, pd.DatetimeIndex):
                if "date" in df_c.columns:
                    df_c["date"] = pd.to_datetime(df_c["date"])
                    df_c.set_index("date", inplace=True)
            df_c.sort_index(inplace=True)
            clean_market_data[sym] = df_c
            for d in df_c.index.strftime("%Y-%m-%d"):
                all_dates.add(d)

        sorted_dates = sorted(list(all_dates))
        if start_date:
            sorted_dates = [d for d in sorted_dates if d >= start_date]
        if end_date:
            sorted_dates = [d for d in sorted_dates if d <= end_date]

        if not sorted_dates:
            metrics = PerformanceEngine.calculate_metrics(self.initial_cash, [], [])
            return BacktestResult(metrics, [], [], portfolio)

        pending_orders: List[Signal] = []

        for date_idx, cur_date in enumerate(sorted_dates):
            today_bars = {}
            for sym, df_c in clean_market_data.items():
                cur_dt = pd.to_datetime(cur_date)
                if cur_dt in df_c.index:
                    row = df_c.loc[cur_dt]
                    today_bars[sym] = {
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(row["volume"]),
                    }

            # -------------------------------------------------------------
            # 1. 執行 T+1 日撮合（撮合前一日盤後產生的 pending_orders）
            # -------------------------------------------------------------
            for order in pending_orders:
                sym = order.symbol
                if sym not in today_bars:
                    continue
                bar = today_bars[sym]
                open_p = bar["open"]
                high_p = bar["high"]
                low_p = bar["low"]
                vol = bar["volume"]

                if order.direction == "LONG":
                    # 做多撮合：漲停鎖死防呆
                    is_limit_up_locked = (open_p == high_p) and (vol < 50)
                    if is_limit_up_locked:
                        continue

                    if low_p <= order.entry_price:
                        match_price = min(open_p, order.entry_price) * (1.0 + self.slippage_pct)
                        match_price = round(match_price, 2)
                        if portfolio.can_open(match_price, order.target_shares, direction="LONG"):
                            portfolio.open_position(
                                symbol=sym,
                                date=cur_date,
                                price=match_price,
                                shares=order.target_shares,
                                direction="LONG",
                                entry_target_price=order.entry_price,
                                addon_price=order.addon_price,
                                tp1_price=order.tp1_price,
                                tp2_price=order.tp2_price,
                                stop_loss_price=order.stop_loss_price,
                                reason="ENTRY_LONG",
                            )
                else:
                    # 做空撮合：跌停鎖死防呆 (禁止追空)
                    is_limit_down_locked = (open_p == low_p) and (vol < 50)
                    if is_limit_down_locked:
                        continue

                    if high_p >= order.entry_price:
                        match_price = max(open_p, order.entry_price) * (1.0 - self.slippage_pct)
                        match_price = round(match_price, 2)
                        if portfolio.can_open(match_price, order.target_shares, direction="SHORT"):
                            portfolio.open_position(
                                symbol=sym,
                                date=cur_date,
                                price=match_price,
                                shares=order.target_shares,
                                direction="SHORT",
                                entry_target_price=order.entry_price,
                                addon_price=order.addon_price,
                                tp1_price=order.tp1_price,
                                tp2_price=order.tp2_price,
                                stop_loss_price=order.stop_loss_price,
                                reason="ENTRY_SHORT",
                            )
            pending_orders = []

            # -------------------------------------------------------------
            # 2. 持倉部位四大防線狀態機檢核
            # -------------------------------------------------------------
            open_symbols = list(portfolio.positions.keys())
            for sym in open_symbols:
                if sym not in today_bars:
                    continue
                pos = portfolio.positions.get(sym)
                if not pos:
                    continue

                bar = today_bars[sym]
                open_p = bar["open"]
                high_p = bar["high"]
                low_p = bar["low"]
                vol = bar["volume"]

                if pos.direction == "LONG":
                    is_limit_down_locked = (open_p == low_p) and (vol < 50)

                    # A. 嚴格停損 (STOP LOSS) - 跌破防線
                    if not is_limit_down_locked and pos.stop_loss_price > 0 and low_p <= pos.stop_loss_price:
                        exit_price = min(open_p, pos.stop_loss_price) * (1.0 - self.slippage_pct)
                        exit_price = round(exit_price, 2)
                        portfolio.close_position(sym, cur_date, exit_price, reason="STOP_LOSS")
                        continue

                    # B. 目標停利 (TP1 / TP2)
                    if not is_limit_down_locked:
                        if not pos.tp1_done and pos.tp1_price > 0 and high_p >= pos.tp1_price:
                            exit_price = max(open_p, pos.tp1_price) * (1.0 - self.slippage_pct)
                            exit_price = round(exit_price, 2)
                            if pos.shares > 1000:
                                half_shares = (pos.shares // 2000) * 1000
                                portfolio.close_position(sym, cur_date, exit_price, shares_to_sell=half_shares, reason="TP1_HALF")
                            else:
                                portfolio.close_position(sym, cur_date, exit_price, reason="TP1_ALL")
                                continue

                        if pos.tp1_done and pos.tp2_price > 0 and high_p >= pos.tp2_price:
                            exit_price = max(open_p, pos.tp2_price) * (1.0 - self.slippage_pct)
                            exit_price = round(exit_price, 2)
                            portfolio.close_position(sym, cur_date, exit_price, reason="TP2_FINAL")
                            continue

                    # C. 動能加碼 (ADDON)
                    if not pos.addon_done and pos.addon_price > 0 and high_p >= pos.addon_price:
                        addon_p = max(open_p, pos.addon_price) * (1.0 + self.slippage_pct)
                        addon_p = round(addon_p, 2)
                        portfolio.add_to_position(sym, cur_date, addon_p, shares=1000, reason="ADDON_BREAKOUT")

                else:
                    # 空方狀態機 (SHORT)
                    is_limit_up_locked = (open_p == high_p) and (vol < 50)

                    # A. 嚴格停損 (STOP LOSS) - 向上突破停損價
                    if not is_limit_up_locked and pos.stop_loss_price > 0 and high_p >= pos.stop_loss_price:
                        exit_price = max(open_p, pos.stop_loss_price) * (1.0 + self.slippage_pct)
                        exit_price = round(exit_price, 2)
                        portfolio.close_position(sym, cur_date, exit_price, reason="STOP_LOSS")
                        continue

                    # B. 目標停利 (TP1 / TP2) - 向下摜破獲利目標回補
                    if not is_limit_up_locked:
                        if not pos.tp1_done and pos.tp1_price > 0 and low_p <= pos.tp1_price:
                            exit_price = min(open_p, pos.tp1_price) * (1.0 + self.slippage_pct)
                            exit_price = round(exit_price, 2)
                            if pos.shares > 1000:
                                half_shares = (pos.shares // 2000) * 1000
                                portfolio.close_position(sym, cur_date, exit_price, shares_to_sell=half_shares, reason="COVER_TP1_HALF")
                            else:
                                portfolio.close_position(sym, cur_date, exit_price, reason="COVER_TP1_ALL")
                                continue

                        if pos.tp1_done and pos.tp2_price > 0 and low_p <= pos.tp2_price:
                            exit_price = min(open_p, pos.tp2_price) * (1.0 + self.slippage_pct)
                            exit_price = round(exit_price, 2)
                            portfolio.close_position(sym, cur_date, exit_price, reason="COVER_TP2_FINAL")
                            continue

                    # C. 破線加碼 (SHORT ADDON) - 向下跌破加碼價
                    if not pos.addon_done and pos.addon_price > 0 and low_p <= pos.addon_price:
                        addon_p = min(open_p, pos.addon_price) * (1.0 - self.slippage_pct)
                        addon_p = round(addon_p, 2)
                        portfolio.add_to_position(sym, cur_date, addon_p, shares=1000, reason="SHORT_ADDON_BREAKDOWN")

            # -------------------------------------------------------------
            # 3. 每日收盤結算 Mark-to-Market
            # -------------------------------------------------------------
            closing_prices = {sym: bar["close"] for sym, bar in today_bars.items()}
            portfolio.mark_to_market(cur_date, closing_prices)

            # -------------------------------------------------------------
            # 4. T 日收盤後計算信號 (零未來函數)
            # -------------------------------------------------------------
            hist_snapshot = {}
            for sym, df_c in clean_market_data.items():
                cur_dt = pd.to_datetime(cur_date)
                slice_df = df_c.loc[:cur_dt]
                if not slice_df.empty:
                    hist_snapshot[sym] = slice_df

            new_signals = signal_generator(cur_date, hist_snapshot)
            if new_signals:
                for sig in new_signals:
                    if sig.symbol not in portfolio.positions:
                        pending_orders.append(sig)

        # 回測結束，計算包含 Benchmark 基準的統計指標
        metrics = PerformanceEngine.calculate_metrics(
            initial_cash=self.initial_cash,
            equity_curve=portfolio.equity_curve,
            trade_history=portfolio.trade_history,
            benchmark_series=benchmark_series,
        )
        return BacktestResult(
            metrics=metrics,
            equity_curve=portfolio.equity_curve,
            trade_history=portfolio.trade_history,
            portfolio=portfolio,
        )
