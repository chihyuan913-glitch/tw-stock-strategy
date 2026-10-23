#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 全策略 5 日滾動保留與歷史追蹤管理器
================================================================================
核心功能：
1. 【5 日滾動保留機制 (5-Day Rolling Retention)】：
   - 盤後選股時，自動讀取既有 `result.csv`，將今日新選標的與過去 5 個交易日內之標的進行智慧合併。
   - 杜絕盤後更新後「只留當天、過往標的消失」的痛點，建立完整波段追蹤池。
2. 【智慧重複觸發標註 (Deduplication & Re-trigger)】：
   - 若歷史標的在今日再次觸發，以最新價格、四大防線與指標刷新，並標註「🔥 續強觸發」。
   - 若為今日首次入選，標註「🟢 今日新選」；過往仍在 5 日內的標的標註「⏳ 持續追蹤」。
3. 【嚴格 5 交易日水位防守 (5 Trading Days Pruning)】：
   - 自動識別選股日期序列，精準保留最近 5 個交易日的標的，逾期標的自動歸檔除帳。
4. 【歷史回填引擎 (Git Backfill)】：
   - 支援自 Git 歷史提交自動還原近 5 日選股報表，實現一鍵歷史補齊。
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
DEFAULT_RETENTION_DAYS = 5


def normalize_date_str(date_val: Any) -> str:
    """將各式日期字串或物件標準化為 YYYY-MM-DD"""
    if date_val is None or pd.isna(date_val):
        return datetime.date.today().strftime("%Y-%m-%d")
    s = str(date_val).strip()
    # Match YYYY-MM-DD or YYYY/MM/DD
    m = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # Match YYYYMMDD
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
            # 處理可能被解析為 float 的代號 (如 2330.0)
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


def merge_rolling_records(
    existing_df_or_rows: Union[pd.DataFrame, List[Dict[str, Any]], None],
    new_df_or_rows: Union[pd.DataFrame, List[Dict[str, Any]]],
    today_date_str: Optional[str] = None,
    max_days: int = DEFAULT_RETENTION_DAYS
) -> pd.DataFrame:
    """
    將今日新選標的與既有 5 日內的標的進行合併與滾動更新：
    1. 補齊 `選股日期`、`入選天數`、`狀態標註`。
    2. 若今日新標的已在既有名冊中，以今日數據覆蓋並標記 `🔥 續強觸發`。
    3. 保留最近 `max_days` 個獨立交易日。
    4. 依選股日期（最新在前）與評分降序排列。
    """
    today_norm = normalize_date_str(today_date_str)

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

    # 若兩者皆為空，直接回傳空 DataFrame
    if new_df.empty and exist_df.empty:
        return pd.DataFrame(columns=["證券代號", "證券名稱", "選股日期", "入選天數", "狀態標註"])

    # 提取今日新選代碼集合
    new_codes_set = set()
    if not new_df.empty:
        for _, r in new_df.iterrows():
            c = extract_code(r)
            if c:
                new_codes_set.add(c)
        # 設定今日日期
        new_df["選股日期"] = today_norm

    # 處理既有標的
    clean_exist_rows = []
    if not exist_df.empty:
        for _, r in exist_df.iterrows():
            c = extract_code(r)
            if not c:
                continue
            # 若今日已經再次觸發，以今日新數據為主，略過舊筆數
            if c in new_codes_set:
                continue
            row_dict = r.to_dict()
            # 確保有選股日期，若無則預設為今日前一日
            row_date = row_dict.get("選股日期")
            row_dict["選股日期"] = normalize_date_str(row_date) if row_date else today_norm
            clean_exist_rows.append(row_dict)

    # 今日標的轉為 dict list
    new_rows = []
    if not new_df.empty:
        # 檢查是否有曾出現於 exist_df 中的續強標的
        old_codes_in_exist = set()
        if not exist_df.empty:
            for _, r in exist_df.iterrows():
                oc = extract_code(r)
                if oc:
                    old_codes_in_exist.add(oc)

        for _, r in new_df.iterrows():
            row_dict = r.to_dict()
            c = extract_code(row_dict)
            if c in old_codes_in_exist:
                row_dict["狀態標註"] = "🔥 續強觸發"
            else:
                row_dict["狀態標註"] = "🟢 今日新選"
            row_dict["選股日期"] = today_norm
            new_rows.append(row_dict)

    # 標記既有舊標的的狀態
    for r in clean_exist_rows:
        if "狀態標註" not in r or not r["狀態標註"] or r["狀態標註"] == "🟢 今日新選":
            r["狀態標註"] = "⏳ 持續追蹤"

    # 合併全部資料
    combined_rows = new_rows + clean_exist_rows
    if not combined_rows:
        return pd.DataFrame()

    combined_df = pd.DataFrame(combined_rows)

    # 3. 水位防守：找出所有不重複的選股日期，由新到舊排序
    all_dates = sorted(list({normalize_date_str(d) for d in combined_df["選股日期"].dropna()}), reverse=True)
    retained_dates = all_dates[:max_days]
    date_to_t_badge = {}
    for idx, d in enumerate(retained_dates):
        if idx == 0 and d == today_norm:
            date_to_t_badge[d] = "T+0"
        else:
            date_to_t_badge[d] = f"T+{idx}"

    # 過濾只保留在 retained_dates 內的記錄
    combined_df["選股日期"] = combined_df["選股日期"].apply(normalize_date_str)
    combined_df = combined_df[combined_df["選股日期"].isin(retained_dates)].copy()

    # 指派入選天數
    def _assign_t_badge(row):
        d = row.get("選股日期", "")
        base_t = date_to_t_badge.get(d, "T+?")
        status = row.get("狀態標註", "")
        if "續強" in status:
            return f"{base_t} (續強)"
        elif "今日" in status:
            return f"{base_t} (今日)"
        return base_t

    combined_df["入選天數"] = combined_df.apply(_assign_t_badge, axis=1)

    # 4. 欄位順序優化：將「選股日期」、「入選天數」、「狀態標註」置於「證券名稱」之後
    cols = list(combined_df.columns)
    for special_col in ["選股日期", "入選天數", "狀態標註"]:
        if special_col in cols:
            cols.remove(special_col)

    name_idx = -1
    for idx, c in enumerate(cols):
        if c in ["證券名稱", "股票名稱", "名稱", "name"]:
            name_idx = idx
            break

    if name_idx != -1:
        insert_pos = name_idx + 1
        cols.insert(insert_pos, "選股日期")
        cols.insert(insert_pos + 1, "入選天數")
        cols.insert(insert_pos + 2, "狀態標註")
    else:
        cols = ["選股日期", "入選天數", "狀態標註"] + cols

    combined_df = combined_df[cols]

    # 5. 排序：優先依「選股日期」由新至舊，同日期依評分/星級降序排列
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
    max_days: int = DEFAULT_RETENTION_DAYS
) -> pd.DataFrame:
    """
    讀取現有 csv_path 並與新選股數據合併滾動 5 日，再以 UTF-8-BOM 格式寫回。
    """
    csv_file = Path(csv_path)
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
        max_days=max_days
    )

    csv_file.parent.mkdir(parents=True, exist_ok=True)
    merged_df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"[✓] 已成功更新 5 日滾動選股報表至: {csv_file} (保留 {len(merged_df)} 檔，含今日與近 5 日追蹤標的)")
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

    # 取得最近包含 "chore(cloud): 自動更新全策略盤後選股報表" 的 commits
    try:
        cmd = ["git", "log", "--pretty=format:%h|%cd|%s", "--date=short", "-n", "30"]
        out = subprocess.check_output(cmd, cwd=str(ROOT_DIR), text=True, encoding="utf-8")
        lines = out.strip().split("\n")
    except Exception as e:
        print(f"[!] 無法取得 Git 歷史: {e}")
        return {}

    # 找出最近 5 個不同日期的 commit (優先選取 cloud daily screener)
    commits_by_date = {}
    for line in lines:
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            h, dt, s = parts[0], parts[1], parts[2]
            if "chore(cloud)" in s or "自動更新" in s or "chore:" in s or "feat" in s:
                if dt not in commits_by_date and len(commits_by_date) < max_days:
                    commits_by_date[dt] = h

    sorted_dates = sorted(commits_by_date.keys(), reverse=True)
    print(f"[*] 偵測到歷史交易日 Commit: {[(d, commits_by_date[d]) for d in sorted_dates]}")

    results_count = {}
    import io

    for strat in strat_folders:
        strat_csv = strategies_dir / strat / "result.csv"
        daily_dfs = []
        for d in reversed(sorted_dates):  # 由舊到新逐日疊加
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
                    m = save_rolling_result_csv(strat_csv, cur_df, today_date_str=cur_date, max_days=max_days)
                    results_count[strat] = len(m)
                except Exception:
                    results_count[strat] = 0
            continue

        # 由最舊的一天開始逐步向最新的一天 rolling merge
        running_df = None
        for d, df_day in daily_dfs:
            running_df = merge_rolling_records(
                existing_df_or_rows=running_df,
                new_df_or_rows=df_day,
                today_date_str=d,
                max_days=max_days
            )

        if running_df is not None and not running_df.empty:
            strat_csv.parent.mkdir(parents=True, exist_ok=True)
            running_df.to_csv(strat_csv, index=False, encoding="utf-8-sig")
            results_count[strat] = len(running_df)
            print(f"[✓] 策略 {strat} 成功回填 5 日滾動數據共 {len(running_df)} 檔！")

    return results_count


if __name__ == "__main__":
    print("=" * 70)
    print("  🚀 台股 5 日滾動保留管理器 - 啟動歷史回填與狀態檢驗")
    print("=" * 70)
    res = backfill_all_strategies_from_git(max_days=5)
    print(f"\n[✓] 歷史回填完成成果: {res}")
