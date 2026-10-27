"""
PortfolioAllocator & LiveSignalEngine: 多策略資產配置組合、每日訊號矩陣與 K 線重播解說引擎
實作：
1. 每日訊號生成：自選股清單 × 策略池矩陣交叉比對（明確標示：買進、賣出、持有中、被濾網擋下），支援 CSV 匯出
2. 資產配置組合：支援等權重 (equal_weight)、波動倒數 (inverse_volatility)、夏普加權 (sharpe_weighted)，
   強制輸出「策略相關係數矩陣」與「各策略利潤貢獻度」
3. 互動式教學重播：K 線逐根重播時間軸，遇進出場點位自動標記強制暫停並輸出繁體中文解說理由。
"""

import io
import csv
from typing import Dict, List, Any, Optional
import numpy as np
import pandas as pd

from .backtest_engine import BacktestEngine, BacktestResult
from ..strategies.adapters import StrategyRegistry


class PortfolioAllocator:
    @staticmethod
    def allocate(
        market_data: Dict[str, pd.DataFrame],
        strategy_ids: List[str],
        method: str = "equal_weight",  # "equal_weight", "inverse_volatility", "sharpe_weighted"
        total_capital: float = 1_000_000.0,
    ) -> Dict[str, Any]:
        """
        對多策略組合執行資金分配，輸出權重、策略相關係數矩陣與各策略利潤貢獻度。
        """
        valid_ids = [sid for sid in strategy_ids if StrategyRegistry.get(sid) is not None]
        if not valid_ids:
            valid_ids = ["01", "02"]

        n_strats = len(valid_ids)
        strat_results: Dict[str, BacktestResult] = {}
        daily_returns_map: Dict[str, pd.Series] = {}
        vols: Dict[str, float] = {}
        sharpes: Dict[str, float] = {}
        raw_pnls: Dict[str, float] = {}

        for sid in valid_ids:
            strat = StrategyRegistry.get(sid)
            eng = BacktestEngine(initial_cash=total_capital)
            res = eng.run(market_data=market_data, signal_generator=strat.generate_signals)
            strat_results[sid] = res

            if res.equity_curve:
                df_eq = pd.DataFrame(res.equity_curve)
                df_eq["date"] = pd.to_datetime(df_eq["date"])
                df_eq.set_index("date", inplace=True)
                ret_ser = df_eq["total_equity"].pct_change().fillna(0.0)
                daily_returns_map[sid] = ret_ser
                std_val = float(ret_ser.std())
                vols[sid] = max(1e-5, std_val)
            else:
                daily_returns_map[sid] = pd.Series(dtype=float)
                vols[sid] = 0.01

            sharpes[sid] = max(0.05, float(res.metrics.get("sharpe_ratio", 0.0)))
            raw_pnls[sid] = float(res.metrics.get("final_equity", total_capital) - total_capital)

        # 1. 計算權重
        mode = method.lower()
        weights: Dict[str, float] = {}
        if mode == "inverse_volatility":
            inv_vols = {sid: 1.0 / vols[sid] for sid in valid_ids}
            total_inv = sum(inv_vols.values())
            for sid in valid_ids:
                weights[sid] = round(inv_vols[sid] / total_inv, 4)
        elif mode == "sharpe_weighted":
            total_sh = sum(sharpes.values())
            for sid in valid_ids:
                weights[sid] = round(sharpes[sid] / total_sh, 4)
        else:
            for sid in valid_ids:
                weights[sid] = round(1.0 / n_strats, 4)

        # 2. 計算策略相關係數矩陣 (Correlation Matrix)
        df_rets = pd.DataFrame(daily_returns_map).fillna(0.0)
        if not df_rets.empty and len(df_rets) > 1:
            corr_df = df_rets.corr().fillna(0.0).round(3)
            for col in corr_df.columns:
                corr_df.loc[col, col] = 1.0
            correlation_matrix = corr_df.to_dict()
        else:
            correlation_matrix = {
                s1: {s2: (1.0 if s1 == s2 else 0.0) for s2 in valid_ids}
                for s1 in valid_ids
            }

        # 3. 計算各策略利潤貢獻度 (Profit Contribution)
        weighted_pnls = {sid: raw_pnls[sid] * weights[sid] for sid in valid_ids}
        total_abs_pnl = sum(abs(v) for v in weighted_pnls.values())
        total_portfolio_pnl = sum(weighted_pnls.values())

        profit_contribution = []
        for sid in valid_ids:
            strat_obj = StrategyRegistry.get(sid)
            w_pnl = weighted_pnls[sid]
            contrib_pct = (w_pnl / total_abs_pnl * 100.0) if total_abs_pnl > 1e-5 else (100.0 / n_strats)
            profit_contribution.append({
                "strategy_id": sid,
                "strategy_name": strat_obj.name if strat_obj else sid,
                "weight": round(weights[sid] * 100.0, 2),
                "allocated_capital": round(total_capital * weights[sid], 0),
                "strategy_pnl": round(w_pnl, 2),
                "contribution_pct": round(contrib_pct, 2),
                "sharpe_ratio": round(float(strat_results[sid].metrics.get("sharpe_ratio", 0.0)), 2),
            })

        return {
            "method": mode,
            "total_capital": total_capital,
            "portfolio_net_pnl": round(total_portfolio_pnl, 2),
            "portfolio_return_pct": round(total_portfolio_pnl / total_capital * 100.0, 2),
            "weights": weights,
            "correlation_matrix": correlation_matrix,
            "profit_contribution": profit_contribution,
        }


class LiveSignalEngine:
    @staticmethod
    def generate_daily_signal_matrix(
        market_data: Dict[str, pd.DataFrame],
        watchlist: List[str],
        strategy_ids: List[str],
        holding_symbols: Optional[List[str]] = None,
        enable_market_filter: bool = False,
        benchmark_below_ma60: bool = False,
        disposal_symbols: Optional[set] = None,
    ) -> Dict[str, Any]:
        """
        將「自選股清單」與「策略池」進行矩陣交叉比對。
        明確標示：買進、賣出、持有中、被濾網擋下、觀望。
        """
        holdings = set(holding_symbols or [])
        disposals = disposal_symbols or set()
        rows = []

        for sym in watchlist:
            df = market_data.get(sym)
            if df is None or df.empty:
                continue

            last_bar = df.iloc[-1]
            latest_date = (
                df.index[-1].strftime("%Y-%m-%d")
                if isinstance(df.index, pd.DatetimeIndex)
                else str(last_bar.get("date", ""))[:10]
            )
            close_p = float(last_bar["close"])
            vol = int(last_bar["volume"])
            is_suspended = (vol == 0)
            is_disposal = (sym in disposals)

            for sid in strategy_ids:
                strat = StrategyRegistry.get(sid)
                if not strat:
                    continue

                sigs = strat.generate_signals(latest_date, {sym: df})
                has_sig = len(sigs) > 0
                sig_obj = sigs[0] if has_sig else None

                status = "觀望"
                filter_reason = ""
                entry_p, tp1_p, sl_p = 0.0, 0.0, 0.0

                if has_sig and sig_obj:
                    entry_p = sig_obj.entry_price
                    tp1_p = sig_obj.tp1_price
                    sl_p = sig_obj.stop_loss_price

                    if is_suspended:
                        status = "被濾網擋下"
                        filter_reason = "當日成交量為0(暫停交易)"
                    elif is_disposal:
                        status = "被濾網擋下"
                        filter_reason = "處置股過濾攔截"
                    elif enable_market_filter and benchmark_below_ma60:
                        status = "被濾網擋下"
                        filter_reason = "大盤跌破60MA凍結新倉"
                    elif sig_obj.direction == "SHORT" and sym in holdings:
                        status = "賣出"
                        filter_reason = "觸發破線轉空出場"
                    else:
                        status = "買進" if sig_obj.direction == "LONG" else "賣出"
                else:
                    if sym in holdings:
                        ma20 = float(df["close"].rolling(20, min_periods=1).mean().iloc[-1])
                        if close_p < ma20 * 0.96:
                            status = "賣出"
                            filter_reason = "跌破防守線停損出場"
                        else:
                            status = "持有中"

                rows.append({
                    "date": latest_date,
                    "symbol": sym,
                    "strategy_id": sid,
                    "strategy_name": strat.name,
                    "status": status,
                    "close_price": close_p,
                    "volume": vol,
                    "entry_price": entry_p,
                    "tp1_price": tp1_p,
                    "stop_loss_price": sl_p,
                    "note": filter_reason,
                })

        return {
            "count": len(rows),
            "signals": rows,
            "csv_content": LiveSignalEngine.export_signals_to_csv(rows),
        }

    @staticmethod
    def export_signals_to_csv(signal_rows: List[Dict[str, Any]]) -> str:
        """匯出每日訊號矩陣為標準 CSV 字串"""
        output = io.StringIO()
        fieldnames = [
            "date", "symbol", "strategy_id", "strategy_name", "status",
            "close_price", "volume", "entry_price", "tp1_price", "stop_loss_price", "note",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for r in signal_rows:
            writer.writerow(r)
        return output.getvalue()

    @staticmethod
    def build_kline_replay_frames(
        symbol: str,
        df: pd.DataFrame,
        trades: List[Any],
        blocked_signals: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        產生互動式 K 線逐根重播資料序列。
        遇策略進出場點位時強制標記 pause_required=True，並輸出繁體中文解說理由。
        """
        trade_by_date: Dict[str, List[Any]] = {}
        for t in trades:
            d = t.date if hasattr(t, "date") else str(t.get("date", ""))
            trade_by_date.setdefault(d, []).append(t)

        blocked_by_date: Dict[str, List[Dict[str, Any]]] = {}
        for b in (blocked_signals or []):
            d = str(b.get("date", ""))
            blocked_by_date.setdefault(d, []).append(b)

        frames = []
        df_c = df.copy()
        if not isinstance(df_c.index, pd.DatetimeIndex) and "date" in df_c.columns:
            df_c["date"] = pd.to_datetime(df_c["date"])
            df_c.set_index("date", inplace=True)

        for dt_idx, row in df_c.iterrows():
            d_str = dt_idx.strftime("%Y-%m-%d")
            day_trades = trade_by_date.get(d_str, [])
            day_blocks = blocked_by_date.get(d_str, [])

            pause_req = False
            commentary = ""

            if day_trades:
                pause_req = True
                msgs = []
                for t in day_trades:
                    act = t.action if hasattr(t, "action") else t.get("action", "")
                    px = t.price if hasattr(t, "price") else t.get("price", 0.0)
                    sh = t.shares if hasattr(t, "shares") else t.get("shares", 1000)
                    rsn = t.reason if hasattr(t, "reason") else t.get("reason", "")
                    pnl = t.realized_pnl if hasattr(t, "realized_pnl") else t.get("realized_pnl", 0.0)

                    if "ENTRY" in act or act == "SELL_SHORT":
                        msgs.append(f"【建倉進場】{symbol} 於 {d_str} 滿足複合因子進場條件 ({rsn})，以 NT${px:.2f} 成交 {sh} 股。")
                    elif "ADDON" in act:
                        msgs.append(f"【動能加碼】{symbol} 於 {d_str} 突破關鍵防線 ({rsn})，於 NT${px:.2f} 加碼 {sh} 股。")
                    else:
                        msgs.append(f"【風控出場】{symbol} 於 {d_str} 觸發 {act} ({rsn})，於 NT${px:.2f} 平倉 {sh} 股，實現損益 NT${pnl:+,.0f}。")
                commentary = " ".join(msgs)
            elif day_blocks:
                pause_req = True
                b = day_blocks[0]
                commentary = f"【濾網攔截保護】{symbol} 於 {d_str} 雖出現技術訊號，但因「{b.get('reason')}」被風控網成功攔截，免除潛在風險。"

            frames.append({
                "date": d_str,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "pause_required": pause_req,
                "commentary_zh": commentary,
            })

        return frames
