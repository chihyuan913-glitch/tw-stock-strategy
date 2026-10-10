#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略一優化實驗引擎：
基於 114/01/01 ~ 115/09/30 (2025/01/01 ~ 2026/09/30) 回測資料，
測試多組進出場條件優化方案：
1. 原版基準 (Baseline)
2. 方案 A：通道走平與反彈空間濾網 (排除向下大開口主跌段，2.0% <= upside <= 6.0%)
3. 方案 B：K線止跌嚴格確認 (拒絕弱黑K，要求紅K或下影線 >= 30%)
4. 方案 C：動態前低停損 (防線設為 max(LB * 0.95, Low * 0.99))
5. 方案 D：分批停利 + 移動保本防守 (觸及 TP1 出脫 50%，剩餘抱至 TP2 或破 20MA，同時啟動保本停損)
6. 方案 E：旗艦綜合優化版 (結合 A + B + C + D)
"""

import sys
import os
import sqlite3
import pickle
import pandas as pd
import numpy as np

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "t86.sqlite")
OHLCV_PATH = os.path.join(BASE_DIR, "ohlcv.pkl")

with open(OHLCV_PATH, 'rb') as f:
    ohlcv_raw = pickle.load(f)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("SELECT date, code, foreign_net, trust_net, dealer_net, total_net FROM t86_trades")
chips_dict = {}
for r in cur.fetchall():
    d_str, code, f_net, t_net, d_net, tot_net = r
    chips_dict[(d_str, code)] = {
        'foreign': f_net,
        'trust': t_net,
        'dealer': d_net,
        'total': tot_net
    }
conn.close()

open_df = ohlcv_raw['Open']
high_df = ohlcv_raw['High']
low_df = ohlcv_raw['Low']
close_df = ohlcv_raw['Close']
volume_df = ohlcv_raw['Volume']

date_strs = [d.strftime('%Y%m%d') for d in close_df.index]
date_to_idx = {d: i for i, d in enumerate(date_strs)}
eval_dates = [d for d in date_strs if '20250101' <= d <= '20260930']

available_tickers = list(close_df.columns)
ticker_to_code = {t: t.split('.')[0] for t in available_tickers}

def evaluate_signals(signals, sl_rule='fixed_lb', tp_rule='tp1_full', max_hold_days=20):
    trades = []
    for s in signals:
        t_idx = s['signal_idx']
        ticker = s['ticker']
        tp1 = s['tp1']
        tp2 = s['tp2']

        # 停損價位判定
        if sl_rule == 'fixed_lb':
            sl = s['sl_fixed']
        elif sl_rule == 'tight_low':
            sl = max(s['sl_fixed'], round(s['low_val'] * 0.99, 2))
        else:
            sl = s['sl_fixed']

        if t_idx + 1 >= len(date_strs):
            continue

        entry_date = date_strs[t_idx + 1]
        entry_price = float(open_df[ticker].iloc[t_idx + 1])
        if pd.isna(entry_price) or entry_price <= 0:
            entry_price = s['close']

        end_idx = min(t_idx + 1 + max_hold_days, len(date_strs))

        if tp_rule == 'tp1_full':
            exit_date = None
            exit_price = None
            exit_reason = None
            holding_days = 0

            for day_i, curr_idx in enumerate(range(t_idx + 1, end_idx), 1):
                curr_date = date_strs[curr_idx]
                curr_o = float(open_df[ticker].iloc[curr_idx])
                curr_h = float(high_df[ticker].iloc[curr_idx])
                curr_l = float(low_df[ticker].iloc[curr_idx])
                curr_c = float(close_df[ticker].iloc[curr_idx])
                if pd.isna(curr_c):
                    continue

                touch_sl = (curr_l <= sl)
                touch_tp1 = (curr_h >= tp1)

                if touch_sl and touch_tp1:
                    exit_reason = "STOP_LOSS"
                    exit_price = min(curr_o, sl) if curr_o < sl else sl
                    exit_date = curr_date
                    holding_days = day_i
                    break
                elif touch_sl:
                    exit_reason = "STOP_LOSS"
                    exit_price = min(curr_o, sl) if curr_o < sl else sl
                    exit_date = curr_date
                    holding_days = day_i
                    break
                elif touch_tp1:
                    exit_reason = "TP1"
                    exit_price = max(curr_o, tp1) if curr_o > tp1 else tp1
                    exit_date = curr_date
                    holding_days = day_i
                    break

                if day_i == max_hold_days:
                    exit_reason = "TIME_EXIT"
                    exit_price = curr_c
                    exit_date = curr_date
                    holding_days = day_i
                    break

            if exit_reason is None:
                last_idx = end_idx - 1
                exit_reason = "HOLDING_END"
                exit_price = float(close_df[ticker].iloc[last_idx])
                exit_date = date_strs[last_idx]
                holding_days = end_idx - (t_idx + 1)

            ret_pct = (exit_price - entry_price) / entry_price * 100.0
            trades.append({
                'return_pct': ret_pct,
                'is_win': ret_pct > 0,
                'exit_reason': exit_reason,
                'holding_days': holding_days
            })

        elif tp_rule == 'partial_trail':
            # 分批出場：觸及 TP1 出場 50%，其餘 50% 啟動保本停損 (entry_price) 並挑戰 TP2
            pos1_sold = False
            pos1_price = 0
            pos1_ret = 0
            pos2_sold = False
            pos2_price = 0
            pos2_ret = 0
            holding_days = 0
            final_reason = "TIME_EXIT"

            for day_i, curr_idx in enumerate(range(t_idx + 1, end_idx), 1):
                curr_date = date_strs[curr_idx]
                curr_o = float(open_df[ticker].iloc[curr_idx])
                curr_h = float(high_df[ticker].iloc[curr_idx])
                curr_l = float(low_df[ticker].iloc[curr_idx])
                curr_c = float(close_df[ticker].iloc[curr_idx])
                if pd.isna(curr_c):
                    continue

                if not pos1_sold:
                    if curr_l <= sl:
                        # 全停損
                        p = min(curr_o, sl) if curr_o < sl else sl
                        r = (p - entry_price) / entry_price * 100.0
                        pos1_sold, pos2_sold = True, True
                        pos1_ret, pos2_ret = r, r
                        final_reason = "STOP_LOSS"
                        holding_days = day_i
                        break
                    elif curr_h >= tp1:
                        # 達標 TP1，賣出一半
                        p1 = max(curr_o, tp1) if curr_o > tp1 else tp1
                        pos1_sold = True
                        pos1_ret = (p1 - entry_price) / entry_price * 100.0
                        final_reason = "TP1_PARTIAL"
                        holding_days = day_i
                        # 防守價提升至進場成本 (保本)
                        sl = entry_price
                else:
                    # 剩餘部位挑戰 TP2 或保本
                    if curr_h >= tp2:
                        p2 = max(curr_o, tp2) if curr_o > tp2 else tp2
                        pos2_sold = True
                        pos2_ret = (p2 - entry_price) / entry_price * 100.0
                        final_reason = "TP2_FULL"
                        holding_days = day_i
                        break
                    elif curr_l <= sl:
                        p2 = min(curr_o, sl) if curr_o < sl else sl
                        pos2_sold = True
                        pos2_ret = (p2 - entry_price) / entry_price * 100.0
                        holding_days = day_i
                        break

                if day_i == max_hold_days:
                    holding_days = day_i
                    if not pos1_sold:
                        pos1_ret = (curr_c - entry_price) / entry_price * 100.0
                    if not pos2_sold:
                        pos2_ret = (curr_c - entry_price) / entry_price * 100.0
                    pos1_sold, pos2_sold = True, True
                    break

            if not pos1_sold:
                pos1_ret = (curr_c - entry_price) / entry_price * 100.0
            if not pos2_sold:
                pos2_ret = (curr_c - entry_price) / entry_price * 100.0

            total_ret = (pos1_ret + pos2_ret) / 2.0
            trades.append({
                'return_pct': total_ret,
                'is_win': total_ret > 0,
                'exit_reason': final_reason,
                'holding_days': holding_days
            })

    if not trades:
        return {'total': 0, 'win_rate': 0, 'avg_ret': 0, 'tp_hits': 0, 'sl_hits': 0, 'pf': 0, 'plr': 0}

    df_t = pd.DataFrame(trades)
    win_cnt = df_t['is_win'].sum()
    total = len(df_t)
    win_rate = win_cnt / total * 100.0
    avg_ret = df_t['return_pct'].mean()
    wins = df_t[df_t['return_pct'] > 0]['return_pct']
    losses = df_t[df_t['return_pct'] < 0]['return_pct']
    avg_win = wins.mean() if len(wins) > 0 else 0
    avg_loss = losses.mean() if len(losses) > 0 else 0
    plr = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    pf = wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 0
    tp_hits = len(df_t[df_t['exit_reason'].str.contains('TP')])
    sl_hits = len(df_t[df_t['exit_reason'] == 'STOP_LOSS'])

    return {
        'total': total,
        'win_rate': round(win_rate, 2),
        'avg_ret': round(avg_ret, 2),
        'tp_hits': tp_hits,
        'sl_hits': sl_hits,
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_loss_ratio': round(plr, 2),
        'profit_factor': round(pf, 2),
        'avg_days': round(df_t['holding_days'].mean(), 1)
    }

def run_tests():
    print("[*] 正在產生全歷史基礎信號母池...")
    raw_signals = []

    for d_str in eval_dates:
        t_idx = date_to_idx[d_str]
        if t_idx < 25:
            continue

        for ticker in available_tickers:
            code = ticker_to_code[ticker]
            chip = chips_dict.get((d_str, code))
            if not chip:
                continue

            close_val = close_df[ticker].iloc[t_idx]
            vol_val = volume_df[ticker].iloc[t_idx]
            open_val = open_df[ticker].iloc[t_idx]
            high_val = high_df[ticker].iloc[t_idx]
            low_val = low_df[ticker].iloc[t_idx]

            if pd.isna(close_val) or pd.isna(vol_val) or vol_val <= 0 or close_val <= 0:
                continue

            vol_lots = int(vol_val // 1000)
            if vol_lots < 1000:
                continue

            tot_lots = int(chip['total'] // 1000)
            t_lots = int(chip['trust'] // 1000)
            inst_ratio = (tot_lots / vol_lots * 100.0) if vol_lots > 0 else 0.0

            if tot_lots <= 0 or inst_ratio < 1.5 or t_lots < -500:
                continue

            # K線型態
            amp = max(high_val - low_val, 0.001)
            lower_shadow = max(min(open_val, close_val) - low_val, 0.0)
            ls_ratio = lower_shadow / amp
            is_bullish = close_val >= open_val
            not_closed_at_low = close_val > (low_val + amp * 0.10)
            is_reversal = (ls_ratio >= 0.25) or is_bullish or not_closed_at_low

            # 嚴格K線型態 (紅K 或 下影線 >= 30%)
            is_strict_candle = is_bullish or (ls_ratio >= 0.30)

            sub_series = close_df[ticker].iloc[: t_idx + 1].dropna()
            if len(sub_series) < 20:
                continue
            hist_closes = sub_series.iloc[-20:].values
            ma20 = float(np.mean(hist_closes))
            std20 = float(np.std(hist_closes, ddof=0))
            lb = ma20 - 2.0 * std20
            ub = ma20 + 2.0 * std20
            if lb <= 0:
                continue

            dist_lb_pct = (close_val - lb) / lb * 100.0
            if not (-5.0 <= dist_lb_pct <= 1.0):
                continue

            upside_pct = (ma20 - close_val) / close_val * 100.0
            if upside_pct < 2.0:
                continue

            # 20MA 斜率 (近5日變化率)
            if len(sub_series) >= 25:
                ma20_5d_ago = float(np.mean(sub_series.iloc[-25:-5].values))
                ma20_slope_5d = (ma20 - ma20_5d_ago) / ma20_5d_ago * 100.0
            else:
                ma20_slope_5d = 0.0

            raw_signals.append({
                'signal_date': d_str,
                'signal_idx': t_idx,
                'ticker': ticker,
                'code': code,
                'close': close_val,
                'open_val': open_val,
                'high_val': high_val,
                'low_val': low_val,
                'tp1': round(ma20, 2),
                'tp2': round(ub, 2),
                'sl_fixed': round(lb * 0.95, 2),
                'upside_pct': upside_pct,
                'ma20_slope_5d': ma20_slope_5d,
                'inst_ratio': inst_ratio,
                'is_reversal': is_reversal,
                'is_strict_candle': is_strict_candle
            })

    print(f"[OK] 母池收集完畢，共 {len(raw_signals)} 筆原始訊號。開始回測各優化方案...\n")

    # 1. 原版基準 (Baseline)
    s_base = [s for s in raw_signals if s['is_reversal']]
    res_base = evaluate_signals(s_base, sl_rule='fixed_lb', tp_rule='tp1_full')

    # 2. 方案 A：空間上限 <= 5.5% (穩健通道走平)
    s_a = [s for s in s_base if 2.0 <= s['upside_pct'] <= 5.5 and s['ma20_slope_5d'] >= -2.0]
    res_a = evaluate_signals(s_a, sl_rule='fixed_lb', tp_rule='tp1_full')

    # 3. 方案 B：空間上限 <= 6.0% + 嚴格K線 (紅K或下影線>=30%)
    s_b = [s for s in s_base if 2.0 <= s['upside_pct'] <= 6.0 and s['ma20_slope_5d'] >= -2.0 and s['is_strict_candle']]
    res_b = evaluate_signals(s_b, sl_rule='fixed_lb', tp_rule='tp1_full')

    # 4. 方案 C：方案 B + 次日開盤不破前低過濾 (Next-day Confirmation: Open_{T+1} > Low_T)
    s_c = []
    for s in s_b:
        t_idx = s['signal_idx']
        ticker = s['ticker']
        if t_idx + 1 < len(date_strs):
            next_open = float(open_df[ticker].iloc[t_idx + 1])
            if next_open >= s['low_val']:
                s_c.append(s)
    res_c = evaluate_signals(s_c, sl_rule='fixed_lb', tp_rule='tp1_full')

    # 5. 方案 D：方案 C + 分批停利50%與保本機制 (Partial TP1 + Breakeven + TP2)
    res_d = evaluate_signals(s_c, sl_rule='fixed_lb', tp_rule='partial_trail')

    # 6. 方案 E：高敏捷型 (空間上限 5.5% + 嚴格K棒 + 次日確認 + 分批保本)
    s_e = []
    for s in s_a:
        if s['is_strict_candle']:
            t_idx = s['signal_idx']
            ticker = s['ticker']
            if t_idx + 1 < len(date_strs):
                next_open = float(open_df[ticker].iloc[t_idx + 1])
                if next_open >= s['low_val']:
                    s_e.append(s)
    res_e = evaluate_signals(s_e, sl_rule='fixed_lb', tp_rule='partial_trail')

    summary_table = pd.DataFrame([
        {'優化方案名稱': '【基準】原版策略', **res_base},
        {'優化方案名稱': '【優化1】通道走平濾網 (空間<=5.5% 排除墜落開口)', **res_a},
        {'優化方案名稱': '【優化2】空間<=6.0% ＋ 嚴格落底K線確認', **res_b},
        {'優化方案名稱': '【優化3】優化2 ＋ 次日開盤不破前低確認', **res_c},
        {'優化方案名稱': '【優化4 旗艦】優化3 ＋ 分批停利50%與保本機制', **res_d},
        {'優化方案名稱': '【優化5 超高勝率】空間<=5.5% ＋ 嚴格K棒 ＋ 次日確認 ＋ 分批保本', **res_e},
    ])

    print("="*95)
    print("【策略一進出場條件全面優化方案對照成果】")
    print("="*95)
    cols = ['優化方案名稱', 'total', 'win_rate', 'tp_hits', 'sl_hits', 'profit_loss_ratio', 'profit_factor', 'avg_ret', 'avg_days']
    print(summary_table[cols].to_string(index=False))
    print("="*95)

if __name__ == '__main__':
    run_tests()
