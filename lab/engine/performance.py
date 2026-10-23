"""
PerformanceMetrics: 量化績效分析與統計度量引擎 (旗艦升級版)
精算年化 Sharpe Ratio、Sortino、Calmar、Max Drawdown、Alpha/Beta (大盤基準)、勝率、連勝/連敗與月度績效熱圖。
"""

import math
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd

from .portfolio import TradeRecord


class PerformanceEngine:
    @staticmethod
    def calculate_metrics(
        initial_cash: float,
        equity_curve: List[Dict[str, Any]],
        trade_history: List[TradeRecord],
        risk_free_rate: float = 0.015,  # 台灣一年期定存基準約 1.5%
        benchmark_series: Optional[pd.Series] = None,
    ) -> Dict[str, Any]:
        """
        計算標準回測績效矩陣指標 (支援多空已平倉交易與大盤 Benchmark 比較)
        """
        # 1. 交易維度統計 (納入多單平倉 SELL 與空單回補 COVER)
        closed_trades = [
            t for t in trade_history
            if t.action.startswith("SELL") or t.action.startswith("COVER") or "COVER" in t.action
        ]
        total_closed_trades = len(closed_trades)
        wins = [t for t in closed_trades if t.realized_pnl > 0]
        losses = [t for t in closed_trades if t.realized_pnl <= 0]

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_closed_trades * 100.0) if total_closed_trades > 0 else 0.0

        total_gain = sum(t.realized_pnl for t in wins)
        total_loss = abs(sum(t.realized_pnl for t in losses))
        profit_factor = (total_gain / total_loss) if total_loss > 0 else (999.0 if total_gain > 0 else 0.0)

        pnl_pcts = [t.pnl_pct for t in closed_trades]
        avg_trade_pnl_pct = float(np.mean(pnl_pcts)) if pnl_pcts else 0.0
        max_win_pct = max(pnl_pcts) if pnl_pcts else 0.0
        max_loss_pct = min(pnl_pcts) if pnl_pcts else 0.0

        # 平均持倉天數
        holding_days_list = [t.holding_days for t in closed_trades if t.holding_days > 0]
        avg_holding_days = float(np.mean(holding_days_list)) if holding_days_list else 1.0

        # 最大連續獲利與虧損次數
        max_cons_wins, max_cons_losses = 0, 0
        cur_wins, cur_losses = 0, 0
        for t in closed_trades:
            if t.realized_pnl > 0:
                cur_wins += 1
                cur_losses = 0
                max_cons_wins = max(max_cons_wins, cur_wins)
            else:
                cur_losses += 1
                cur_wins = 0
                max_cons_losses = max(max_cons_losses, cur_losses)

        # 2. 權益曲線與時間序列度量
        if not equity_curve:
            return {
                "initial_cash": initial_cash,
                "final_equity": initial_cash,
                "total_return_pct": 0.0,
                "cagr_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "calmar_ratio": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "total_closed_trades": total_closed_trades,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_trade_pnl_pct": 0.0,
                "max_win_pct": 0.0,
                "max_loss_pct": 0.0,
                "avg_holding_days": 0.0,
                "max_cons_wins": 0,
                "max_cons_losses": 0,
                "alpha_pct": 0.0,
                "beta": 1.0,
                "benchmark_return_pct": 0.0,
                "monthly_returns": {},
            }

        df_eq = pd.DataFrame(equity_curve)
        df_eq["date"] = pd.to_datetime(df_eq["date"])
        df_eq.set_index("date", inplace=True)
        final_equity = float(df_eq["total_equity"].iloc[-1])
        total_return_pct = ((final_equity - initial_cash) / initial_cash * 100.0)

        # 計算最大回撤 (MDD)
        cummax = df_eq["total_equity"].cummax()
        drawdowns = (cummax - df_eq["total_equity"]) / cummax * 100.0
        max_drawdown_pct = float(drawdowns.max()) if not drawdowns.empty else 0.0

        # 計算日報酬率
        df_eq["daily_return"] = df_eq["total_equity"].pct_change().fillna(0.0)
        daily_returns = df_eq["daily_return"].values

        # 年化報酬率 (CAGR)
        total_days = (df_eq.index[-1] - df_eq.index[0]).days if len(df_eq) > 1 else 0
        if total_days > 14 and final_equity > 0:
            years = total_days / 365.25
            cagr_pct = ((final_equity / initial_cash) ** (1.0 / years) - 1.0) * 100.0
        else:
            cagr_pct = total_return_pct

        # 卡瑪比率 (Calmar Ratio = CAGR / MDD)
        if max_drawdown_pct > 0.01:
            calmar_ratio = cagr_pct / max_drawdown_pct
        else:
            calmar_ratio = max(0.0, cagr_pct)

        # 年化夏普值 (Sharpe Ratio)
        std_dev = np.std(daily_returns)
        mean_ret = np.mean(daily_returns)
        trading_days = 250
        daily_rf = (1.0 + risk_free_rate) ** (1.0 / trading_days) - 1.0

        if std_dev > 1e-8:
            sharpe_ratio = float((mean_ret - daily_rf) / std_dev * np.sqrt(trading_days))
        else:
            sharpe_ratio = 0.0

        # 年化索提諾值 (Sortino Ratio)
        downside_returns = daily_returns[daily_returns < 0]
        downside_std = np.std(downside_returns) if len(downside_returns) > 0 else 0.0
        if downside_std > 1e-8:
            sortino_ratio = float((mean_ret - daily_rf) / downside_std * np.sqrt(trading_days))
        else:
            sortino_ratio = float(sharpe_ratio) if sharpe_ratio > 0 else 0.0

        # 3. 大盤 Benchmark 對比與 Alpha / Beta 運算
        alpha_pct = 0.0
        beta = 1.0
        benchmark_return_pct = 0.0
        if benchmark_series is not None and not benchmark_series.empty:
            try:
                bm_aligned = benchmark_series.reindex(df_eq.index).ffill().bfill()
                bm_daily_ret = bm_aligned.pct_change().fillna(0.0).values
                bm_var = np.var(bm_daily_ret)
                if bm_var > 1e-8:
                    covariance = np.cov(daily_returns, bm_daily_ret)[0][1]
                    beta = float(covariance / bm_var)
                    # Jensen's Alpha (年化)
                    annual_strat_ret = mean_ret * trading_days
                    annual_bm_ret = np.mean(bm_daily_ret) * trading_days
                    alpha_pct = float((annual_strat_ret - risk_free_rate - beta * (annual_bm_ret - risk_free_rate)) * 100.0)
                if len(bm_aligned) > 1 and bm_aligned.iloc[0] > 0:
                    benchmark_return_pct = float((bm_aligned.iloc[-1] - bm_aligned.iloc[0]) / bm_aligned.iloc[0] * 100.0)
            except Exception:
                pass

        # 月度報酬統計
        monthly_returns = {}
        try:
            monthly_series = df_eq["total_equity"].resample("ME").last().pct_change()
            for ts, val in monthly_series.dropna().items():
                monthly_returns[ts.strftime("%Y-%m")] = round(float(val * 100.0), 2)
        except Exception:
            pass

        return {
            "initial_cash": round(initial_cash, 2),
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return_pct, 2),
            "cagr_pct": round(cagr_pct, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "calmar_ratio": round(calmar_ratio, 2),
            "sharpe_ratio": round(sharpe_ratio, 3),
            "sortino_ratio": round(sortino_ratio, 3),
            "total_closed_trades": total_closed_trades,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_trade_pnl_pct": round(avg_trade_pnl_pct, 2),
            "max_win_pct": round(max_win_pct, 2),
            "max_loss_pct": round(max_loss_pct, 2),
            "avg_holding_days": round(avg_holding_days, 1),
            "max_cons_wins": max_cons_wins,
            "max_cons_losses": max_cons_losses,
            "alpha_pct": round(alpha_pct, 2),
            "beta": round(beta, 2),
            "benchmark_return_pct": round(benchmark_return_pct, 2),
            "monthly_returns": monthly_returns,
        }
