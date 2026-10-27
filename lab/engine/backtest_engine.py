"""
BacktestEngine: 台股原生事件驅動回測撮合核心 (複合因子與風控防護升級版)
支援零未來函數 (T+0 訊號 / T+1 撮合)、多方 (LONG) 與空方 (SHORT) 雙向撮合、
暫停交易 (Volume=0) 與處置股過濾、大盤均線濾網開關、ATR 動態部位、移動停利與濾網攔截次數統計。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
import numpy as np
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
    blocked_by_filter_count: int = 0
    blocked_signals: List[Dict[str, Any]] = field(default_factory=list)


class BacktestEngine:
    def __init__(
        self,
        initial_cash: float = 1_000_000.0,
        commission_rate: float = 0.001425,
        discount_factor: float = 0.6,
        tax_rate: float = 0.003,
        short_margin_rate: float = 0.9,
        min_commission: float = 20.0,
        slippage_pct: float = 0.001,
        sizing_mode: str = "fixed_shares",  # "fixed_shares", "fixed_amount", "equity_pct", "atr_risk"
        fixed_amount: float = 200_000.0,
        equity_pct: float = 0.20,
        atr_risk_pct: float = 0.015,
        max_single_loss_pct: float = 0.05,
        enable_trailing_stop: bool = False,
        trailing_stop_pct: float = 0.05,
        enable_market_filter: bool = False,
        market_ma_period: int = 60,
        disposal_dates_map: Optional[Dict[str, set]] = None,
    ):
        self.initial_cash = initial_cash
        self.commission_rate = commission_rate
        self.discount_factor = discount_factor
        self.tax_rate = tax_rate
        self.short_margin_rate = short_margin_rate
        self.min_commission = min_commission
        self.slippage_pct = slippage_pct

        # 動態部位與風險防護網參數
        self.sizing_mode = sizing_mode
        self.fixed_amount = fixed_amount
        self.equity_pct = equity_pct
        self.atr_risk_pct = atr_risk_pct
        self.max_single_loss_pct = max_single_loss_pct
        self.enable_trailing_stop = enable_trailing_stop
        self.trailing_stop_pct = trailing_stop_pct

        # 大盤與處置股濾網設定
        self.enable_market_filter = enable_market_filter
        self.market_ma_period = market_ma_period
        self.disposal_dates_map = disposal_dates_map or {}

    @staticmethod
    def _calc_atr14(df_slice: pd.DataFrame) -> float:
        """計算截至當日之 14 日真實波動幅度均值 (ATR)"""
        if len(df_slice) < 2:
            return float(df_slice["close"].iloc[-1] * 0.02) if not df_slice.empty else 1.0
        high = df_slice["high"]
        low = df_slice["low"]
        prev_close = df_slice["close"].shift(1).fillna(df_slice["open"])
        tr = pd.concat([
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.tail(14).mean()
        return float(atr) if not np.isnan(atr) and atr > 0 else float(df_slice["close"].iloc[-1] * 0.02)

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
            metrics["blocked_by_filter_count"] = 0
            return BacktestResult(metrics, [], [], portfolio, 0, [])

        # 預先整理大盤基準序列
        clean_bm: Optional[pd.Series] = None
        if benchmark_series is not None and not benchmark_series.empty:
            clean_bm = benchmark_series.copy()
            if not isinstance(clean_bm.index, pd.DatetimeIndex):
                clean_bm.index = pd.to_datetime(clean_bm.index)
            clean_bm.sort_index(inplace=True)

        pending_orders: List[Signal] = []
        blocked_signals: List[Dict[str, Any]] = []

        for date_idx, cur_date in enumerate(sorted_dates):
            cur_dt = pd.to_datetime(cur_date)
            today_bars = {}
            for sym, df_c in clean_market_data.items():
                if cur_dt in df_c.index:
                    row = df_c.loc[cur_dt]
                    vol_val = int(row["volume"])
                    today_bars[sym] = {
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": vol_val,
                        "status": "SUSPENDED" if vol_val == 0 else "ACTIVE",
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

                # 當日成交量為 0 (暫停交易狀態)，禁止撮合進場
                if vol == 0 or bar["status"] == "SUSPENDED":
                    blocked_signals.append({
                        "date": cur_date,
                        "symbol": sym,
                        "reason": "SUSPENDED_ZERO_VOLUME_ON_EXECUTION",
                        "entry_price": order.entry_price,
                    })
                    continue

                # 計算 ATR 供動態部位使用
                hist_slice = clean_market_data[sym].loc[:cur_dt].iloc[:-1]
                atr_val = self._calc_atr14(hist_slice) if not hist_slice.empty else open_p * 0.02

                if order.direction == "LONG":
                    is_limit_up_locked = (open_p == high_p) and (vol < 50)
                    if is_limit_up_locked:
                        continue

                    if low_p <= order.entry_price:
                        match_price = min(open_p, order.entry_price) * (1.0 + self.slippage_pct)
                        match_price = round(match_price, 2)
                        shares_to_open = portfolio.calculate_position_size(
                            sizing_mode=self.sizing_mode,
                            price=match_price,
                            default_shares=order.target_shares,
                            stop_loss_price=order.stop_loss_price,
                            atr_value=atr_val,
                            fixed_amount=self.fixed_amount,
                            equity_pct=self.equity_pct,
                            atr_risk_pct=self.atr_risk_pct,
                            max_single_loss_pct=self.max_single_loss_pct,
                        )
                        if portfolio.can_open(match_price, shares_to_open, direction="LONG"):
                            portfolio.open_position(
                                symbol=sym,
                                date=cur_date,
                                price=match_price,
                                shares=shares_to_open,
                                direction="LONG",
                                entry_target_price=order.entry_price,
                                addon_price=order.addon_price,
                                tp1_price=order.tp1_price,
                                tp2_price=order.tp2_price,
                                stop_loss_price=order.stop_loss_price,
                                reason="ENTRY_LONG",
                            )
                else:
                    is_limit_down_locked = (open_p == low_p) and (vol < 50)
                    if is_limit_down_locked:
                        continue

                    if high_p >= order.entry_price:
                        match_price = max(open_p, order.entry_price) * (1.0 - self.slippage_pct)
                        match_price = round(match_price, 2)
                        shares_to_open = portfolio.calculate_position_size(
                            sizing_mode=self.sizing_mode,
                            price=match_price,
                            default_shares=order.target_shares,
                            stop_loss_price=order.stop_loss_price,
                            atr_value=atr_val,
                            fixed_amount=self.fixed_amount,
                            equity_pct=self.equity_pct,
                            atr_risk_pct=self.atr_risk_pct,
                            max_single_loss_pct=self.max_single_loss_pct,
                        )
                        if portfolio.can_open(match_price, shares_to_open, direction="SHORT"):
                            portfolio.open_position(
                                symbol=sym,
                                date=cur_date,
                                price=match_price,
                                shares=shares_to_open,
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
            # 2. 持倉部位四大防線狀態機檢核 (含移動停利 Trailing Stop)
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

                if vol == 0:
                    continue  # 暫停交易日無法平倉

                if pos.direction == "LONG":
                    is_limit_down_locked = (open_p == low_p) and (vol < 50)

                    # A. 嚴格停損 / 移動停利觸發 (STOP LOSS)
                    if not is_limit_down_locked and pos.stop_loss_price > 0 and low_p <= pos.stop_loss_price:
                        exit_price = min(open_p, pos.stop_loss_price) * (1.0 - self.slippage_pct)
                        exit_price = round(exit_price, 2)
                        sl_reason = "TRAILING_STOP" if (self.enable_trailing_stop and pos.stop_loss_price > pos.entry_price) else "STOP_LOSS"
                        portfolio.close_position(sym, cur_date, exit_price, reason=sl_reason)
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

                    # D. 更新移動停利防線 (供次日檢核)
                    if self.enable_trailing_stop and sym in portfolio.positions:
                        pos.highest_price = max(pos.highest_price, high_p)
                        new_trailing_sl = round(pos.highest_price * (1.0 - self.trailing_stop_pct), 2)
                        if new_trailing_sl > pos.stop_loss_price:
                            pos.stop_loss_price = new_trailing_sl

                else:
                    # 空方狀態機 (SHORT)
                    is_limit_up_locked = (open_p == high_p) and (vol < 50)

                    # A. 嚴格停損 (STOP LOSS)
                    if not is_limit_up_locked and pos.stop_loss_price > 0 and high_p >= pos.stop_loss_price:
                        exit_price = max(open_p, pos.stop_loss_price) * (1.0 + self.slippage_pct)
                        exit_price = round(exit_price, 2)
                        sl_reason = "TRAILING_STOP" if (self.enable_trailing_stop and pos.stop_loss_price < pos.entry_price) else "STOP_LOSS"
                        portfolio.close_position(sym, cur_date, exit_price, reason=sl_reason)
                        continue

                    # B. 目標停利 (TP1 / TP2)
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

                    # C. 破線加碼 (SHORT ADDON)
                    if not pos.addon_done and pos.addon_price > 0 and low_p <= pos.addon_price:
                        addon_p = min(open_p, pos.addon_price) * (1.0 - self.slippage_pct)
                        addon_p = round(addon_p, 2)
                        portfolio.add_to_position(sym, cur_date, addon_p, shares=1000, reason="SHORT_ADDON_BREAKDOWN")

                    # D. 更新空方移動停利防線
                    if self.enable_trailing_stop and sym in portfolio.positions:
                        pos.lowest_price = min(pos.lowest_price, low_p)
                        new_trailing_sl = round(pos.lowest_price * (1.0 + self.trailing_stop_pct), 2)
                        if pos.stop_loss_price == 0 or new_trailing_sl < pos.stop_loss_price:
                            pos.stop_loss_price = new_trailing_sl

            # -------------------------------------------------------------
            # 3. 每日收盤結算 Mark-to-Market
            # -------------------------------------------------------------
            closing_prices = {sym: bar["close"] for sym, bar in today_bars.items()}
            portfolio.mark_to_market(cur_date, closing_prices)

            # -------------------------------------------------------------
            # 4. T 日收盤後計算信號，並執行「交易狀態 / 處置股 / 大盤均線濾網」檢核
            # -------------------------------------------------------------
            hist_snapshot = {}
            for sym, df_c in clean_market_data.items():
                slice_df = df_c.loc[:cur_dt]
                if not slice_df.empty:
                    hist_snapshot[sym] = slice_df

            # 檢驗大盤環境濾網 (加權指數跌破 N 日均線時凍結新倉建立權限)
            market_freeze_new_entries = False
            if self.enable_market_filter and clean_bm is not None:
                bm_slice = clean_bm.loc[:cur_dt]
                if len(bm_slice) >= self.market_ma_period:
                    bm_close = float(bm_slice.iloc[-1])
                    bm_ma = float(bm_slice.tail(self.market_ma_period).mean())
                    if bm_close < bm_ma:
                        market_freeze_new_entries = True
                elif len(bm_slice) >= 2:
                    bm_close = float(bm_slice.iloc[-1])
                    bm_ma = float(bm_slice.mean())
                    if bm_close < bm_ma:
                        market_freeze_new_entries = True

            new_signals = signal_generator(cur_date, hist_snapshot)
            if new_signals:
                for sig in new_signals:
                    if sig.symbol in portfolio.positions:
                        continue

                    # 濾網 1: 當日成交量為 0 (暫停交易)
                    if sig.symbol in today_bars and today_bars[sig.symbol]["volume"] == 0:
                        blocked_signals.append({
                            "date": cur_date,
                            "symbol": sig.symbol,
                            "reason": "SUSPENDED_ZERO_VOLUME",
                            "entry_price": sig.entry_price,
                        })
                        continue

                    # 濾網 2: 處置股過濾
                    if cur_date in self.disposal_dates_map.get(sig.symbol, set()):
                        blocked_signals.append({
                            "date": cur_date,
                            "symbol": sig.symbol,
                            "reason": "DISPOSAL_STOCK",
                            "entry_price": sig.entry_price,
                        })
                        continue

                    # 濾網 3: 大盤跌破均線凍結新倉
                    if market_freeze_new_entries:
                        blocked_signals.append({
                            "date": cur_date,
                            "symbol": sig.symbol,
                            "reason": f"MARKET_BELOW_MA{self.market_ma_period}",
                            "entry_price": sig.entry_price,
                        })
                        continue

                    pending_orders.append(sig)

        # 回測結束，計算包含 Benchmark 基準與濾網攔截次數的統計指標
        metrics = PerformanceEngine.calculate_metrics(
            initial_cash=self.initial_cash,
            equity_curve=portfolio.equity_curve,
            trade_history=portfolio.trade_history,
            benchmark_series=benchmark_series,
        )
        metrics["blocked_by_filter_count"] = len(blocked_signals)

        return BacktestResult(
            metrics=metrics,
            equity_curve=portfolio.equity_curve,
            trade_history=portfolio.trade_history,
            portfolio=portfolio,
            blocked_by_filter_count=len(blocked_signals),
            blocked_signals=blocked_signals,
        )
