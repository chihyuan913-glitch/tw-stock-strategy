#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import numpy as np

conn_p = sqlite3.connect('strategies/02_institutional_momentum/cache/prices.db')
benchmark_df = pd.read_sql_query('SELECT date, close FROM benchmark ORDER BY date ASC', conn_p).set_index('date')
prices_df = pd.read_sql_query('SELECT code, date, open, high, low, close, volume FROM daily_prices ORDER BY code, date ASC', conn_p)
conn_p.close()

conn_c = sqlite3.connect('strategies/02_institutional_momentum/cache/chips.db')
chips_df = pd.read_sql_query('SELECT date, code, name, foreign_net, trust_net, dealer_net, total_net FROM daily_chips ORDER BY code, date ASC', conn_c)
conn_c.close()

chips_df = chips_df[~chips_df['code'].str.startswith('00')].copy()
prices_df = prices_df[~prices_df['code'].str.startswith('00')].copy()
stock_names = chips_df[['code', 'name']].drop_duplicates().set_index('code')['name'].to_dict()

benchmark_df['ma20'] = benchmark_df['close'].rolling(20).mean()
benchmark_df['bull'] = benchmark_df['close'] >= benchmark_df['ma20']

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
    df['bias20'] = ((df['close'] - df['ma20']) / df['ma20']) * 100.0
    df['body'] = (df['close'] - df['open']).abs()
    df['upper_shadow'] = df['high'] - df[['open', 'close']].max(axis=1)
    df.set_index('date', inplace=True)
    stock_price_dict[code] = df

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

daily_signals = {}
for d in test_dates:
    if not benchmark_df.loc[d, 'bull']:
        continue
    g_idx = all_dates.index(d)
    if g_idx < 4:
        continue
    d_5d = all_dates[g_idx-4 : g_idx+1]
    d_3d = all_dates[g_idx-2 : g_idx+1]
    candidates = []
    for code, p in stock_price_dict.items():
        if d not in p.index:
            continue
        p_row = p.loc[d]
        if pd.isna(p_row['ma20']) or pd.isna(p_row['ma20_prev']) or pd.isna(p_row['v5']):
            continue
        if p_row['v5'] < 1000000:
            continue
        if not (p_row['close'] >= p_row['ma20'] and p_row['ma20'] > p_row['ma20_prev']):
            continue
        if not (0.0 <= p_row['bias20'] < 8.0):
            continue
        body = p_row['body']
        shadow = p_row['upper_shadow']
        if body > 0.05 and shadow >= 0.5 * body:
            continue
        elif body <= 0.05 and shadow > p_row['close'] * 0.005:
            continue
        c_today = chips_lookup.get((d, code))
        if not c_today or p_row['volume'] <= 0 or c_today['total'] <= 0:
            continue
        inst_ratio = (c_today['total'] / p_row['volume']) * 100.0
        if inst_ratio < 10.0:
            continue
        f_3d = sum(chips_lookup.get((dt, code), {}).get('foreign', 0) for dt in d_3d)
        t_3d = sum(chips_lookup.get((dt, code), {}).get('trust', 0) for dt in d_3d)
        if not (f_3d >= 1000000 or t_3d >= 1000000):
            continue
        tot_5d = sum(chips_lookup.get((dt, code), {}).get('total', 0) for dt in d_5d)
        if tot_5d <= 0:
            continue
        candidates.append({'code': code, 'name': stock_names.get(code, code), 'inst_ratio': inst_ratio, 'tot_5d': tot_5d})
    if candidates:
        candidates.sort(key=lambda x: (x['inst_ratio'], x['tot_5d']), reverse=True)
        daily_signals[d] = candidates

def test_exits(tp1_pct=12.0, tp2_pct=20.0, trail_ma='ma5', max_hold=25, hard_sl=5.0):
    cash = 1000000.0
    active_pos = {}
    trades = []
    equity = []
    COMM = 0.001425 * 0.5
    TAX = 0.003
    for d in test_dates:
        g_idx = all_dates.index(d)
        prev_d = all_dates[g_idx - 1] if g_idx > 0 else None
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
                                    'days': 0, 'partial_tp': False, 'tp1_shares': sh // 2, 'tp1_cash': 0.0
                                }
        to_del = []
        for code, pos in active_pos.items():
            pos['days'] += 1
            pdf = stock_price_dict[code]
            if d not in pdf.index:
                continue
            p = pdf.loc[d]
            ex = False
            ex_p = 0.0
            tp1_price = pos['entry_price'] * (1.0 + tp1_pct / 100.0)
            tp2_price = pos['entry_price'] * (1.0 + tp2_pct / 100.0)
            sl_hard = pos['entry_price'] * (1.0 - hard_sl / 100.0)

            if p['low'] <= sl_hard:
                ex = True; ex_p = sl_hard
            elif p['close'] < p['ma20'] * 0.98:
                ex = True; ex_p = p['close']
            elif tp2_pct > 0 and p['high'] >= tp2_price:
                ex = True; ex_p = tp2_price
            elif not pos['partial_tp'] and p['high'] >= tp1_price:
                pos['partial_tp'] = True
                g = pos['tp1_shares'] * tp1_price
                net = g * (1.0 - COMM - TAX)
                cash += net
                pos['tp1_cash'] = net
                pos['shares'] -= pos['tp1_shares']
            elif pos['partial_tp'] and p['close'] < p[trail_ma]:
                ex = True; ex_p = p['close']
            elif pos['days'] >= max_hold:
                ex = True; ex_p = p['close']
            elif d == test_dates[-1]:
                ex = True; ex_p = p['close']

            if ex:
                g = pos['shares'] * ex_p
                net = g * (1.0 - COMM - TAX)
                cash += net
                tot_real = pos['tp1_cash'] + net
                pnl = tot_real - pos['invested']
                ret = pnl / pos['invested'] * 100.0
                trades.append({'ret': ret, 'pnl': pnl})
                to_del.append(code)
        for c in to_del:
            del active_pos[c]
        h_val = sum(pos['shares'] * (stock_price_dict[code].loc[d, 'close'] if d in stock_price_dict[code].index else pos['entry_price']) for code, pos in active_pos.items())
        equity.append(cash + h_val)

    df_t = pd.DataFrame(trades)
    fin_eq = equity[-1]
    tot_ret = (fin_eq - 1000000.0) / 10000.0
    win_r = (df_t['pnl'] > 0).mean() * 100
    pf = df_t[df_t['pnl']>0]['pnl'].sum() / abs(df_t[df_t['pnl']<0]['pnl'].sum())
    s_eq = pd.Series(equity)
    mdd = abs(((s_eq - s_eq.cummax()) / s_eq.cummax()).min()) * 100.0
    avg_ret = df_t['ret'].mean()
    print(f"TP1: {tp1_pct:4.1f}% | TP2: {tp2_pct:4.1f}% | Trail: {trail_ma:4s} | Hold: {max_hold:2d}d | HardSL: {hard_sl:3.1f}% -> Return: {tot_ret:+6.2f}% | WinRate: {win_r:4.1f}% | PF: {pf:4.2f} | MDD: -{mdd:4.1f}% | AvgRet: {avg_ret:+4.2f}%")

print("Exit Parameter Grid Search:")
for tp1 in [10.0, 12.0, 15.0]:
    for tp2 in [18.0, 20.0, 25.0]:
        for tr in ['ma5', 'ma10']:
            for mh in [20, 25, 30]:
                test_exits(tp1_pct=tp1, tp2_pct=tp2, trail_ma=tr, max_hold=mh, hard_sl=5.0)
