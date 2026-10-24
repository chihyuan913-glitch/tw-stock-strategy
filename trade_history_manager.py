#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 操盤戰績與結案歷史管理模組 (Trade History & Performance Ledger)
================================================================================
核心職責：
1. 記錄所有自儀表板除帳之個股操盤戰績 (Closed Trades Ledger)。
2. 完整保留「第 1 次進場/停利、第 2 次進場/停利、...」多階段梯隊歷程。
3. 精算每檔個股操作損益率 (%)、損益金額、持有天數與退場原因。
4. 雙軌持久化儲存至 JSON (完整巢狀結構) 與 CSV (UTF-8-BOM Excel 易讀格式)。
5. 提供全專案與各策略獨立之勝率、盈虧比、平均報酬率統計指標。
================================================================================
"""

import os
import sys
import json
import threading
import datetime
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any, Optional

ROOT_DIR = Path(__file__).resolve().parent
HISTORY_JSON_PATH = ROOT_DIR / "trade_history.json"
HISTORY_CSV_PATH = ROOT_DIR / "trade_history.csv"

_LOCK = threading.RLock()


def _ensure_float(val: Any, default: float = 0.0) -> float:
    """安全轉換浮點數"""
    if val is None or pd.isna(val):
        return default
    try:
        s = str(val).replace(',', '').replace('%', '').strip()
        return float(s)
    except Exception:
        return default


def _calc_pnl(entry_price: float, exit_price: float, is_short: bool = False) -> float:
    """計算損益百分比 (%)"""
    if entry_price <= 0 or exit_price <= 0:
        return 0.0
    if is_short:
        return round(((entry_price - exit_price) / entry_price) * 100.0, 2)
    else:
        return round(((exit_price - entry_price) / entry_price) * 100.0, 2)


class TradeHistoryManager:
    """操盤戰績記錄庫管理器 (單例/全域共用)"""

    def __init__(self, json_path: Path = HISTORY_JSON_PATH, csv_path: Path = HISTORY_CSV_PATH):
        self.json_path = Path(json_path)
        self.csv_path = Path(csv_path)

    def load_trades(self) -> List[Dict[str, Any]]:
        """讀取歷史所有已結案操盤記錄"""
        with _LOCK:
            if not self.json_path.exists():
                return []
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
                    return []
            except Exception as e:
                print(f"[!] 讀取歷史操盤資料失敗: {e}")
                return []

    def record_closed_trade(self, trade_data: Dict[str, Any]) -> bool:
        """
        記錄一筆已除帳之結案操盤記錄。
        自動進行查重 (相同 code + strategy_id + exit_date 則略過或刷新)。
        """
        with _LOCK:
            records = self.load_trades()
            code = str(trade_data.get("code") or trade_data.get("證券代號") or "").strip()
            strat_id = str(trade_data.get("strategy_id") or "").strip()
            exit_date = str(trade_data.get("exit_date") or datetime.date.today().strftime("%Y-%m-%d")).strip()

            if not code:
                return False

            # 補齊各項結構化標準欄位
            name = str(trade_data.get("name") or trade_data.get("證券名稱") or code).strip()
            strat_name = str(trade_data.get("strategy_name") or strat_id).strip()

            entry_1 = _ensure_float(trade_data.get("first_entry_price") or trade_data.get("第一次進場價位") or trade_data.get("進場價位"))
            tp_1 = _ensure_float(trade_data.get("first_tp_price") or trade_data.get("第一次停利價位") or trade_data.get("停利TP1"))
            entry_2 = _ensure_float(trade_data.get("second_entry_price") or trade_data.get("第二次進場價位"))
            tp_2 = _ensure_float(trade_data.get("second_tp_price") or trade_data.get("第二次停利價位"))
            entry_3 = _ensure_float(trade_data.get("third_entry_price") or trade_data.get("第三次進場價位"))
            tp_3 = _ensure_float(trade_data.get("third_tp_price") or trade_data.get("第三次停利價位"))

            exit_price = _ensure_float(trade_data.get("exit_price") or trade_data.get("結案退場價位") or trade_data.get("收盤價"))
            entry_date = str(trade_data.get("first_entry_date") or trade_data.get("選股日期") or exit_date).strip()
            exit_reason = str(trade_data.get("exit_reason") or trade_data.get("剔除原因") or "波段結案除帳").strip()

            # 計算持有天數
            try:
                d1 = datetime.datetime.strptime(entry_date, "%Y-%m-%d").date()
                d2 = datetime.datetime.strptime(exit_date, "%Y-%m-%d").date()
                holding_days = max(1, (d2 - d1).days + 1)
            except Exception:
                holding_days = int(trade_data.get("holding_days") or 1)

            # 判定做空策略 (如策略 03)
            is_short = ("03" in strat_id or "short" in strat_id.lower() or "做空" in strat_name)

            # 加權平均進場成本估算 (僅在真正觸發第 2 階段加碼時才合併計算成本)
            stage_info = str(trade_data.get("操盤階段") or "")
            has_addon_triggered = ("第 2 階段" in stage_info or "第 3 階段" in stage_info or "加碼" in stage_info or "續強" in stage_info)
            if entry_2 > 0 and has_addon_triggered:
                avg_entry = round((entry_1 + entry_2) / 2.0, 2)
            else:
                avg_entry = entry_1 if entry_1 > 0 else exit_price

            return_pct = _ensure_float(trade_data.get("return_pct"))
            if return_pct == 0.0 and avg_entry > 0 and exit_price > 0:
                return_pct = _calc_pnl(avg_entry, exit_price, is_short=is_short)

            # 損益金額 (以 1 張 = 1000 股估算)
            if is_short:
                diff_per_share = avg_entry - exit_price
            else:
                diff_per_share = exit_price - avg_entry
            return_amount = round(diff_per_share * 1000.0, 0)

            # 損益狀態
            if return_pct > 0.01:
                pnl_status = "WIN"
            elif return_pct < -0.01:
                pnl_status = "LOSS"
            else:
                pnl_status = "FLAT"

            stages_history = trade_data.get("stages_history")
            if not isinstance(stages_history, list) or not stages_history:
                stages_history = [
                    {"stage": 1, "date": entry_date, "entry": entry_1, "tp": tp_1}
                ]
                if entry_2 > 0:
                    stages_history.append({"stage": 2, "date": exit_date, "entry": entry_2, "tp": tp_2})
                if entry_3 > 0:
                    stages_history.append({"stage": 3, "date": exit_date, "entry": entry_3, "tp": tp_3})

            record_id = f"{strat_id}_{code}_{exit_date}"
            new_record = {
                "trade_id": record_id,
                "code": code,
                "name": name,
                "strategy_id": strat_id,
                "strategy_name": strat_name,
                "first_entry_date": entry_date,
                "first_entry_price": entry_1,
                "first_tp_price": tp_1,
                "second_entry_price": entry_2 if entry_2 > 0 else None,
                "second_tp_price": tp_2 if tp_2 > 0 else None,
                "third_entry_price": entry_3 if entry_3 > 0 else None,
                "third_tp_price": tp_3 if tp_3 > 0 else None,
                "stages_history": stages_history,
                "exit_date": exit_date,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "holding_days": holding_days,
                "avg_entry_price": avg_entry,
                "return_pct": return_pct,
                "return_amount": return_amount,
                "pnl_status": pnl_status,
                "archived_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            # 查重防重複添加
            existing_idx = -1
            for idx, r in enumerate(records):
                if r.get("trade_id") == record_id:
                    existing_idx = idx
                    break

            if existing_idx >= 0:
                records[existing_idx] = new_record
            else:
                records.insert(0, new_record)  # 最新在最前

            # 寫入 JSON
            try:
                self.json_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.json_path, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[!] 儲存操盤 JSON 歷史失敗: {e}")

            # 同步更新 CSV (扁平化便於 Excel 閱讀)
            self._sync_csv(records)
            return True

    def _sync_csv(self, records: List[Dict[str, Any]]) -> None:
        """將操盤記錄同步匯出為 UTF-8-BOM CSV 報表"""
        if not records:
            return
        flat_rows = []
        for r in records:
            stages = r.get("stages_history") or []
            stage_desc = " ➔ ".join([f"第{s.get('stage')}次進場 {s.get('entry')}元/停利 {s.get('tp')}元" for s in stages])
            flat_rows.append({
                "結案日期": r.get("exit_date"),
                "股票代碼": r.get("code"),
                "股票名稱": r.get("name"),
                "所屬策略": r.get("strategy_name") or r.get("strategy_id"),
                "首次進場日": r.get("first_entry_date"),
                "第一次進場價": r.get("first_entry_price"),
                "第一次停利價": r.get("first_tp_price"),
                "第二次進場價": r.get("second_entry_price") or "--",
                "第二次停利價": r.get("second_tp_price") or "--",
                "梯隊操盤歷程": stage_desc,
                "平均進場成本": r.get("avg_entry_price"),
                "結案退場價": r.get("exit_price"),
                "退場原因": r.get("exit_reason"),
                "持有天數": r.get("holding_days"),
                "操作損益率(%)": f"{r.get('return_pct', 0.0):+.2f}%",
                "預估損益額(元)": f"{int(r.get('return_amount', 0)):+,}元",
                "勝負狀態": "🟢 獲利了結" if r.get("pnl_status") == "WIN" else ("🔴 停損離場" if r.get("pnl_status") == "LOSS" else "⚪ 平手"),
                "歸檔時間": r.get("archived_at")
            })

        df = pd.DataFrame(flat_rows)
        try:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(self.csv_path, index=False, encoding="utf-8-sig")
        except Exception as e:
            print(f"[!] 匯出歷史操盤 CSV 失敗: {e}")

    def get_closed_trades(self, strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """取得符合條件的已結案操盤記錄"""
        records = self.load_trades()
        if not strategy_id:
            return records
        sid = strategy_id.strip().lower()
        return [r for r in records if sid in str(r.get("strategy_id", "")).lower() or sid in str(r.get("strategy_name", "")).lower()]

    def get_performance_summary(self, strategy_id: Optional[str] = None) -> Dict[str, Any]:
        """計算操盤戰績總覽與 KPI 指標"""
        trades = self.get_closed_trades(strategy_id)
        total_trades = len(trades)
        if total_trades == 0:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "flat_trades": 0,
                "win_rate_pct": 0.0,
                "avg_return_pct": 0.0,
                "total_return_pct": 0.0,
                "max_win_pct": 0.0,
                "max_loss_pct": 0.0,
                "avg_holding_days": 0.0,
                "total_pnl_amount": 0.0,
                "trades": []
            }

        wins = [t for t in trades if t.get("pnl_status") == "WIN"]
        losses = [t for t in trades if t.get("pnl_status") == "LOSS"]
        flats = [t for t in trades if t.get("pnl_status") == "FLAT"]

        win_count = len(wins)
        loss_count = len(losses)
        flat_count = len(flats)

        win_rate = round((win_count / total_trades) * 100.0, 1)

        returns = [_ensure_float(t.get("return_pct")) for t in trades]
        avg_ret = round(sum(returns) / total_trades, 2)
        total_ret = round(sum(returns), 2)
        max_win = round(max(returns), 2) if returns else 0.0
        max_loss = round(min(returns), 2) if returns else 0.0

        days_list = [int(t.get("holding_days", 1)) for t in trades]
        avg_days = round(sum(days_list) / total_trades, 1)

        amounts = [_ensure_float(t.get("return_amount")) for t in trades]
        total_amount = round(sum(amounts), 0)

        return {
            "total_trades": total_trades,
            "winning_trades": win_count,
            "losing_trades": loss_count,
            "flat_trades": flat_count,
            "win_rate_pct": win_rate,
            "avg_return_pct": avg_ret,
            "total_return_pct": total_ret,
            "max_win_pct": max_win,
            "max_loss_pct": max_loss,
            "avg_holding_days": avg_days,
            "total_pnl_amount": total_amount,
            "trades": trades[:50]  # 回傳前 50 筆供展示
        }


# 全域單例
GLOBAL_TRADE_HISTORY = TradeHistoryManager()
