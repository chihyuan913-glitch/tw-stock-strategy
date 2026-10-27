"""
VerificationEngine: 台股量化進階統計驗證模組
實作：
1. 前進分析 (Walk-Forward Analysis, WFA)：訓練期最佳化 ➔ 測試期實跑 ➔ 串接樣本外 (OOS) 淨值曲線與資本效率值 (WFE)
2. 蒙地卡羅模擬 (Monte Carlo Simulation, 500~3000次)：
   - 'shuffle' (打亂交易順序)：評估最大回撤 (MDD) 之極端分布狀況
   - 'resample' (Bootstrap 重抽樣)：檢驗最終報酬率與勝率的不確定性分布
3. 參數穩定度高原分析 (Parameter Plateau Heatmap)：
   - 輸出 2D 參數熱力矩陣，使用「★」符號標註周圍參數表現一致的穩定高原區域，排除單一孤島最高分之過度擬合參數。
"""

from typing import Dict, List, Any, Optional, Callable
import numpy as np
import pandas as pd

from .backtest_engine import BacktestEngine, Signal
from .portfolio import TradeRecord


class VerificationEngine:
    @staticmethod
    def run_monte_carlo_simulation(
        trade_history: List[TradeRecord],
        initial_cash: float = 1_000_000.0,
        iterations: int = 1000,
        mode: str = "shuffle",  # "shuffle" (打亂交易順序) 或 "resample" (有放回重抽樣)
        seed: int = 42,
    ) -> Dict[str, Any]:
        """
        執行 500 ~ 3000 次蒙地卡羅模擬分析。
        - mode='shuffle': 打亂交易順序，評估最大回撤 (MDD) 極端分布。
        - mode='resample': 有放回重抽樣，評估最終報酬與勝率的不確定性信賴區間。
        """
        n_iter = max(500, min(3000, int(iterations)))
        rng = np.random.default_rng(seed)

        closed_trades = [
            t for t in trade_history
            if t.action.startswith("SELL") or t.action.startswith("COVER") or "COVER" in t.action
        ]

        if not closed_trades:
            return {
                "iterations": n_iter,
                "mode": mode,
                "trade_count": 0,
                "mdd_distribution": {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "worst": 0.0},
                "return_distribution": {"p5": 0.0, "p50": 0.0, "p95": 0.0, "mean": 0.0},
                "win_rate_distribution": {"p5": 0.0, "p50": 0.0, "p95": 0.0},
                "sample_curves": [],
            }

        pnl_ratios = np.array([t.realized_pnl / initial_cash for t in closed_trades], dtype=float)
        n_trades = len(pnl_ratios)

        mdd_list = []
        final_ret_list = []
        win_rate_list = []
        sample_curves = []

        for i in range(n_iter):
            if mode.lower() == "shuffle":
                # 打亂交易順序 (無放回隨機排列)
                sampled = rng.permutation(pnl_ratios)
            else:
                # 拔靴法重抽樣 (有放回隨機抽取 N 筆)
                indices = rng.integers(0, n_trades, size=n_trades)
                sampled = pnl_ratios[indices]

            # 構建該次模擬的累積權益序列
            eq_path = initial_cash * (1.0 + np.cumsum(sampled))
            eq_full = np.insert(eq_path, 0, initial_cash)
            eq_full = np.maximum(eq_full, 1.0)  # 防止負權益

            # 計算該路徑最大回撤 (MDD %)
            peaks = np.maximum.accumulate(eq_full)
            dds = (peaks - eq_full) / peaks * 100.0
            mdd_list.append(float(np.max(dds)))

            # 計算最終報酬率與勝率
            final_ret = (eq_full[-1] - initial_cash) / initial_cash * 100.0
            final_ret_list.append(float(final_ret))
            win_rate = float(np.sum(sampled > 0) / n_trades * 100.0)
            win_rate_list.append(win_rate)

            if i < 15:
                sample_curves.append([round(float(x), 0) for x in eq_full])

        mdd_arr = np.array(mdd_list)
        ret_arr = np.array(final_ret_list)
        wr_arr = np.array(win_rate_list)

        return {
            "iterations": n_iter,
            "mode": mode.lower(),
            "trade_count": n_trades,
            "mdd_distribution": {
                "p50": round(float(np.percentile(mdd_arr, 50)), 2),
                "p90": round(float(np.percentile(mdd_arr, 90)), 2),
                "p95": round(float(np.percentile(mdd_arr, 95)), 2),
                "p99": round(float(np.percentile(mdd_arr, 99)), 2),
                "worst": round(float(np.max(mdd_arr)), 2),
            },
            "return_distribution": {
                "p5": round(float(np.percentile(ret_arr, 5)), 2),
                "p50": round(float(np.percentile(ret_arr, 50)), 2),
                "p95": round(float(np.percentile(ret_arr, 95)), 2),
                "mean": round(float(np.mean(ret_arr)), 2),
            },
            "win_rate_distribution": {
                "p5": round(float(np.percentile(wr_arr, 5)), 2),
                "p50": round(float(np.percentile(wr_arr, 50)), 2),
                "p95": round(float(np.percentile(wr_arr, 95)), 2),
            },
            "sample_curves": sample_curves,
        }

    @staticmethod
    def run_walk_forward_analysis(
        market_data: Dict[str, pd.DataFrame],
        strategy_factory: Callable[[Dict[str, Any]], Any],
        param_candidates: List[Dict[str, Any]],
        n_splits: int = 3,
        train_ratio: float = 0.7,
        initial_cash: float = 1_000_000.0,
    ) -> Dict[str, Any]:
        """
        執行前進分析 (Walk-Forward Analysis, WFA)：
        將歷史資料劃分為訓練期 (In-Sample) 與測試期 (Out-of-Sample)，
        提取訓練期最佳參數於測試期實跑，串接測試期績效生成樣本外淨值曲線，並計算資本效率值 (WFE)。
        """
        # 收集所有交易日
        all_dates = set()
        for sym, df in market_data.items():
            idx = pd.to_datetime(df.index if isinstance(df.index, pd.DatetimeIndex) else df["date"])
            for d in idx.strftime("%Y-%m-%d"):
                all_dates.add(d)
        sorted_dates = sorted(list(all_dates))

        if len(sorted_dates) < 30 or not param_candidates:
            return {
                "status": "insufficient_data",
                "wfe_pct": 0.0,
                "windows": [],
                "oos_equity_curve": [],
            }

        total_len = len(sorted_dates)
        window_size = max(20, total_len // max(1, n_splits))
        windows_report = []
        oos_combined_curve: List[Dict[str, Any]] = []
        running_cash = initial_cash

        is_returns = []
        oos_returns = []

        for split_idx in range(n_splits):
            start_i = split_idx * (total_len - window_size) // max(1, n_splits - 1) if n_splits > 1 else 0
            end_i = min(total_len, start_i + window_size)
            seg_dates = sorted_dates[start_i:end_i]
            if len(seg_dates) < 15:
                continue

            split_point = max(10, int(len(seg_dates) * train_ratio))
            train_start, train_end = seg_dates[0], seg_dates[split_point - 1]
            test_start, test_end = seg_dates[split_point], seg_dates[-1]

            # 1. 訓練期掃描最佳參數
            best_params = param_candidates[0]
            best_score = -999999.0
            best_is_ret = 0.0

            for cand in param_candidates:
                strat = strategy_factory(cand)
                eng = BacktestEngine(initial_cash=initial_cash)
                res_is = eng.run(
                    market_data=market_data,
                    signal_generator=strat.generate_signals,
                    start_date=train_start,
                    end_date=train_end,
                )
                ret_is = res_is.metrics.get("total_return_pct", 0.0)
                sharpe_is = res_is.metrics.get("sharpe_ratio", 0.0)
                score = ret_is + sharpe_is * 2.0
                if score > best_score:
                    best_score = score
                    best_params = cand
                    best_is_ret = ret_is

            # 2. 測試期 (Out-of-Sample) 實跑
            oos_strat = strategy_factory(best_params)
            oos_eng = BacktestEngine(initial_cash=running_cash)
            res_oos = oos_eng.run(
                market_data=market_data,
                signal_generator=oos_strat.generate_signals,
                start_date=test_start,
                end_date=test_end,
            )
            ret_oos = res_oos.metrics.get("total_return_pct", 0.0)
            if res_oos.equity_curve:
                running_cash = float(res_oos.equity_curve[-1]["total_equity"])
                for pt in res_oos.equity_curve:
                    oos_combined_curve.append({
                        "date": pt["date"],
                        "total_equity": pt["total_equity"],
                        "window": split_idx + 1,
                    })

            is_returns.append(best_is_ret / max(1, split_point))
            oos_returns.append(ret_oos / max(1, len(seg_dates) - split_point))

            windows_report.append({
                "window": split_idx + 1,
                "train_period": f"{train_start} ~ {train_end}",
                "test_period": f"{test_start} ~ {test_end}",
                "best_params": best_params,
                "in_sample_return_pct": round(best_is_ret, 2),
                "out_of_sample_return_pct": round(ret_oos, 2),
            })

        # 計算資本效率值 WFE (Walk-Forward Efficiency: OOS 日均報酬 / IS 日均報酬)
        mean_is = float(np.mean(is_returns)) if is_returns else 0.0
        mean_oos = float(np.mean(oos_returns)) if oos_returns else 0.0
        if abs(mean_is) > 1e-6:
            wfe_pct = round((mean_oos / abs(mean_is)) * 100.0, 2)
        else:
            wfe_pct = 100.0 if mean_oos >= 0 else 0.0

        total_oos_return_pct = round((running_cash - initial_cash) / initial_cash * 100.0, 2)

        return {
            "status": "success",
            "n_splits": len(windows_report),
            "initial_cash": initial_cash,
            "final_oos_equity": round(running_cash, 2),
            "total_oos_return_pct": total_oos_return_pct,
            "wfe_pct": wfe_pct,
            "windows": windows_report,
            "oos_equity_curve": oos_combined_curve,
        }

    @staticmethod
    def run_parameter_plateau_scan(
        market_data: Dict[str, pd.DataFrame],
        strategy_factory: Callable[[Dict[str, Any]], Any],
        param_x_name: str,
        param_x_values: List[Any],
        param_y_name: str,
        param_y_values: List[Any],
        initial_cash: float = 1_000_000.0,
    ) -> Dict[str, Any]:
        """
        執行 2D 參數穩定度高原分析：
        輸出參數高原熱力圖矩陣，使用「★」符號標註周圍參數表現一致的穩定區域，
        自動識別並排除單一孤島最高分的過度擬合 (Overfitting) 參數。
        """
        nx = len(param_x_values)
        ny = len(param_y_values)
        score_grid = np.zeros((ny, nx), dtype=float)

        for iy, val_y in enumerate(param_y_values):
            for ix, val_x in enumerate(param_x_values):
                params = {param_x_name: val_x, param_y_name: val_y}
                strat = strategy_factory(params)
                eng = BacktestEngine(initial_cash=initial_cash)
                res = eng.run(market_data=market_data, signal_generator=strat.generate_signals)
                score_grid[iy, ix] = float(res.metrics.get("total_return_pct", 0.0))

        # 計算每個網格與周圍 3x3 鄰域的一致性與高原特徵
        global_median = float(np.median(score_grid))
        cells = []
        plateau_stars = []

        for iy in range(ny):
            row_cells = []
            for ix in range(nx):
                val = score_grid[iy, ix]
                # 擷取周圍 3x3 鄰居 (不含自身或含自身)
                y0, y1 = max(0, iy - 1), min(ny, iy + 2)
                x0, x1 = max(0, ix - 1), min(nx, ix + 2)
                neighborhood = score_grid[y0:y1, x0:x1].flatten()
                neighbors_only = np.delete(neighborhood, np.argmax(neighborhood == val)) if len(neighborhood) > 1 else neighborhood

                neigh_mean = float(np.mean(neighbors_only))
                neigh_std = float(np.std(neighbors_only))

                # 判定是否為孤島過擬合峰值：自身很高，但周圍鄰居均值顯著低落
                is_isolated_peak = bool(val > global_median and (val - neigh_mean) > max(5.0, abs(neigh_mean) * 0.8))

                # 判定是否為「★」穩定參數高原：自身與鄰居均值皆高於或等於中位數，且鄰域變異度低 (表現一致)
                is_plateau = bool(
                    not is_isolated_peak
                    and val >= global_median
                    and neigh_mean >= global_median
                    and neigh_std <= max(3.5, abs(neigh_mean) * 0.5 + 1.0)
                )

                label = f"★ {val:.2f}%" if is_plateau else f"{val:.2f}%"
                cell_info = {
                    "x_param": param_x_values[ix],
                    "y_param": param_y_values[iy],
                    "return_pct": round(val, 2),
                    "neighbor_mean": round(neigh_mean, 2),
                    "neighbor_std": round(neigh_std, 2),
                    "is_plateau_star": is_plateau,
                    "is_isolated_overfit": is_isolated_peak,
                    "label": label,
                }
                row_cells.append(cell_info)
                if is_plateau:
                    plateau_stars.append(cell_info)
            cells.append(row_cells)

        return {
            "param_x_name": param_x_name,
            "param_x_values": param_x_values,
            "param_y_name": param_y_name,
            "param_y_values": param_y_values,
            "grid": cells,
            "plateau_stars_count": len(plateau_stars),
            "recommended_plateau": plateau_stars[0] if plateau_stars else None,
        }
