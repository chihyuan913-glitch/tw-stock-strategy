#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可轉債 (CB) 定價伏擊策略 多重風控與出場機制 深度參數回測矩陣
"""

import sys
import os
import json
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

def simulate_trade_strategy(df, entry_idx, lockup_dt, conv_price, 
                            sl_pct=0.08, tp_mode='trailing', trail_active=0.10, trail_pullback=0.08,
                            enable_130_cap=True):
    """
    tp_mode:
      - 'fixed_tp': TP1(+7.5%)出半, TP2(+15%)出完, 130%出清
      - 'trailing': 漲幅超過 trail_active 後啟動移動停利，自最高點回檔 trail_pullback 停利；130%出清
      - 'cap_130_only': 僅在到達 130% 轉換價時提早停利，其餘抱至閉鎖期解禁日
      - 'pure_lockup': 純持有至閉鎖期解禁日收盤
    """
    entry_p = float(df['Open'].iloc[entry_idx])
    entry_date = df.index[entry_idx]
    if entry_p <= 0 or np.isnan(entry_p): return None
    
    target_130 = conv_price * 1.30 if (conv_price > 0 and enable_130_cap) else float('inf')
    target_tp1 = max(entry_p * 1.075, conv_price * 1.075)
    target_tp2 = max(entry_p * 1.15, conv_price * 1.15)
    
    sl_price = entry_p * (1.0 - sl_pct) if sl_pct is not None else 0.0
    
    position = 1.0
    realized = []
    max_h = entry_p
    min_l = entry_p
    trailing_peak = entry_p
    trailing_active = False
    
    exit_date = None
    exit_price = None
    
    for i in range(entry_idx, len(df)):
        row = df.iloc[i]
        curr_dt = df.index[i]
        o = float(row['Open'])
        h = float(row['High'])
        l = float(row['Low'])
        c = float(row['Close'])
        
        if h > max_h: max_h = h
        if l < min_l: min_l = l
        
        is_expired = (curr_dt >= lockup_dt)
        
        # 1. 純持有至解禁
        if tp_mode == 'pure_lockup':
            if is_expired or i == len(df) - 1:
                realized.append((1.0, c, "LOCKUP_EXPIRE", curr_dt))
                exit_date = curr_dt
                exit_price = c
                break
            continue
            
        # 2. 空間極限 130% 逼贖
        if enable_130_cap and h >= target_130:
            p_sell = max(o, target_130)
            realized.append((position, p_sell, "130%_CEILING", curr_dt))
            exit_date = curr_dt
            exit_price = p_sell
            position = 0.0
            break
            
        # 3. 停損保護
        if sl_pct is not None and l <= sl_price:
            p_sell = min(o, sl_price)
            realized.append((position, p_sell, "STOP_LOSS", curr_dt))
            exit_date = curr_dt
            exit_price = p_sell
            position = 0.0
            break
            
        # 4. 出場邏輯分支
        if tp_mode == 'cap_130_only':
            if is_expired or i == len(df) - 1:
                realized.append((position, c, "LOCKUP_EXPIRE", curr_dt))
                exit_date = curr_dt
                exit_price = c
                break
        elif tp_mode == 'fixed_tp':
            if position == 1.0 and h >= target_tp1:
                p_sell = max(o, target_tp1)
                realized.append((0.5, p_sell, "TP1_HALF", curr_dt))
                position = 0.5
                if sl_pct is not None:
                    sl_price = max(sl_price, entry_p) # 保本
            if position == 0.5 and h >= target_tp2:
                p_sell = max(o, target_tp2)
                realized.append((0.5, p_sell, "TP2_FULL", curr_dt))
                exit_date = curr_dt
                exit_price = p_sell
                position = 0.0
                break
            if is_expired or i == len(df) - 1:
                realized.append((position, c, "LOCKUP_EXPIRE", curr_dt))
                exit_date = curr_dt
                exit_price = c
                break
        elif tp_mode == 'trailing':
            # 檢查是否啟動移動停利
            if h >= entry_p * (1.0 + trail_active):
                trailing_active = True
            if trailing_active:
                if h > trailing_peak:
                    trailing_peak = h
                trail_stop = trailing_peak * (1.0 - trail_pullback)
                if l <= trail_stop:
                    p_sell = min(o, trail_stop)
                    realized.append((position, p_sell, "TRAILING_STOP", curr_dt))
                    exit_date = curr_dt
                    exit_price = p_sell
                    position = 0.0
                    break
            if is_expired or i == len(df) - 1:
                realized.append((position, c, "LOCKUP_EXPIRE", curr_dt))
                exit_date = curr_dt
                exit_price = c
                break

    tot_rev = sum(w * p for w, p, r, d in realized)
    ret_pct = ((tot_rev - entry_p) / entry_p) * 100.0
    holding_days = len(df.loc[entry_date:exit_date]) if exit_date else 0
    reasons = "/".join(r for w, p, r, d in realized)
    
    return {
        'entry_date': entry_date.strftime('%Y-%m-%d'),
        'entry_price': round(entry_p, 2),
        'exit_date': exit_date.strftime('%Y-%m-%d') if exit_date else '',
        'exit_price': round(exit_price, 2) if exit_price else 0.0,
        'return_pct': round(ret_pct, 2),
        'holding_days': holding_days,
        'exit_reason': reasons,
        'max_runup_pct': round(((max_h - entry_p) / entry_p) * 100.0, 2),
        'max_drawdown_pct': round(((min_l - entry_p) / entry_p) * 100.0, 2)
    }

def eval_matrix():
    param_set = [
        # (說明, entry_offset, sl_pct, tp_mode, trail_active, trail_pullback, cap_130, vol_filter)
        ("A1. 原版四價 (SL 5%, TP1/2分批)", -5, 0.05, 'fixed_tp', 0, 0, True, 500),
        ("A2. 原版四價 (SL 8%, TP1/2分批)", -5, 0.08, 'fixed_tp', 0, 0, True, 500),
        ("A3. 原版四價 (SL 10%, TP1/2分批)", -5, 0.10, 'fixed_tp', 0, 0, True, 500),
        
        ("B1. 移動停利 (SL 8%, 漲10%後回檔8%停利, 130%上限)", -5, 0.08, 'trailing', 0.10, 0.08, True, 500),
        ("B2. 移動停利 (SL 10%, 漲12%後回檔8%停利, 130%上限)", -5, 0.10, 'trailing', 0.12, 0.08, True, 500),
        ("B3. 移動停利 (SL 12%, 漲15%後回檔10%停利, 130%上限)", -5, 0.12, 'trailing', 0.15, 0.10, True, 500),
        
        ("C1. 波段保護 (SL 8%, 130%上限清倉, 其餘持股至解禁)", -5, 0.08, 'cap_130_only', 0, 0, True, 500),
        ("C2. 波段保護 (SL 10%, 130%上限清倉, 其餘持股至解禁)", -5, 0.10, 'cap_130_only', 0, 0, True, 500),
        ("C3. 波段保護 (SL 12%, 130%上限清倉, 其餘持股至解禁)", -5, 0.12, 'cap_130_only', 0, 0, True, 500),
        
        ("D1. 純閉鎖期持有 (無停損, 130%上限提早停利)", -5, None, 'cap_130_only', 0, 0, True, 500),
        ("D2. 純閉鎖期持有 (完全不干預, 持股滿3個月收盤平倉)", -5, None, 'pure_lockup', 0, 0, False, 500),
        
        ("E1. 掛牌日進場 (波段保護 SL 10%, 130%上限清倉)", 0, 0.10, 'cap_130_only', 0, 0, True, 500),
        ("E2. 掛牌日進場 (純閉鎖期持有 滿3個月解禁收盤平倉)", 0, None, 'pure_lockup', 0, 0, False, 500),
    ]

    summary_rows = []
    
    for name, offset, sl, tp_m, t_act, t_pb, c130, vol in param_set:
        trades = []
        for cb in cb_issues:
            code = cb['IssuerCode']
            if code not in ohlcv_dict: continue
            df = ohlcv_dict[code].copy()
            if len(df) < 30: continue
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            
            issue_dt = pd.to_datetime(cb['IssueDate'])
            lockup_dt = pd.to_datetime(cb['Conversion/ExchangePeriodStartDate'])
            conv_price = float(cb.get('ConversionPrice', 0))
            
            avail = df.index[df.index <= issue_dt]
            if len(avail) == 0: continue
            issue_idx = df.index.get_loc(avail[-1])
            target_idx = max(0, issue_idx + offset)
            if target_idx < 10: continue
            
            # vol filter
            vols = df['Volume'].iloc[target_idx-10:target_idx]
            if vols.mean() / 1000.0 < vol: continue
            
            res = simulate_trade_strategy(
                df, target_idx, lockup_dt, conv_price,
                sl_pct=sl, tp_mode=tp_m, trail_active=t_act, trail_pullback=t_pb,
                enable_130_cap=c130
            )
            if res:
                res['code'] = code
                res['name'] = cb.get('IssuerName', '')
                res['cb_name'] = cb.get('ShortName', '')
                trades.append(res)
                
        df_t = pd.DataFrame(trades)
        if df_t.empty: continue
        
        n = len(df_t)
        win = (df_t['return_pct'] > 0).sum()
        win_rate = win / n * 100.0
        avg_ret = df_t['return_pct'].mean()
        tot_ret = df_t['return_pct'].sum()
        
        pos_sum = df_t[df_t['return_pct'] > 0]['return_pct'].sum()
        neg_sum = abs(df_t[df_t['return_pct'] < 0]['return_pct'].sum())
        pf = round(pos_sum / neg_sum, 2) if neg_sum > 0 else 999.0
        
        avg_hold = df_t['holding_days'].mean()
        max_w = df_t['return_pct'].max()
        max_l = df_t['return_pct'].min()
        
        cum = (1 + df_t['return_pct']/100.0).cumprod()
        mdd = ((cum - cum.cummax()) / cum.cummax() * 100.0).min()
        
        summary_rows.append({
            '策略配置': name,
            '筆數': n,
            '勝率(%)': round(win_rate, 2),
            '平均報酬(%)': round(avg_ret, 2),
            '獲利因子': pf,
            '總報酬(%)': round(tot_ret, 1),
            '最大獲利(%)': round(max_w, 1),
            '最大虧損(%)': round(max_l, 1),
            '平均持有天數': round(avg_hold, 1),
            '最大策略回撤(%)': round(mdd, 2)
        })

    df_summary = pd.DataFrame(summary_rows)
    print("\n" + "="*85)
    print("【可轉債策略 參數優化與回測成果矩陣 (114年1月~115年9月)】")
    print("="*85)
    print(df_summary.to_string(index=False))
    
    # 儲存 CSV 供報告引用
    df_summary.to_csv(os.path.join(BASE_DIR, "optimization_matrix.csv"), index=False, encoding='utf-8-sig')

if __name__ == "__main__":
    eval_matrix()
