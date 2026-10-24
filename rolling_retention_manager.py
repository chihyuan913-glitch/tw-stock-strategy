#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 全策略 5 日滾動保留與歷史操盤生命週期管理器
================================================================================
核心功能：
1. 【多階段梯隊操盤追蹤 (Multi-Stage Entry & TP Tracking)】：
   - 在個股字卡與報表中精準標示：
     * 第一次進場價位 ｜ 第一次停利價位 (第 1 階段建倉)
     * 第二次進場價位 ｜ 第二次停利價位 (第 2 階段續強加碼)
     * 以此類推 (第 3 階段...)，波段加碼與停利節奏一目了然。
2. 【兩階段剔除與保留 1 天機制 (Pending Removal Lifecycle)】：
   - 當個股觸發停損跌破防線、達標最高停利、逾 5 日週期或技術籌碼不符時：
     * 第一天：進入【⚠️ 待剔除名單 (保留中)】，保留在儀表板與報表 1 天，展示波段結案損益與退場原因。
     * 第二天：正式從儀表板主選股名單除帳，杜絕畫面無預警驟失標的。
3. 【操盤戰績自動歸檔結算庫 (Trade Performance Ledger)】：
   - 標的正式除帳時，自動將該檔個股在這段期間的操作獲利或損失完整寫入
     `trade_history.json` 與 `trade_history.csv`，永久記錄勝率、報酬率與持有天數。
4. 【5 日滾動保留與 Git 歷史回填 (5-Day Rolling & Backfill)】：
   - 保持 5 交易日水位防守，支援一鍵自 Git 還原歷史數據。
================================================================================
"""

import os
import sys
import re
import datetime
import subprocess
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Union

# Windows 命令列編碼保護
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from trade_history_manager import GLOBAL_TRADE_HISTORY, _ensure_float, _calc_pnl

DEFAULT_RETENTION_DAYS = 5


def normalize_date_str(date_val: Any) -> str:
    """將各式日期字串或物件標準化為 YYYY-MM-DD"""
    if date_val is None or pd.isna(date_val):
        return datetime.date.today().strftime("%Y-%m-%d")
    s = str(date_val).strip()
    m = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m2 = re.search(r'(\d{4})(\d{2})(\d{2})', s)
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"
    return datetime.date.today().strftime("%Y-%m-%d")


def extract_code(row: Union[pd.Series, Dict[str, Any]]) -> str:
    """自資料列中提取股票代碼"""
    for col in ["證券代號", "股票代碼", "代號", "代碼", "code", "Code", "證券代碼"]:
        val = row.get(col) if isinstance(row, dict) else (row[col] if col in row else None)
        if val is not None and not pd.isna(val):
            s = str(val).strip()
            if s.endswith(".0"):
                s = s[:-2]
            if s:
                return s
    return ""


def extract_name(row: Union[pd.Series, Dict[str, Any]]) -> str:
    """自資料列中提取股票名稱"""
    for col in ["證券名稱", "股票名稱", "名稱", "name", "Name"]:
        val = row.get(col) if isinstance(row, dict) else (row[col] if col in row else None)
        if val is not None and not pd.isna(val):
            s = str(val).strip()
            if s:
                return s
    return extract_code(row)


def extract_price(row: Union[pd.Series, Dict[str, Any]], field_keys: List[str]) -> float:
    """安全提取指定關鍵字群的第一個有效浮點數價位"""
    for key in field_keys:
        val = row.get(key) if isinstance(row, dict) else (row[key] if key in row else None)
        if val is not None and not pd.isna(val):
            f = _ensure_float(val)
            if f > 0:
                return f
    return 0.0


def get_current_stock_price(row: Union[pd.Series, Dict[str, Any]]) -> float:
    """提取個股最新收盤價或現價"""
    return extract_price(row, ["即時現價", "realtime_price", "收盤價", "最新現價", "現價", "期貨收盤價", "close"])


def get_entry_price(row: Union[pd.Series, Dict[str, Any]]) -> float:
    """提取建議進場價位"""
    return extract_price(row, ["第一次進場價位", "建議進場價", "進場價位", "進場買進上限", "多單進場", "空單進場", "現貨進場"])


def get_tp1_price(row: Union[pd.Series, Dict[str, Any]]) -> float:
    """提取目標停利 TP1"""
    return extract_price(row, ["第一次停利價位", "停利TP1", "目標停利TP1", "現貨TP1", "TP1"])


def get_addon_price(row: Union[pd.Series, Dict[str, Any]]) -> float:
    """提取加碼價位"""
    return extract_price(row, ["第二次進場價位", "動能加碼價", "加碼價位", "續強加碼價", "突破加碼", "加空價位", "現貨加碼"])


def get_tp2_price(row: Union[pd.Series, Dict[str, Any]]) -> float:
    """提取目標停利 TP2"""
    return extract_price(row, ["第二次停利價位", "停利TP2", "目標停利TP2", "現貨TP2", "TP2"])


def get_sl_price(row: Union[pd.Series, Dict[str, Any]]) -> float:
    """提取嚴格停損防守價"""
    return extract_price(row, ["嚴格停損價", "停損價位", "停損防守", "停損回補", "現貨停損", "停損"])


def evaluate_removal_trigger(
    row: Union[pd.Series, Dict[str, Any]],
    today_date: str,
    is_short: bool = False
) -> Tuple[bool, str, float]:
    """
    評估個股是否觸發退場剔除條件：
    1. 跌破嚴格停損價 (風控離場)
    2. 達標目標停利 TP2 (獲利了結)
    3. 明確標記不符條件或技術轉弱
    回傳: (should_remove: bool, reason: str, exit_price: float)
    """
    curr_price = get_current_stock_price(row)
    sl = get_sl_price(row)
    tp2 = get_tp2_price(row)

    # 1. 停損檢核 (最優先風控)
    if sl > 0 and curr_price > 0:
        if is_short:
            if curr_price >= sl:
                return True, f"觸發停損回補 (現價 {curr_price} >= 停損防線 {sl})", curr_price
        else:
            if curr_price <= sl:
                return True, f"跌破嚴格停損 (現價 {curr_price} <= 停損防線 {sl})", curr_price

    # 2. 停利檢核 (達標 TP2)
    if tp2 > 0 and curr_price > 0:
        if is_short:
            if curr_price <= tp2:
                return True, f"達標空單第二停利目標 TP2 ({tp2} 元)", curr_price
        else:
            if curr_price >= tp2:
                return True, f"達標波段第二停利目標 TP2 ({tp2} 元)", curr_price

    return False, "", 0.0


def merge_rolling_records(
    existing_df_or_rows: Union[pd.DataFrame, List[Dict[str, Any]], None],
    new_df_or_rows: Union[pd.DataFrame, List[Dict[str, Any]]],
    today_date_str: Optional[str] = None,
    max_days: int = DEFAULT_RETENTION_DAYS,
    strategy_id: Optional[str] = None
) -> pd.DataFrame:
    """
    將今日新選標的與既有名冊進行深度合併與操盤生命週期追蹤：
    1. 【多階梯歷程】：記錄「第一次進場/停利」、「第二次進場/停利 (加碼)」、「第三次進場/停利」。
    2. 【兩階段剔除】：
       - 若前次已處於 `PENDING_REMOVAL` 且已保留滿 1 天：正式自名單移除，並歸檔至歷史戰績庫。
       - 若今日剛觸發停損/停利/逾期：標記為 `PENDING_REMOVAL`，在名單中保留 1 天並計算波段損益。
    3. 【續強追擊】：歷史標的若今日再次符合策略條件，刷新加碼價位與階段，保持 `ACTIVE`。
    4. 保留最近 `max_days` 交易日內之有效標的。
    """
    today_norm = normalize_date_str(today_date_str)
    is_short = bool(strategy_id and ("03" in strategy_id or "short" in strategy_id.lower()))

    # 1. 轉換 new 為 DataFrame
    if isinstance(new_df_or_rows, pd.DataFrame):
        new_df = new_df_or_rows.copy()
    elif isinstance(new_df_or_rows, list):
        new_df = pd.DataFrame(new_df_or_rows)
    else:
        new_df = pd.DataFrame()

    # 2. 轉換 existing 為 DataFrame
    if isinstance(existing_df_or_rows, pd.DataFrame):
        exist_df = existing_df_or_rows.copy()
    elif isinstance(existing_df_or_rows, list) and existing_df_or_rows:
        exist_df = pd.DataFrame(existing_df_or_rows)
    else:
        exist_df = pd.DataFrame()

    if new_df.empty and exist_df.empty:
        return pd.DataFrame(columns=[
            "證券代號", "證券名稱", "選股日期", "入選天數", "狀態標註",
            "生命週期狀態", "第一次進場價位", "第一次停利價位",
            "第二次進場價位", "第二次停利價位", "操盤階段"
        ])

    # 建立既有標的索引字典 {code: dict}
    exist_dict = {}
    if not exist_df.empty:
        for _, r in exist_df.iterrows():
            c = extract_code(r)
            if c:
                exist_dict[c] = r.to_dict()

    # 今日新選名冊代碼
    new_codes_set = set()
    if not new_df.empty:
        for _, r in new_df.iterrows():
            c = extract_code(r)
            if c:
                new_codes_set.add(c)
        new_df["選股日期"] = today_norm

    # --------------------------------------------------------------------------
    # 步驟一：處理既有名冊標的之生命週期流轉
    # --------------------------------------------------------------------------
    retained_exist_rows = []
    
    # 收集現有名冊中所有出現過的日期，預估保留交易日
    existing_dates = set()
    for r in exist_dict.values():
        d = r.get("選股日期")
        if d:
            existing_dates.add(normalize_date_str(d))
    existing_dates.add(today_norm)
    all_sorted_dates = sorted(list(existing_dates), reverse=True)
    valid_retained_dates = all_sorted_dates[:max_days]

    for c, old_r in exist_dict.items():
        # 若今日已再次被新策略選中，稍後由今日新資料統一處理 (避免覆蓋前遺漏歷程)
        if c in new_codes_set:
            continue

        lifecycle = str(old_r.get("生命週期狀態") or "ACTIVE").strip().upper()
        trigger_date = str(old_r.get("剔除觸發日期") or "").strip()

        # (A) 若前次已被列入待剔除名單 (PENDING_REMOVAL)
        if lifecycle == "PENDING_REMOVAL":
            # 檢查是否已在儀表板保留了 1 天以上 (剔除觸發日期早於今日)
            if trigger_date and trigger_date < today_norm:
                # ➔ 正式除帳！歸檔進歷史操盤戰績記錄庫，自選股名冊中移除！
                old_r["exit_date"] = today_norm
                old_r["strategy_id"] = strategy_id or old_r.get("strategy_id") or ""
                GLOBAL_TRADE_HISTORY.record_closed_trade(old_r)
                print(f"[*] 標的 {c} {extract_name(old_r)} 已保留 1 天，今日正式除帳並寫入操盤戰績庫。")
                continue
            else:
                # 依然是今日剛標記待剔除，繼續保留在名冊中供今日展示
                retained_exist_rows.append(old_r)
                continue

        # (B) 檢核是否已逾 5 個交易日滾動水位期限
        old_pick_date = normalize_date_str(old_r.get("選股日期"))
        if old_pick_date not in valid_retained_dates:
            # ➔ 5 日週期屆滿，正式除帳並歸檔戰績庫！
            curr_p = get_current_stock_price(old_r)
            old_r["exit_date"] = today_norm
            old_r["exit_price"] = curr_p if curr_p > 0 else get_entry_price(old_r)
            old_r["exit_reason"] = f"5 交易日週期屆滿出清 (入選日 {old_pick_date})"
            old_r["strategy_id"] = strategy_id or old_r.get("strategy_id") or ""
            GLOBAL_TRADE_HISTORY.record_closed_trade(old_r)
            continue

        # (C) 若在 5 日內，檢核是否觸發停損/停利等不符條件退場
        should_remove, reason, exit_price = evaluate_removal_trigger(
            old_r, today_date=today_norm, is_short=is_short
        )

        if should_remove:
            # ➔ 轉入待剔除狀態 (PENDING_REMOVAL)，保留 1 天！
            old_r["生命週期狀態"] = "PENDING_REMOVAL"
            old_r["狀態標註"] = "⚠️ 待剔除 (保留中)"
            old_r["剔除觸發日期"] = today_norm
            old_r["剔除原因"] = reason
            old_r["結案退場價位"] = exit_price

            # 損益試算 (依當前操盤階段決定成本計算)
            stage_str = str(old_r.get("操盤階段") or "")
            entry_1 = get_entry_price(old_r)
            entry_2 = get_addon_price(old_r)
            if ("第 2 階段" in stage_str or "第 3 階段" in stage_str or "加碼" in stage_str) and (entry_1 > 0 and entry_2 > 0):
                avg_entry = round((entry_1 + entry_2) / 2.0, 2)
            else:
                avg_entry = entry_1 if entry_1 > 0 else (entry_2 or exit_price)

            if avg_entry > 0 and exit_price > 0:
                pnl_pct = _calc_pnl(avg_entry, exit_price, is_short=is_short)
                old_r["操作損益率%"] = pnl_pct
                old_r["損益狀態"] = "WIN" if pnl_pct > 0.01 else ("LOSS" if pnl_pct < -0.01 else "FLAT")
            
            retained_exist_rows.append(old_r)
        else:
            # 繼續保持 ACTIVE 正常追蹤
            old_r["生命週期狀態"] = "ACTIVE"
            if not old_r.get("狀態標註") or "今日" in str(old_r.get("狀態標註")):
                old_r["狀態標註"] = "⏳ 持續追蹤"
            retained_exist_rows.append(old_r)

    # --------------------------------------------------------------------------
    # 步驟二：處理今日新選標的（初次入選 vs 續強加碼）
    # --------------------------------------------------------------------------
    processed_new_rows = []
    for _, r in new_df.iterrows():
        row_dict = r.to_dict()
        c = extract_code(row_dict)
        name = extract_name(row_dict)

        # 基礎價位提取
        curr_p = get_current_stock_price(row_dict)
        entry_p = extract_price(row_dict, ["建議進場價", "進場價位", "進場買進上限", "多單進場", "空單進場", "現貨進場"])
        if entry_p <= 0:
            entry_p = curr_p
        tp1_p = extract_price(row_dict, ["停利TP1", "目標停利TP1", "現貨TP1", "TP1"])
        addon_p = extract_price(row_dict, ["動能加碼價", "加碼價位", "續強加碼價", "突破加碼", "加空價位", "現貨加碼"])
        tp2_p = extract_price(row_dict, ["停利TP2", "目標停利TP2", "現貨TP2", "TP2"])

        old_record = exist_dict.get(c)

        if old_record:
            # 這是【🔥 續強觸發】標的！繼承歷史進場價位並追加新階梯
            first_entry = _ensure_float(old_record.get("第一次進場價位") or get_entry_price(old_record))
            first_tp = _ensure_float(old_record.get("第一次停利價位") or get_tp1_price(old_record))
            second_entry = _ensure_float(old_record.get("第二次進場價位"))
            second_tp = _ensure_float(old_record.get("第二次停利價位"))
            old_stage = str(old_record.get("操盤階段") or "")

            row_dict["第一次進場價位"] = first_entry if first_entry > 0 else entry_p
            row_dict["第一次停利價位"] = first_tp if first_tp > 0 else tp1_p

            # 若前次為第 1 階段 (初次建倉)，今日續強正式觸發第 2 階段加碼
            if "第 2 階段" not in old_stage and "第 3 階段" not in old_stage:
                row_dict["第二次進場價位"] = entry_p if entry_p > 0 else addon_p
                row_dict["第二次停利價位"] = tp1_p if tp1_p > 0 else tp2_p
                row_dict["操盤階段"] = "第 2 階段 (續強加碼)"
            else:
                row_dict["第二次進場價位"] = second_entry
                row_dict["第二次停利價位"] = second_tp
                row_dict["第三次進場價位"] = addon_p if addon_p > 0 else entry_p
                row_dict["第三次停利價位"] = tp2_p if tp2_p > 0 else (tp1_p * 1.05)
                row_dict["操盤階段"] = "第 3 階段 (二次加碼)"

            row_dict["狀態標註"] = "🔥 續強觸發"
            row_dict["生命週期狀態"] = "ACTIVE"
            row_dict["選股日期"] = today_norm
        else:
            # 這是【🟢 今日新選】全新標的！初始化第 1 階段進場與停利，並展示第 2 階段加碼防線點位
            row_dict["第一次進場價位"] = entry_p
            row_dict["第一次停利價位"] = tp1_p
            row_dict["第二次進場價位"] = addon_p if addon_p > 0 else (entry_p * 1.03 if not is_short else entry_p * 0.97)
            row_dict["第二次停利價位"] = tp2_p if tp2_p > 0 else (tp1_p * 1.05 if tp1_p > 0 else entry_p * 1.1)
            row_dict["操盤階段"] = "第 1 階段 (初次建倉)"
            row_dict["狀態標註"] = "🟢 今日新選"
            row_dict["生命週期狀態"] = "ACTIVE"
            row_dict["選股日期"] = today_norm

        processed_new_rows.append(row_dict)

    # --------------------------------------------------------------------------
    # 步驟三：合流並分配入選天數與排序
    # --------------------------------------------------------------------------
    combined_rows = processed_new_rows + retained_exist_rows
    if not combined_rows:
        return pd.DataFrame()

    combined_df = pd.DataFrame(combined_rows)

    # 水位防守
    all_dates = sorted(list({normalize_date_str(d) for d in combined_df["選股日期"].dropna()}), reverse=True)
    retained_dates = all_dates[:max_days]
    date_to_t_badge = {}
    for idx, d in enumerate(retained_dates):
        if idx == 0 and d == today_norm:
            date_to_t_badge[d] = "T+0"
        else:
            date_to_t_badge[d] = f"T+{idx}"

    combined_df["選股日期"] = combined_df["選股日期"].apply(normalize_date_str)
    
    # 保留有效交易日內，或處於待剔除保留狀態之記錄
    is_in_date = combined_df["選股日期"].isin(retained_dates)
    is_pending_rem = combined_df["生命週期狀態"] == "PENDING_REMOVAL"
    combined_df = combined_df[is_in_date | is_pending_rem].copy()

    def _assign_t_badge(row):
        d = row.get("選股日期", "")
        base_t = date_to_t_badge.get(d, "T+?")
        status = str(row.get("狀態標註", ""))
        lifecycle = str(row.get("生命週期狀態", ""))
        if lifecycle == "PENDING_REMOVAL" or "待剔除" in status:
            return "⚠️ 今日待除"
        elif "續強" in status:
            return f"{base_t} (續強)"
        elif "今日" in status:
            return f"{base_t} (今日)"
        return base_t

    combined_df["入選天數"] = combined_df.apply(_assign_t_badge, axis=1)

    # 欄位順序排列：確保關鍵操盤資訊排在最前面
    core_front_cols = [
        "選股日期", "入選天數", "狀態標註", "生命週期狀態", "操盤階段",
        "第一次進場價位", "第一次停利價位", "第二次進場價位", "第二次停利價位"
    ]
    cols = list(combined_df.columns)
    for c_item in core_front_cols:
        if c_item in cols:
            cols.remove(c_item)

    name_idx = -1
    for idx, c in enumerate(cols):
        if c in ["證券名稱", "股票名稱", "名稱", "name"]:
            name_idx = idx
            break

    if name_idx != -1:
        insert_pos = name_idx + 1
        for offset, c_item in enumerate(core_front_cols):
            cols.insert(insert_pos + offset, c_item)
    else:
        cols = core_front_cols + cols

    combined_df = combined_df[cols]

    # 排序：最新日期在最前，待剔除標的置底或置頂便於查閱，綜合評分高在最前
    sort_cols = ["選股日期"]
    sort_ascending = [False]
    for score_col in ["綜合評分", "評分", "score", "熱度分", "星級推薦"]:
        if score_col in combined_df.columns:
            sort_cols.append(score_col)
            sort_ascending.append(False)
            break

    combined_df = combined_df.sort_values(by=sort_cols, ascending=sort_ascending).reset_index(drop=True)
    return combined_df


def save_rolling_result_csv(
    csv_path: Union[str, Path],
    new_df_or_rows: Union[pd.DataFrame, List[Dict[str, Any]]],
    today_date_str: Optional[str] = None,
    max_days: int = DEFAULT_RETENTION_DAYS,
    strategy_id: Optional[str] = None
) -> pd.DataFrame:
    """
    讀取現有 csv_path 並與新選股數據合併滾動 5 日與生命週期結案管理，再以 UTF-8-BOM 格式寫回。
    """
    csv_file = Path(csv_path)
    if not strategy_id:
        strategy_id = csv_file.parent.name

    exist_df = None
    if csv_file.exists():
        try:
            exist_df = pd.read_csv(csv_file, encoding="utf-8-sig")
        except Exception as e:
            print(f"[!] 讀取既有報表 {csv_path} 失敗，將以全新資料覆寫: {e}")
            exist_df = None

    merged_df = merge_rolling_records(
        existing_df_or_rows=exist_df,
        new_df_or_rows=new_df_or_rows,
        today_date_str=today_date_str,
        max_days=max_days,
        strategy_id=strategy_id
    )

    csv_file.parent.mkdir(parents=True, exist_ok=True)
    merged_df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"[✓] 已成功更新 5 日滾動選股報表至: {csv_file} (保留 {len(merged_df)} 檔，含今日、追蹤中與待剔除標的)")
    return merged_df


def backfill_all_strategies_from_git(max_days: int = 5) -> Dict[str, int]:
    """
    自 Git 提交歷史提取過去 5 個交易日之報表，一鍵還原並建立各策略 5 日滾動名冊。
    """
    strategies_dir = ROOT_DIR / "strategies"
    strat_folders = [
        "01_bollinger_reversal",
        "02_institutional_momentum",
        "03_short_momentum",
        "04_cb_pricing_ambush",
        "05_stock_futures_long",
        "06_volume_surge",
        "07_margin_call_ambush",
        "08_group_window_dressing",
        "09_treasury_stock_buyback",
    ]

    try:
        cmd = ["git", "log", "--pretty=format:%h|%cd|%s", "--date=short", "-n", "30"]
        out = subprocess.check_output(cmd, cwd=str(ROOT_DIR), text=True, encoding="utf-8")
        lines = out.strip().split("\n")
    except Exception as e:
        print(f"[!] 無法取得 Git 歷史: {e}")
        return {}

    commits_by_date = {}
    for line in lines:
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            h, dt, s = parts[0], parts[1], parts[2]
            if "chore(cloud)" in s or "自動更新" in s or "chore:" in s or "feat" in s or "fix" in s:
                if dt not in commits_by_date and len(commits_by_date) < max_days:
                    commits_by_date[dt] = h

    sorted_dates = sorted(commits_by_date.keys(), reverse=True)
    print(f"[*] 偵測到歷史交易日 Commit: {[(d, commits_by_date[d]) for d in sorted_dates]}")

    results_count = {}
    import io

    for strat in strat_folders:
        strat_csv = strategies_dir / strat / "result.csv"
        daily_dfs = []
        for d in reversed(sorted_dates):
            commit_hash = commits_by_date[d]
            try:
                content = subprocess.check_output(
                    ["git", "show", f"{commit_hash}:strategies/{strat}/result.csv"],
                    cwd=str(ROOT_DIR),
                    stderr=subprocess.DEVNULL
                )
                df_day = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
                if not df_day.empty:
                    df_day["選股日期"] = d
                    daily_dfs.append((d, df_day))
            except Exception:
                pass

        if not daily_dfs:
            if strat_csv.exists():
                try:
                    cur_df = pd.read_csv(strat_csv, encoding="utf-8-sig")
                    cur_date = datetime.date.today().strftime("%Y-%m-%d")
                    m = save_rolling_result_csv(strat_csv, cur_df, today_date_str=cur_date, max_days=max_days, strategy_id=strat)
                    results_count[strat] = len(m)
                except Exception:
                    results_count[strat] = 0
            continue

        running_df = None
        for d, df_day in daily_dfs:
            running_df = merge_rolling_records(
                existing_df_or_rows=running_df,
                new_df_or_rows=df_day,
                today_date_str=d,
                max_days=max_days,
                strategy_id=strat
            )

        if running_df is not None and not running_df.empty:
            strat_csv.parent.mkdir(parents=True, exist_ok=True)
            running_df.to_csv(strat_csv, index=False, encoding="utf-8-sig")
            results_count[strat] = len(running_df)
            print(f"[✓] 策略 {strat} 成功回填 5 日滾動數據共 {len(running_df)} 檔！")

    return results_count


if __name__ == "__main__":
    print("=" * 70)
    print("  🚀 台股 5 日滾動保留與操盤生命週期管理器 - 啟動歷史回填與狀態檢驗")
    print("=" * 70)
    res = backfill_all_strategies_from_git(max_days=5)
    print(f"\n[✓] 歷史回填完成成果: {res}")
