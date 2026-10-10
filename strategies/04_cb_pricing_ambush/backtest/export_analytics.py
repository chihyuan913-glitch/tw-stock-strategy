#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可轉債 (CB) 定價伏擊策略 歷史回測完整數據匯出與圖表統計
產生:
1. detailed_trades_all.csv (完整回測明細清單)
2. top_bottom_trades.csv (勝率王與踩雷股剖析)
3. quarterly_performance.csv (季度季度對照表)
4. backtest_report.md (旗艦量化回測分析報告)
"""

import sys
import os
import json
import pickle
import pandas as pd
import numpy as np

# 設定標準輸出編碼為 UTF-8
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CB_ISSUES_JSON = os.path.join(BASE_DIR, "cb_issues_2025_2026.json")
OHLCV_PKL = os.path.join(BASE_DIR, "ohlcv_cb_stocks.pkl")

with open(CB_ISSUES_JSON, 'r', encoding='utf-8') as f:
    cb_issues = json.load(f)
ohlcv_dict = pd.read_pickle(OHLCV_PKL)

trades = []
for cb in cb_issues:
    code = cb['IssuerCode']
    if code not in ohlcv_dict: continue
    df = ohlcv_dict[code].copy()
    if len(df) < 30: continue
    df.index = pd.to_datetime(df.index)
    
    issue_dt = pd.to_datetime(cb['IssueDate'])
    lockup_dt = pd.to_datetime(cb['Conversion/ExchangePeriodStartDate'])
    avail = df.index[df.index <= issue_dt]
    if len(avail) == 0: continue
    
    # 定價伏擊進場點 (掛牌前 5 營業日)
    issue_idx = df.index.get_loc(avail[-1])
    target_idx = max(0, issue_idx - 5)
    
    entry_p = float(df['Open'].iloc[target_idx])
    entry_dt = df.index[target_idx]
    
    # 閉鎖期解禁日出場點
    lock_avail = df.index[df.index >= lockup_dt]
    exit_p = float(df['Close'].loc[lock_avail[0]]) if len(lock_avail) > 0 else float(df['Close'].iloc[-1])
    exit_dt = lock_avail[0] if len(lock_avail) > 0 else df.index[-1]
    
    # 波動度特徵 (MFE & MAE)
    sub_df = df.loc[entry_dt:exit_dt]
    max_h = float(sub_df['High'].max())
    min_l = float(sub_df['Low'].min())
    mfe = (max_h - entry_p) / entry_p * 100.0
    mae = (min_l - entry_p) / entry_p * 100.0
    
    ret = (exit_p - entry_p) / entry_p * 100.0
    vol20 = float(df['Volume'].iloc[max(0, target_idx-20):target_idx].mean()) / 1000.0
    amt = float(cb.get('IssueAmountNum', 0))
    conv_p = float(cb.get('ConversionPrice', 0))
    
    # 四大價位目標
    tp1 = max(entry_p * 1.075, conv_p * 1.075)
    tp2 = max(entry_p * 1.15, conv_p * 1.15)
    cap_130 = conv_p * 1.30
    sl_5 = entry_p * 0.95
    sl_10 = entry_p * 0.90
    
    hit_130 = (max_h >= cap_130)
    hit_tp1 = (max_h >= tp1)
    hit_tp2 = (max_h >= tp2)
    hit_sl5 = (min_l <= sl_5)
    hit_sl10 = (min_l <= sl_10)
    
    holding_days = len(sub_df)
    year = entry_dt.year
    quarter = f"{year}Q{(entry_dt.month - 1)//3 + 1}"
    
    trades.append({
        '股票代號': code,
        '股票名稱': cb.get('IssuerName', ''),
        'CB簡稱': cb.get('ShortName', ''),
        '發行日期': issue_dt.strftime('%Y-%m-%d'),
        '閉鎖期解禁日': lockup_dt.strftime('%Y-%m-%d'),
        '進場日期': entry_dt.strftime('%Y-%m-%d'),
        '進場價格': round(entry_p, 2),
        '轉換價格': conv_p,
        '出場日期': exit_dt.strftime('%Y-%m-%d'),
        '出場價格': round(exit_p, 2),
        '報酬率%': round(ret, 2),
        '持有交易日': holding_days,
        '最高漲幅MFE%': round(mfe, 2),
        '最大回檔MAE%': round(mae, 2),
        '觸及130%逼贖': '是' if hit_130 else '否',
        '觸及TP1': '是' if hit_tp1 else '否',
        '觸及TP2': '是' if hit_tp2 else '否',
        '跌破5%停損': '是' if hit_sl5 else '否',
        '跌破10%停損': '是' if hit_sl10 else '否',
        '發行規模(億)': round(amt, 1),
        '20日均量(張)': int(vol20),
        '年份': year,
        '季度': quarter
    })

df_all = pd.DataFrame(trades)
df_all.to_csv(os.path.join(BASE_DIR, "detailed_trades_all.csv"), index=False, encoding='utf-8-sig')

# 產出季度統計表
quarterly_rows = []
for q, grp in df_all[df_all['20日均量(張)'] >= 500].groupby('季度'):
    n = len(grp)
    win = (grp['報酬率%'] > 0).sum()
    win_rate = win / n * 100.0
    mean_ret = grp['報酬率%'].mean()
    tot_ret = grp['報酬率%'].sum()
    pos = grp[grp['報酬率%'] > 0]['報酬率%'].sum()
    neg = abs(grp[grp['報酬率%'] < 0]['報酬率%'].sum())
    pf = round(pos / neg, 2) if neg > 0 else 999.0
    max_w = grp['報酬率%'].max()
    max_l = grp['報酬率%'].min()
    quarterly_rows.append({
        '季度': q,
        '交易筆數': n,
        '勝率%': round(win_rate, 1),
        '平均報酬%': round(mean_ret, 2),
        '總報酬%': round(tot_ret, 1),
        '獲利因子': pf,
        '最大獲利%': round(max_w, 1),
        '最大虧損%': round(max_l, 1)
    })
df_q = pd.DataFrame(quarterly_rows)
df_q.to_csv(os.path.join(BASE_DIR, "quarterly_performance.csv"), index=False, encoding='utf-8-sig')

print("已產出 detailed_trades_all.csv 與 quarterly_performance.csv！")
