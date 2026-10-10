#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略三優化研究腳本：進出場條件參數網格分析
測試維度：
1. 大盤避險濾網 (Market Regime Filter): 全部行情 vs 僅大盤弱勢時放空
2. 進場負乖離區間 (Entry Bias): [-8%, 0%] vs [-5%, 0%] vs [-4%, 0%]
3. 停利目標 (TP1/TP2):
   - 現行基準: TP1 -12%, TP2 -20%
   - 靈敏波段: TP1 -8%, TP2 -15%
   - 快打保益: TP1 -6%, TP2 -12%
   - 黃金比例: TP1 -7%, TP2 -14%
4. 持倉週期 (Max Holding Days): 5, 8, 10, 15, 20 天
5. 移動保本停損 (Trailing Breakeven Stop):
   - 無保本 (現行)
   - 浮盈達 +3% 時啟動保本 (SL 移至進場價)
   - 浮盈達 +4% 時啟動保本 (SL 移至進場價)
6. 籌碼集中度 (Institutional Sell Ratio): 10% vs 15% vs 20%
"""

import os
import sys
import json
import sqlite3
import numpy as np
import pandas as pd
import yfinance as yf

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
STRATEGY_DIR = os.path.dirname(BASE_DIR)
FUTURES_JSON_PATH = os.path.join(STRATEGY_DIR, "stock_futures_list.json")
DB_PATH = os.path.join(BASE_DIR, "chips_futures.db")
OHLCV_PATH = os.path.join(BASE_DIR, "ohlcv_futures.pkl")

START_DATE = "20250101"
END_DATE = "20260930"

def load_data():
    with open(FUTURES_JSON_PATH, "r", encoding="utf-8") as f:
        futures_meta = json.load(f)

    ohlcv_df = pd.read_pickle(OHLCV_PATH)
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT date, code, foreign_net, trust_net, dealer_net, total_net FROM chips")
    chips_rows = cur.fetchall()
    conn.close()

    chips_dict = {}
    for r in chips_rows:
        d_str, code, f_net, t_net, d_net, tot_net = r
        chips_dict[(d_str, code)] = {
            'foreign': f_net,
            'trust': t_net,
            'dealer': d_net,
            'total': tot_net
        }

    taiex = yf.download('0050.TW', start='2024-11-01', end='2026-10-01', auto_adjust=False, progress=False)
    close_series = taiex['Close']['0050.TW'] if isinstance(taiex.columns, pd.MultiIndex) else taiex['Close']
    t_ma20 = close_series.rolling(20).mean()
    t_slope = t_ma20 - t_ma20.shift(1)

    market_regime = {}
    for d, c_val, ma_val, slp_val in zip(taiex.index, close_series, t_ma20, t_slope):
        d_str = d.strftime('%Y%m%d')
        market_regime[d_str] = bool((c_val <= ma_val) or (slp_val < 0))

    return futures_meta, ohlcv_df, chips_dict, market_regime

def prepare_stock_series(ohlcv_df, code):
    ticker_candidates = [f"{code}.TW", f"{code}.TWO"]
    target_ticker = None
    for cand in ticker_candidates:
        if ('Close', cand) in ohlcv_df.columns:
            target_ticker = cand
            break

    if target_ticker is None:
        return None

    try:
        df = pd.DataFrame({
            'open': ohlcv_df[('Open', target_ticker)],
            'high': ohlcv_df[('High', target_ticker)],
            'low': ohlcv_df[('Low', target_ticker)],
            'close': ohlcv_df[('Close', target_ticker)],
            'volume': ohlcv_df[('Volume', target_ticker)]
        }).dropna()

        if len(df) < 25:
            return None

        df['date_str'] = [d.strftime('%Y%m%d') for d in df.index]
        return df
    except Exception:
        return None

def precompute_stocks(futures_meta, ohlcv_df):
    stock_data_map = {}
    for code in futures_meta.keys():
        s_df = prepare_stock_series(ohlcv_df, code)
        if s_df is not None:
            s_df['ma20'] = s_df['close'].rolling(20).mean()
            s_df['ma20_slope'] = s_df['ma20'] - s_df['ma20'].shift(1)
            s_df['vol_ma5'] = s_df['volume'].rolling(5).mean()
            s_df['bias20'] = (s_df['close'] - s_df['ma20']) / s_df['ma20'] * 100.0
            s_df['body'] = (s_df['close'] - s_df['open']).abs()
            s_df['lower_shadow'] = np.minimum(s_df['open'], s_df['close']) - s_df['low']
            s_df = s_df.reset_index(drop=True)
            date_to_idx = {row['date_str']: idx for idx, row in s_df.iterrows()}
            stock_data_map[code] = (s_df, date_to_idx)
    return stock_data_map

def evaluate_strategy(futures_meta, stock_data_map, chips_dict, market_regime,
                      hedge_filter=True,
                      bias_min=-8.0,
                      sell_ratio_min=10.0,
                      tp1_ratio=0.88, # ma20 * 0.88 = -12%
                      tp2_ratio=0.80, # ma20 * 0.80 = -20%
                      sl_ratio=1.02,  # ma20 * 1.02 = +2%
                      max_hold=20,
                      breakeven_trigger=None): # 例如 0.03 (浮盈達3%移至進場價保本)
    
    trading_dates_all = sorted(list(set(d_str for (d_str, c) in chips_dict.keys())))
    backtest_dates = [d for d in trading_dates_all if START_DATE <= d <= END_DATE]

    signals = []
    for d_str in backtest_dates:
        if hedge_filter and not market_regime.get(d_str, True):
            continue

        past_5_dates = [d for d in trading_dates_all if d <= d_str][-5:]
        past_3_dates = past_5_dates[-3:]
        if len(past_5_dates) < 5:
            continue

        for code, meta in futures_meta.items():
            if code not in stock_data_map:
                continue
            s_df, date_to_idx = stock_data_map[code]
            if d_str not in date_to_idx:
                continue
            curr_idx = date_to_idx[d_str]
            if curr_idx < 20:
                continue

            row_today = s_df.iloc[curr_idx]
            if pd.isna(row_today['vol_ma5']) or (row_today['vol_ma5'] < 1000 * 1000):
                continue
            if row_today['close'] > row_today['ma20'] or row_today['ma20_slope'] >= 0:
                continue
            if not (bias_min <= row_today['bias20'] <= 0.0):
                continue

            # K 棒無長下影線
            body = row_today['body']
            l_shadow = row_today['lower_shadow']
            if body > 0.05:
                if l_shadow >= 0.5 * body:
                    continue
            else:
                if l_shadow > (row_today['close'] * 0.005):
                    continue

            # 籌碼
            c_today = chips_dict.get((d_str, code))
            if not c_today or row_today['volume'] <= 0 or c_today['total'] >= 0:
                continue
            inst_sell_ratio = (abs(c_today['total']) / row_today['volume']) * 100.0
            if inst_sell_ratio < sell_ratio_min:
                continue

            f_3d = sum(chips_dict.get((d, code), {}).get('foreign', 0) for d in past_3_dates)
            t_3d = sum(chips_dict.get((d, code), {}).get('trust', 0) for d in past_3_dates)
            if not (f_3d <= -1000 * 1000 or t_3d <= -1000 * 1000):
                continue

            tot_5d = sum(chips_dict.get((d, code), {}).get('total', 0) for d in past_5_dates)
            if tot_5d >= 0:
                continue

            signals.append({
                'code': code,
                'signal_date': d_str,
                'signal_idx': curr_idx,
                'signal_close': row_today['close'],
                'signal_high': row_today['high'],
                'ma20': row_today['ma20'],
                'inst_sell_ratio': inst_sell_ratio
            })

    # 撮合
    trades = []
    FUTURES_FEE = 0.0005
    for sig in signals:
        code = sig['code']
        s_df, _ = stock_data_map[code]
        entry_idx = sig['signal_idx'] + 1
        if entry_idx >= len(s_df):
            continue

        entry_row = s_df.iloc[entry_idx]
        entry_p = entry_row['open']

        sl_p = max(sig['ma20'] * sl_ratio, sig['signal_high'] * 1.01)
        tp1_p = sig['ma20'] * tp1_ratio
        tp2_p = sig['ma20'] * tp2_ratio

        exit_p = None
        exit_reason = None
        holding_days = 0
        current_sl = sl_p

        for d_offset in range(1, max_hold + 1):
            curr_pos = entry_idx + (d_offset - 1)
            if curr_pos >= len(s_df):
                break

            r = s_df.iloc[curr_pos]
            holding_days = d_offset
            h = r['high']
            l = r['low']
            c = r['close']
            o = r['open']

            # 動態保本停損機制
            if breakeven_trigger is not None:
                max_gain = (entry_p - l) / entry_p
                if max_gain >= breakeven_trigger:
                    current_sl = min(current_sl, entry_p) # 移動至進場價保本

            # 動態月線停損
            dyn_sl = max(current_sl, r['ma20'] * sl_ratio)

            if h >= dyn_sl:
                exit_p = max(o, dyn_sl)
                exit_reason = "SL"
                break
            if l <= tp2_p:
                exit_p = min(o, tp2_p)
                exit_reason = "TP2"
                break
            if l <= tp1_p:
                exit_p = min(o, tp1_p)
                exit_reason = "TP1"
                break
            if d_offset == max_hold:
                exit_p = c
                exit_reason = "TIME"
                break

        if exit_p is None:
            exit_p = s_df.iloc[-1]['close']
            exit_reason = "END"

        ret = (entry_p - exit_p) / entry_p - FUTURES_FEE
        trades.append({
            'return_pct': ret * 100.0,
            'exit_reason': exit_reason,
            'holding_days': holding_days
        })

    if not trades:
        return None

    df_t = pd.DataFrame(trades)
    tot = len(df_t)
    w_cnt = len(df_t[df_t['return_pct'] > 0])
    w_rate = (w_cnt / tot) * 100.0
    tot_ret = df_t['return_pct'].sum()
    avg_ret = df_t['return_pct'].mean()
    w_sum = df_t[df_t['return_pct'] > 0]['return_pct'].sum()
    l_sum = abs(df_t[df_t['return_pct'] <= 0]['return_pct'].sum())
    pf = w_sum / l_sum if l_sum > 0 else 0
    avg_win = df_t[df_t['return_pct'] > 0]['return_pct'].mean() if w_cnt > 0 else 0
    avg_loss = df_t[df_t['return_pct'] <= 0]['return_pct'].mean() if (tot - w_cnt) > 0 else 0
    payoff = abs(avg_win / avg_loss) if abs(avg_loss) > 0 else 0
    tp1_cnt = len(df_t[df_t['exit_reason'] == 'TP1'])
    tp2_cnt = len(df_t[df_t['exit_reason'] == 'TP2'])
    sl_cnt = len(df_t[df_t['exit_reason'] == 'SL'])
    time_cnt = len(df_t[df_t['exit_reason'] == 'TIME'])

    return {
        'trades': tot,
        'win_rate': round(w_rate, 2),
        'total_return': round(tot_ret, 2),
        'avg_return': round(avg_ret, 2),
        'profit_factor': round(pf, 2),
        'payoff_ratio': round(payoff, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'avg_hold': round(df_t['holding_days'].mean(), 1),
        'tp1_pct': round(tp1_cnt / tot * 100.0, 1),
        'tp2_pct': round(tp2_cnt / tot * 100.0, 1),
        'sl_pct': round(sl_cnt / tot * 100.0, 1),
        'time_pct': round(time_cnt / tot * 100.0, 1)
    }

def run_parameter_grid():
    futures_meta, ohlcv_df, chips_dict, market_regime = load_data()
    stock_data_map = precompute_stocks(futures_meta, ohlcv_df)

    print("\n" + "="*95)
    print("🔬【參數網格實驗一】：大盤避險濾網 (Market Regime) 影響")
    print("="*95)
    res_raw = evaluate_strategy(futures_meta, stock_data_map, chips_dict, market_regime, hedge_filter=False)
    res_hedge = evaluate_strategy(futures_meta, stock_data_map, chips_dict, market_regime, hedge_filter=True)
    print(f"基準全天候做空 (無濾網) : 筆數 {res_raw['trades']:4d} | 勝率 {res_raw['win_rate']:5.2f}% | 總報酬 {res_raw['total_return']:+8.2f}% | 期望值 {res_raw['avg_return']:+5.2f}% | PF {res_raw['profit_factor']:.2f} | 盈虧比 {res_raw['payoff_ratio']:.2f}")
    print(f"大盤避險模式 (破月線下彎): 筆數 {res_hedge['trades']:4d} | 勝率 {res_hedge['win_rate']:5.2f}% | 總報酬 {res_hedge['total_return']:+8.2f}% | 期望值 {res_hedge['avg_return']:+5.2f}% | PF {res_hedge['profit_factor']:.2f} | 盈虧比 {res_hedge['payoff_ratio']:.2f}")

    print("\n" + "="*95)
    print("🔬【參數網格實驗二】：停利目標 (TP1 / TP2) 靈敏度優化 (在大盤避險模式下)")
    print("="*95)
    tp_configs = [
        ("基準 (TP1 -12%, TP2 -20%)", 0.88, 0.80),
        ("組合A (TP1 -10%, TP2 -16%)", 0.90, 0.84),
        ("組合B (TP1  -8%, TP2 -15%)", 0.92, 0.85),
        ("組合C (TP1  -7%, TP2 -12%)", 0.93, 0.88),
        ("組合D (TP1  -6%, TP2 -10%)", 0.94, 0.90),
    ]
    for label, tp1_r, tp2_r in tp_configs:
        res = evaluate_strategy(futures_meta, stock_data_map, chips_dict, market_regime,
                                hedge_filter=True, tp1_ratio=tp1_r, tp2_ratio=tp2_r)
        print(f"{label:26s} | 勝率 {res['win_rate']:5.2f}% | 總報酬 {res['total_return']:+8.2f}% | 期望值 {res['avg_return']:+5.2f}% | PF {res['profit_factor']:.2f} | TP1率 {res['tp1_pct']:4.1f}% | TP2率 {res['tp2_pct']:4.1f}%")

    print("\n" + "="*95)
    print("🔬【參數網格實驗三】：持倉天數上限 (Max Holding Days) 靈敏度優化")
    print("="*95)
    for h_days in [5, 8, 10, 12, 15, 20]:
        res = evaluate_strategy(futures_meta, stock_data_map, chips_dict, market_regime,
                                hedge_filter=True, max_hold=h_days, tp1_ratio=0.92, tp2_ratio=0.85)
        print(f"最大持倉 {h_days:2d} 天 | 勝率 {res['win_rate']:5.2f}% | 總報酬 {res['total_return']:+8.2f}% | 期望值 {res['avg_return']:+5.2f}% | PF {res['profit_factor']:.2f} | 停損率 {res['sl_pct']:4.1f}% | 到期平倉率 {res['time_pct']:4.1f}%")

    print("\n" + "="*95)
    print("🔬【參數網格實驗四】：移動保本停損機制 (Breakeven Protection) 測試")
    print("="*95)
    be_configs = [
        ("無保本 (常規月線停損)", None),
        ("浮盈達 +2.5% 啟動保本", 0.025),
        ("浮盈達 +3.0% 啟動保本", 0.030),
        ("浮盈達 +4.0% 啟動保本", 0.040),
        ("浮盈達 +5.0% 啟動保本", 0.050),
    ]
    for label, be_trig in be_configs:
        res = evaluate_strategy(futures_meta, stock_data_map, chips_dict, market_regime,
                                hedge_filter=True, tp1_ratio=0.92, tp2_ratio=0.85, max_hold=10, breakeven_trigger=be_trig)
        print(f"{label:22s} | 勝率 {res['win_rate']:5.2f}% | 總報酬 {res['total_return']:+8.2f}% | 期望值 {res['avg_return']:+5.2f}% | PF {res['profit_factor']:.2f} | 盈虧比 {res['payoff_ratio']:.2f}")

    print("\n" + "="*95)
    print("🔬【參數網格實驗五】：進場負乖離甜蜜區 (Bias Filter) 測試")
    print("="*95)
    for b_min in [-10.0, -8.0, -6.0, -5.0, -4.0]:
        res = evaluate_strategy(futures_meta, stock_data_map, chips_dict, market_regime,
                                hedge_filter=True, bias_min=b_min, tp1_ratio=0.92, tp2_ratio=0.85, max_hold=10, breakeven_trigger=0.03)
        print(f"負乖離區間 [{b_min:4.1f}%, 0%] | 筆數 {res['trades']:4d} | 勝率 {res['win_rate']:5.2f}% | 總報酬 {res['total_return']:+8.2f}% | 期望值 {res['avg_return']:+5.2f}% | PF {res['profit_factor']:.2f}")

    print("\n" + "="*95)
    print("🔬【參數網格實驗六】：法人當日賣超佔比門檻 (Sell Ratio Filter) 測試")
    print("="*95)
    for s_min in [10.0, 12.0, 15.0, 18.0, 20.0]:
        res = evaluate_strategy(futures_meta, stock_data_map, chips_dict, market_regime,
                                hedge_filter=True, bias_min=-5.0, sell_ratio_min=s_min,
                                tp1_ratio=0.92, tp2_ratio=0.85, max_hold=10, breakeven_trigger=0.03)
        print(f"法人賣超佔比 >= {s_min:4.1f}% | 筆數 {res['trades']:4d} | 勝率 {res['win_rate']:5.2f}% | 總報酬 {res['total_return']:+8.2f}% | 期望值 {res['avg_return']:+5.2f}% | PF {res['profit_factor']:.2f}")

    print("="*95 + "\n")

if __name__ == '__main__':
    run_parameter_grid()
