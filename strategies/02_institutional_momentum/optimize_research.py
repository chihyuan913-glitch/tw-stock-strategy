#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 02：法人起漲動能策略 參數與條件優化量化實驗引擎
深入探索：
1. 大盤環境濾網 (Market Regime Filter: TAIEX > 20MA)
2. 量能爆發確認 (Volume Expansion: Volume > 1.0x / 1.2x V5MA)
3. 投信主力加權 (Trust priority / Trust 3d lots)
4. 起漲甜蜜乖離區間 (Bias sweet spot: 1%~6% vs 0%~8%)
5. 階梯式保本與動態波段停利 (Tiered Trailing Stop & Break-even)
"""

import sqlite3
import pandas as pd
import numpy as np
import math

conn_p = sqlite3.connect('strategies/02_institutional_momentum/cache/prices.db')
benchmark_df = pd.read_sql_query('SELECT date, close FROM benchmark ORDER BY date ASC', conn_p).set_index('date')
prices_df = pd.read_sql_query('SELECT code, date, open, high, low, close, volume FROM daily_prices ORDER BY code, date ASC', conn_p)
conn_p.close()

conn_c = sqlite3.connect('strategies/02_institutional_momentum/cache/chips.db')
chips_df = pd.read_sql_query('SELECT date, code, name, foreign_net, trust_net, dealer_net, total_net FROM daily_chips ORDER BY code, date ASC', conn_c)
conn_c.close()

# 排除 00 開頭 ETF
chips_df = chips_df[~chips_df['code'].str.startswith('00')].copy()
prices_df = prices_df[~prices_df['code'].str.startswith('00')].copy()

stock_names = chips_df[['code', 'name']].drop_duplicates().set_index('code')['name'].to_dict()

# 計算大盤 20MA 與趨勢
benchmark_df['ma20'] = benchmark_df['close'].rolling(20).mean()
benchmark_df['market_bull'] = benchmark_df['close'] >= benchmark_df['ma20']

# 計算個股技術指標
stock_price_dict = {}
for code, group in prices_df.groupby('code'):
    df = group.copy().sort_values('date').reset_index(drop=True)
    if len(df) < 25:
        continue
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma20_prev'] = df['ma20'].shift(1)
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['v5'] = df['volume'].rolling(5).mean()
    df['vol_ratio'] = df['volume'] / df['v5']
    df['bias20'] = ((df['close'] - df['ma20']) / df['ma20']) * 100.0
    df['body'] = (df['close'] - df['open']).abs()
    df['upper_shadow'] = df['high'] - df[['open', 'close']].max(axis=1)
    df.set_index('date', inplace=True)
    stock_price_dict[code] = df

# 建立籌碼查詢字典
chips_lookup = {}
for _, row in chips_df.iterrows():
    chips_lookup[(row['date'], row['code'])] = {
        'foreign': row['foreign_net'],
        'trust': row['trust_net'],
        'dealer': row['dealer_net'],
        'total': row['total_net'],
    }

all_dates = sorted(list(benchmark_df.index))
test_dates = [d for d in all_dates if '20250102' <= d <= '20260930']

COMM = 0.001425 * 0.5
TAX = 0.003

def backtest_strategy(
    use_market_filter=False,
    min_vol_ratio=0.0,       # 成交量 / 5日均量
    min_inst_ratio=10.0,     # 法人佔比
    bias_min=0.0,
    bias_max=8.0,
    trust_priority=False,    # 投信優先
    exit_type='tiered_trail',# 'tiered_trail', 'close_sop', 'pure_trail'
    max_holding_days=25
):
    # 逐日篩選
    daily_signals = {}
    for d in test_dates:
        # 大盤濾網檢查
        if use_market_filter:
            if not benchmark_df.loc[d, 'market_bull']:
                continue

        g_idx = all_dates.index(d)
        if g_idx < 4:
            continue
        g_idx_start = max(0, g_idx - 4)
        d_5d = all_dates[g_idx_start : g_idx + 1]
        d_3d = all_dates[max(0, g_idx - 2) : g_idx + 1]
        
        candidates = []
        for code, p in stock_price_dict.items():
            if d not in p.index:
                continue
            p_row = p.loc[d]
            if pd.isna(p_row['ma20']) or pd.isna(p_row['ma20_prev']) or pd.isna(p_row['v5']):
                continue
            if p_row['v5'] < 1000000:
                continue
            # 月線向上
            if not (p_row['close'] >= p_row['ma20'] and p_row['ma20'] > p_row['ma20_prev']):
                continue
            # 乖離率
            if not (bias_min <= p_row['bias20'] < bias_max):
                continue
            # 量能放大
            if min_vol_ratio > 0 and p_row['vol_ratio'] < min_vol_ratio:
                continue
            # 上影線
            body = p_row['body']
            shadow = p_row['upper_shadow']
            if body > 0.05:
                if shadow >= 0.5 * body:
                    continue
            else:
                if shadow > p_row['close'] * 0.005:
                    continue
            # 籌碼
            c_today = chips_lookup.get((d, code))
            if not c_today or p_row['volume'] <= 0 or c_today['total'] <= 0:
                continue
            inst_ratio = (c_today['total'] / p_row['volume']) * 100.0
            if inst_ratio < min_inst_ratio:
                continue
            f_3d = sum(chips_lookup.get((dt, code), {}).get('foreign', 0) for dt in d_3d)
            t_3d = sum(chips_lookup.get((dt, code), {}).get('trust', 0) for dt in d_3d)
            if not (f_3d >= 1000000 or t_3d >= 1000000):
                continue
            tot_5d = sum(chips_lookup.get((dt, code), {}).get('total', 0) for dt in d_5d)
            if tot_5d <= 0:
                continue

            candidates.append({
                'code': code,
                'name': stock_names.get(code, code),
                'inst_ratio': inst_ratio,
                'trust_ratio': (c_today['trust'] / p_row['volume']) * 100.0 if c_today['trust'] > 0 else 0.0,
                'tot_5d': tot_5d,
                'vol_ratio': p_row['vol_ratio'],
                'signal_date': d
            })
            
        if candidates:
            if trust_priority:
                candidates.sort(key=lambda x: (x['trust_ratio'], x['inst_ratio']), reverse=True)
            else:
                candidates.sort(key=lambda x: (x['inst_ratio'], x['tot_5d']), reverse=True)
            daily_signals[d] = candidates

    # 模擬撮合
    cash = 1000000.0
    active_pos = {}
    trades = []
    equity = []

    for d in test_dates:
        g_idx = all_dates.index(d)
        prev_d = all_dates[g_idx - 1] if g_idx > 0 else None
        
        # 開倉
        if prev_d and prev_d in daily_signals:
            for sig in daily_signals[prev_d]:
                code = sig['code']
                if code in active_pos:
                    continue
                if len(active_pos) < 5 and cash >= 160000:
                    pdf = stock_price_dict[code]
                    if d in pdf.index:
                        open_p = pdf.loc[d, 'open']
                        if open_p > 0:
                            alloc = min(200000.0, cash)
                            cost_p = open_p * (1.0 + COMM)
                            sh = int(alloc // cost_p)
                            if sh >= 100:
                                inv = sh * cost_p
                                cash -= inv
                                active_pos[code] = {
                                    'code': code, 'name': sig['name'], 'entry_date': d,
                                    'entry_price': open_p, 'shares': sh, 'invested': inv,
                                    'days': 0, 'partial_tp': False, 'tp1_shares': sh // 2,
                                    'tp1_cash': 0.0, 'max_high': open_p
                                }
        # 平倉監控
        to_del = []
        for code, pos in active_pos.items():
            pos['days'] += 1
            pdf = stock_price_dict[code]
            if d not in pdf.index:
                continue
            p = pdf.loc[d]
            pos['max_high'] = max(pos['max_high'], p['high'])
            gain_pct = (p['high'] - pos['entry_price']) / pos['entry_price'] * 100.0

            ex = False
            ex_p = 0.0
            reason = ''

            if exit_type == 'close_sop':
                # SOP 模式
                if p['low'] <= pos['entry_price'] * 0.95:
                    ex = True; ex_p = pos['entry_price'] * 0.95; reason = 'HARD_SL_5%'
                elif p['close'] < p['ma20'] * 0.98:
                    ex = True; ex_p = p['close']; reason = 'CLOSE_MA20'
                elif p['high'] >= pos['entry_price'] * 1.20:
                    ex = True; ex_p = pos['entry_price'] * 1.20; reason = 'TP2'
                elif p['high'] >= pos['entry_price'] * 1.12 and not pos['partial_tp']:
                    pos['partial_tp'] = True
                    g = pos['tp1_shares'] * (pos['entry_price'] * 1.12)
                    net = g * (1.0 - COMM - TAX)
                    cash += net
                    pos['tp1_cash'] = net
                    pos['shares'] -= pos['tp1_shares']
                elif pos['partial_tp'] and p['close'] < p['ma5']:
                    ex = True; ex_p = p['close']; reason = 'TRAIL_MA5'
                elif pos['days'] >= max_holding_days:
                    ex = True; ex_p = p['close']; reason = 'TIME'
                elif d == test_dates[-1]:
                    ex = True; ex_p = p['close']; reason = 'END'

            elif exit_type == 'tiered_trail':
                # 階梯式保本與動態移動停利 (優化版)
                # 1. 硬停損：-5%
                if p['low'] <= pos['entry_price'] * 0.95:
                    ex = True; ex_p = pos['entry_price'] * 0.95; reason = 'HARD_SL_5%'
                # 2. 獲利已超過 +6%：若跌破成本保本價 (進場價 * 1.002) 則保本出場
                elif gain_pct >= 6.0 and p['close'] < pos['entry_price'] * 1.002:
                    ex = True; ex_p = p['close']; reason = 'BREAK_EVEN'
                # 3. 獲利已超過 +12%：啟動 10MA 移動停利 (跌破 10MA 出場)
                elif gain_pct >= 12.0 and p['close'] < p['ma10']:
                    ex = True; ex_p = p['close']; reason = 'TRAIL_MA10'
                # 4. 常規停損：收盤跌破月線 2%
                elif p['close'] < p['ma20'] * 0.98:
                    ex = True; ex_p = p['close']; reason = 'CLOSE_MA20'
                # 5. 時間停損
                elif pos['days'] >= max_holding_days:
                    ex = True; ex_p = p['close']; reason = 'TIME'
                elif d == test_dates[-1]:
                    ex = True; ex_p = p['close']; reason = 'END'

            if ex:
                g = pos['shares'] * ex_p
                net = g * (1.0 - COMM - TAX)
                cash += net
                tot_real = pos['tp1_cash'] + net
                pnl = tot_real - pos['invested']
                ret = pnl / pos['invested'] * 100.0
                trades.append({'code': code, 'name': pos['name'], 'pnl': pnl, 'ret': ret, 'days': pos['days'], 'reason': reason})
                to_del.append(code)

        for c in to_del:
            del active_pos[c]
        h_val = sum(pos['shares'] * (stock_price_dict[code].loc[d, 'close'] if d in stock_price_dict[code].index else pos['entry_price']) for code, pos in active_pos.items())
        equity.append(cash + h_val)

    df_t = pd.DataFrame(trades)
    fin_eq = equity[-1]
    tot_ret = (fin_eq - 1000000.0) / 10000.0
    win_cnt = (df_t['pnl'] > 0).sum() if len(df_t) else 0
    win_r = (win_cnt / len(df_t) * 100.0) if len(df_t) else 0.0
    loss_sum = abs(df_t[df_t['pnl']<0]['pnl'].sum()) if len(df_t[df_t['pnl']<0]) else 1.0
    win_sum = df_t[df_t['pnl']>0]['pnl'].sum() if len(df_t[df_t['pnl']>0]) else 0.0
    pf = win_sum / loss_sum if loss_sum > 0 else 999.0
    s_eq = pd.Series(equity)
    mdd = abs(((s_eq - s_eq.cummax()) / s_eq.cummax()).min()) * 100.0
    avg_r = df_t['ret'].mean() if len(df_t) else 0.0

    return {
        'trades': len(df_t),
        'win_rate': round(win_r, 1),
        'tot_return': round(tot_ret, 2),
        'pf': round(pf, 2),
        'mdd': round(mdd, 2),
        'avg_ret': round(avg_r, 2),
        'final_equity': round(fin_eq, 0)
    }

print("=========================================================================================")
print("  策略 02 進出場條件優化量化實驗矩陣 (114年1月~115年9月底)")
print("=========================================================================================")

experiments = [
    ("1. 原生基準 (Close-SOP, 無大盤濾網)", {}),
    ("2. 引入大盤濾網 (加權指數 > 20MA 始進場)", {'use_market_filter': True}),
    ("3. 引入量能爆發確認 (當日量 > 1.2倍 5日均量)", {'min_vol_ratio': 1.2}),
    ("4. 引入起漲甜蜜乖離 (乖離限縮至 1.0%~6.0%)", {'bias_min': 1.0, 'bias_max': 6.0}),
    ("5. 提高法人定價門檻 (法人佔比 >= 15%)", {'min_inst_ratio': 15.0}),
    ("6. 投信優先選股 (Trust Priority)", {'trust_priority': True}),
    ("7. 階梯式保本與動態移動停利 (Tiered Trailing)", {'exit_type': 'tiered_trail', 'max_holding_days': 30}),
    ("8. 組合優化 A (大盤濾網 + 量能爆發 1.2x + 階梯移動停利)", {'use_market_filter': True, 'min_vol_ratio': 1.2, 'exit_type': 'tiered_trail', 'max_holding_days': 30}),
    ("9. 組合優化 B (大盤濾網 + 投信優先 + 階梯移動停利)", {'use_market_filter': True, 'trust_priority': True, 'exit_type': 'tiered_trail', 'max_holding_days': 30}),
    ("10. 終極黃金組合 (大盤濾網 + 量能>1.1x + 甜蜜乖離 + 階梯移動停利)", {
        'use_market_filter': True,
        'min_vol_ratio': 1.1,
        'bias_min': 0.5,
        'bias_max': 6.5,
        'exit_type': 'tiered_trail',
        'max_holding_days': 30
    })
]

for label, params in experiments:
    res = backtest_strategy(**params)
    print(f"{label:<38} | 交易: {res['trades']:3d} 筆 | 勝率: {res['win_rate']:4.1f}% | 總報酬: {res['tot_return']:+6.2f}% | PF: {res['pf']:4.2f} | MDD: -{res['mdd']:4.1f}% | 筆均: {res['avg_ret']:+4.2f}%")
