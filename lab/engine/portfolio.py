"""
Portfolio & Position: 帳戶資金、多空部位與交割款項管理 (雙向多空升級版)
支援現股做多 (LONG) 與融券/股期做空 (SHORT)、四大防線狀態機、台股手續費折讓與證交稅精算。
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class Position:
    symbol: str
    shares: int
    entry_price: float
    entry_date: str
    cost_basis: float  # 做多為買進總成本；做空為融券保證金＋手續費
    current_price: float
    direction: str = "LONG"  # "LONG" (做多) 或 "SHORT" (做空)
    entry_target_price: float = 0.0
    addon_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    stop_loss_price: float = 0.0
    addon_done: bool = False
    tp1_done: bool = False
    highest_price: float = 0.0
    lowest_price: float = 0.0

    def __post_init__(self):
        if self.highest_price == 0.0:
            self.highest_price = max(self.entry_price, self.current_price)
        if self.lowest_price == 0.0:
            self.lowest_price = min(self.entry_price, self.current_price)

    @property
    def market_value(self) -> float:
        """
        部位市場重估現值：
        - 多方部位：持股現值
        - 空方部位：原始保證金 ＋ 未實現價差利益 (跌賺漲賠)
        """
        if self.direction == "LONG":
            return self.shares * self.current_price
        else:
            price_diff = (self.entry_price - self.current_price) * self.shares
            return max(0.0, self.cost_basis + price_diff)

    @property
    def unrealized_pnl(self) -> float:
        if self.direction == "LONG":
            return (self.current_price - self.entry_price) * self.shares
        else:
            return (self.entry_price - self.current_price) * self.shares

    @property
    def unrealized_pnl_pct(self) -> float:
        denom = (self.entry_price * self.shares) if self.entry_price > 0 else 1.0
        return (self.unrealized_pnl / denom * 100.0)


@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    date: str
    action: str     # "BUY_ENTRY", "BUY_ADDON", "SELL_TP1", "SELL_TP2", "SELL_STOP_LOSS", "SELL_CLOSE", "SELL_SHORT", "BUY_COVER"
    shares: int
    price: float
    commission: float
    tax: float
    net_cash_flow: float  # 現金流入為正，流出為負
    direction: str = "LONG"  # "LONG" 或 "SHORT"
    realized_pnl: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""
    holding_days: int = 0


class Portfolio:
    def __init__(
        self,
        initial_cash: float = 1_000_000.0,
        commission_rate: float = 0.001425,
        discount_factor: float = 0.6,
        tax_rate: float = 0.003,
        short_margin_rate: float = 0.9,  # 台股融券保證金 90%
        min_commission: float = 20.0,
    ):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.effective_commission_rate = commission_rate * discount_factor
        self.tax_rate = tax_rate
        self.short_margin_rate = short_margin_rate
        self.min_commission = min_commission

        self.positions: Dict[str, Position] = {}
        self.trade_history: List[TradeRecord] = []
        self.equity_curve: List[Dict[str, Any]] = []
        self._trade_counter = 0

    def calc_buy_fee(self, price: float, shares: int) -> float:
        raw_fee = price * shares * self.effective_commission_rate
        return max(self.min_commission, math.floor(raw_fee))

    def calc_sell_cost(self, price: float, shares: int) -> tuple[float, float]:
        raw_fee = price * shares * self.effective_commission_rate
        fee = max(self.min_commission, math.floor(raw_fee))
        tax = math.floor(price * shares * self.tax_rate)
        return fee, tax

    def can_buy(self, price: float, shares: int) -> bool:
        return self.can_open(price, shares, direction="LONG")

    def can_open(self, price: float, shares: int, direction: str = "LONG") -> bool:
        if shares <= 0:
            return False
        if direction == "LONG":
            fee = self.calc_buy_fee(price, shares)
            return self.cash >= (price * shares + fee)
        else:
            fee, tax = self.calc_sell_cost(price, shares)
            margin_required = price * shares * self.short_margin_rate
            return self.cash >= (margin_required + fee)

    def calculate_position_size(
        self,
        sizing_mode: str,
        price: float,
        default_shares: int = 1000,
        stop_loss_price: float = 0.0,
        atr_value: float = 0.0,
        fixed_amount: float = 200_000.0,
        equity_pct: float = 0.20,
        atr_risk_pct: float = 0.015,
        max_single_loss_pct: float = 0.05,
    ) -> int:
        """
        動態資金部位控管模型：
        - fixed_shares: 固定股數 (預設 1000 股)
        - fixed_amount: 固定金額 (如每筆 20 萬)
        - equity_pct: 總權益比例 (如每筆佔總資金 20%)
        - atr_risk: ATR 風險定額分配 (依單筆可承受風險金額與 ATR/停損距離決定股數)
        並強制執行單筆最大虧損上限 (max_single_loss_pct) 檢核。
        """
        if price <= 0:
            return 0

        current_equity = self.equity_curve[-1]["total_equity"] if self.equity_curve else self.initial_cash
        mode = (sizing_mode or "fixed_shares").lower()

        if mode == "fixed_amount":
            raw_shares = int(fixed_amount / price)
            shares = max(100, (raw_shares // 100) * 100)
        elif mode == "equity_pct":
            budget = current_equity * equity_pct
            raw_shares = int(budget / price)
            shares = max(100, (raw_shares // 100) * 100)
        elif mode == "atr_risk":
            risk_budget = current_equity * atr_risk_pct
            stop_dist = abs(price - stop_loss_price) if stop_loss_price > 0 else 0.0
            atr_dist = atr_value * 2.0 if atr_value > 0 else 0.0
            per_share_risk = max(stop_dist, atr_dist, price * 0.015)
            raw_shares = int(risk_budget / per_share_risk)
            shares = max(100, (raw_shares // 100) * 100)
        else:
            shares = default_shares

        # 強制檢核單筆最大虧損上限 (Risk Cap Guard)
        if stop_loss_price > 0 and max_single_loss_pct > 0:
            unit_loss = abs(price - stop_loss_price)
            if unit_loss > 0:
                max_allowed_loss = current_equity * max_single_loss_pct
                if shares * unit_loss > max_allowed_loss:
                    capped_shares = int(max_allowed_loss / unit_loss)
                    shares = max(100, (capped_shares // 100) * 100)

        # 不超過可用現金上限
        max_affordable = int((self.cash * 0.98) / price)
        if shares > max_affordable and mode != "fixed_shares":
            shares = max(0, (max_affordable // 100) * 100)

        return shares

    def open_position(
        self,
        symbol: str,
        date: str,
        price: float,
        shares: int,
        direction: str = "LONG",
        entry_target_price: float = 0.0,
        addon_price: float = 0.0,
        tp1_price: float = 0.0,
        tp2_price: float = 0.0,
        stop_loss_price: float = 0.0,
        reason: str = "ENTRY",
    ) -> Optional[Position]:
        if not self.can_open(price, shares, direction):
            return None

        self._trade_counter += 1
        trade_id = f"T{self._trade_counter:05d}"

        if direction == "LONG":
            fee = self.calc_buy_fee(price, shares)
            total_cost = (price * shares) + fee
            self.cash -= total_cost

            pos = Position(
                symbol=symbol,
                shares=shares,
                entry_price=price,
                entry_date=date,
                cost_basis=total_cost,
                current_price=price,
                direction="LONG",
                entry_target_price=entry_target_price or price,
                addon_price=addon_price,
                tp1_price=tp1_price,
                tp2_price=tp2_price,
                stop_loss_price=stop_loss_price,
                highest_price=price,
                lowest_price=price,
            )
            self.positions[symbol] = pos

            rec = TradeRecord(
                trade_id=trade_id,
                symbol=symbol,
                date=date,
                direction="LONG",
                action="BUY_ENTRY",
                shares=shares,
                price=price,
                commission=fee,
                tax=0.0,
                net_cash_flow=-total_cost,
                reason=reason,
            )
        else:
            # 融券/做空建倉 (SELL_SHORT)
            fee, _ = self.calc_sell_cost(price, shares)
            margin_required = price * shares * self.short_margin_rate
            total_outlay = margin_required + fee
            self.cash -= total_outlay

            pos = Position(
                symbol=symbol,
                shares=shares,
                entry_price=price,
                entry_date=date,
                cost_basis=total_outlay,
                current_price=price,
                direction="SHORT",
                entry_target_price=entry_target_price or price,
                addon_price=addon_price,
                tp1_price=tp1_price,
                tp2_price=tp2_price,
                stop_loss_price=stop_loss_price,
                highest_price=price,
                lowest_price=price,
            )
            self.positions[symbol] = pos

            rec = TradeRecord(
                trade_id=trade_id,
                symbol=symbol,
                date=date,
                direction="SHORT",
                action="SELL_SHORT",
                shares=shares,
                price=price,
                commission=fee,
                tax=0.0,
                net_cash_flow=-total_outlay,
                reason=reason,
            )

        self.trade_history.append(rec)
        return pos

    def add_to_position(
        self,
        symbol: str,
        date: str,
        price: float,
        shares: int,
        reason: str = "ADDON",
    ) -> bool:
        if symbol not in self.positions:
            return False
        pos = self.positions[symbol]

        if not self.can_open(price, shares, pos.direction):
            return False

        self._trade_counter += 1
        trade_id = f"T{self._trade_counter:05d}"

        if pos.direction == "LONG":
            fee = self.calc_buy_fee(price, shares)
            total_cost = (price * shares) + fee
            self.cash -= total_cost
            pos.shares += shares
            pos.cost_basis += total_cost
            pos.current_price = price
            pos.addon_done = True
            pos.highest_price = max(pos.highest_price, price)

            rec = TradeRecord(
                trade_id=trade_id,
                symbol=symbol,
                date=date,
                direction="LONG",
                action="BUY_ADDON",
                shares=shares,
                price=price,
                commission=fee,
                tax=0.0,
                net_cash_flow=-total_cost,
                reason=reason,
            )
        else:
            fee, _ = self.calc_sell_cost(price, shares)
            margin_required = price * shares * self.short_margin_rate
            total_outlay = margin_required + fee
            self.cash -= total_outlay
            pos.shares += shares
            pos.cost_basis += total_outlay
            pos.current_price = price
            pos.addon_done = True
            pos.lowest_price = min(pos.lowest_price, price)

            rec = TradeRecord(
                trade_id=trade_id,
                symbol=symbol,
                date=date,
                direction="SHORT",
                action="SHORT_ADDON",
                shares=shares,
                price=price,
                commission=fee,
                tax=0.0,
                net_cash_flow=-total_outlay,
                reason=reason,
            )

        self.trade_history.append(rec)
        return True

    def close_position(
        self,
        symbol: str,
        date: str,
        price: float,
        shares_to_sell: Optional[int] = None,
        reason: str = "CLOSE",
    ) -> Optional[TradeRecord]:
        if symbol not in self.positions:
            return None
        pos = self.positions[symbol]
        shares = shares_to_sell if (shares_to_sell and shares_to_sell <= pos.shares) else pos.shares

        self._trade_counter += 1
        trade_id = f"T{self._trade_counter:05d}"

        # 計算持倉天數
        try:
            import pandas as pd
            d_entry = pd.to_datetime(pos.entry_date)
            d_exit = pd.to_datetime(date)
            holding_days = (d_exit - d_entry).days
        except Exception:
            holding_days = 1

        portion = shares / pos.shares
        allocated_cost = pos.cost_basis * portion

        if pos.direction == "LONG":
            fee, tax = self.calc_sell_cost(price, shares)
            gross_val = price * shares
            net_proceeds = gross_val - fee - tax
            realized_pnl = net_proceeds - allocated_cost
            pnl_pct = (realized_pnl / allocated_cost * 100.0) if allocated_cost > 0 else 0.0
            self.cash += net_proceeds

            action_name = "SELL_CLOSE"
            if "TP1" in reason:
                action_name = "SELL_TP1"
                pos.tp1_done = True
            elif "TP2" in reason:
                action_name = "SELL_TP2"
            elif "TRAILING_STOP" in reason:
                action_name = "SELL_TRAILING_STOP"
            elif "STOP" in reason:
                action_name = "SELL_STOP_LOSS"

            rec = TradeRecord(
                trade_id=trade_id,
                symbol=symbol,
                date=date,
                direction="LONG",
                action=action_name,
                shares=shares,
                price=price,
                commission=fee,
                tax=tax,
                net_cash_flow=net_proceeds,
                realized_pnl=round(realized_pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                reason=reason,
                holding_days=holding_days,
            )
        else:
            # 空單回補 (BUY_COVER)
            # 融券賣出時已鎖定 entry_price，回補時付出手續費與證交稅
            buy_fee = self.calc_buy_fee(price, shares)
            sell_fee, sell_tax = self.calc_sell_cost(pos.entry_price, shares)
            # 價差毛利 = (Entry - Exit) * shares
            gross_pnl = (pos.entry_price - price) * shares
            # 淨利 = 價差 - 買進手續費 - 賣出手續費 - 賣出證交稅
            realized_pnl = gross_pnl - buy_fee - sell_fee - sell_tax
            # 返還保證金 + 實現損益
            net_proceeds = allocated_cost + realized_pnl
            self.cash += net_proceeds
            pnl_pct = (realized_pnl / allocated_cost * 100.0) if allocated_cost > 0 else 0.0

            action_name = "BUY_COVER"
            if "TP1" in reason:
                action_name = "COVER_TP1"
                pos.tp1_done = True
            elif "TP2" in reason:
                action_name = "COVER_TP2"
            elif "STOP" in reason:
                action_name = "COVER_STOP_LOSS"

            rec = TradeRecord(
                trade_id=trade_id,
                symbol=symbol,
                date=date,
                direction="SHORT",
                action=action_name,
                shares=shares,
                price=price,
                commission=buy_fee + sell_fee,
                tax=sell_tax,
                net_cash_flow=net_proceeds,
                realized_pnl=round(realized_pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                reason=reason,
                holding_days=holding_days,
            )

        pos.shares -= shares
        pos.cost_basis -= allocated_cost
        pos.current_price = price
        self.trade_history.append(rec)

        if pos.shares <= 0:
            del self.positions[symbol]

        return rec

    def mark_to_market(self, date: str, current_prices: Dict[str, float]) -> float:
        market_val = 0.0
        for sym, pos in self.positions.items():
            if sym in current_prices:
                p = current_prices[sym]
                pos.current_price = p
                pos.highest_price = max(pos.highest_price, p)
                pos.lowest_price = min(pos.lowest_price, p)
            market_val += pos.market_value

        total_equity = self.cash + market_val
        peak_equity = max([e["total_equity"] for e in self.equity_curve] + [self.initial_cash, total_equity])
        drawdown_pct = ((peak_equity - total_equity) / peak_equity * 100.0) if peak_equity > 0 else 0.0

        self.equity_curve.append({
            "date": date,
            "cash": round(self.cash, 2),
            "market_value": round(market_val, 2),
            "total_equity": round(total_equity, 2),
            "drawdown_pct": round(drawdown_pct, 2),
            "open_positions": len(self.positions),
        })
        return total_equity
