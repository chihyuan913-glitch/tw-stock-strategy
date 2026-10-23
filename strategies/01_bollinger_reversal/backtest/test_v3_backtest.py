__test__ = False
import sys
import os
import pickle
import sqlite3
import pandas as pd
import numpy as np

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'ohlcv.pkl'), 'rb') as f:
    ohlcv = pickle.load(f)

conn = sqlite3.connect(os.path.join(BASE_DIR, 't86.sqlite'))
cur = conn.cursor()
cur.execute('SELECT date, code, foreign_net, trust_net, dealer_net, total_net FROM t86_trades')
chips_dict = {(r[0], r[1]): {'foreign': r[2], 'trust': r[3], 'dealer': r[4], 'total': r[5]} for r in cur.fetchall()}
conn.close()

close_df = ohlcv['Close']
open_df = ohlcv['Open']
high_df = ohlcv['High']
low_df = ohlcv['Low']
volume_df = ohlcv['Volume']

date_strs = [d.strftime('%Y%m%d') for d in close_df.index]
date_to_idx = {d: i for i, d in enumerate(date_strs)}
eval_dates = [d for d in date_strs if '20250101' <= d <= '20260930']
available_tickers = list(close_df.columns)
ticker_to_code = {t: t.split('.')[0] for t in available_tickers}

# Test V3.0 Screening & Backtest
v3_signals = []

for d_str in eval_dates:
    t_idx = date_to_idx[d_str]
    if t_idx < 25:
        continue

    for ticker in available_tickers:
        code = ticker_to_code[ticker]
        chip = chips_dict.get((d_str, code))
        if not chip:
            continue

        c_p = close_df[ticker].iloc[t_idx]
        v_p = volume_df[ticker].iloc[t_idx]
        o_p = open_df[ticker].iloc[t_idx]
        h_p = high_df[ticker].iloc[t_idx]
        l_p = low_df[ticker].iloc[t_idx]

        if pd.isna(c_p) or pd.isna(v_p) or v_p <= 0 or c_p <= 0:
            continue
        vol_lots = int(v_p // 1000)
        if vol_lots < 800:
            continue

        tot_lots = int(chip['total'] // 1000)
        t_lots = int(chip['trust'] // 1000)
        f_lots = int(chip['foreign'] // 1000)
        inst_ratio = (tot_lots / vol_lots * 100.0) if vol_lots > 0 else 0.0

        if tot_lots <= 0 or inst_ratio < 1.0 or t_lots < -500:
            continue

        sub_s = close_df[ticker].iloc[: t_idx + 1].dropna()
        if len(sub_s) < 20:
            continue
        hist_c = sub_s.iloc[-20:].values
        ma20 = float(np.mean(hist_c))
        std20 = float(np.std(hist_c, ddof=0))
        lb = ma20 - 2.0 * std20
        ub = ma20 + 2.0 * std20
        if lb <= 0 or (ub - lb) <= 0:
            continue

        bw = (ub - lb) / ma20 * 100.0
        pct_b = (c_p - lb) / (ub - lb)
        dist_lb = (c_p - lb) / lb * 100.0
        upside = (ma20 - c_p) / c_p * 100.0

        if len(sub_s) >= 25:
            ma20_5d_ago = float(np.mean(sub_s.iloc[-25:-5].values))
            slope_5d = (ma20 - ma20_5d_ago) / ma20_5d_ago * 100.0
        else:
            slope_5d = 0.0

        amp = max(h_p - l_p, 0.001)
        lower_shadow = max(min(o_p, c_p) - l_p, 0.0)
        ls_ratio = lower_shadow / amp
        is_bullish = c_p >= o_p
        has_reversal_candle = is_bullish or (ls_ratio >= 0.25)

        # Tier-1 精選條件 (嚴格防墜落)
        is_tier1 = (
            vol_lots >= 1000 and
            -5.0 <= dist_lb <= 1.0 and
            -0.05 <= pct_b <= 0.15 and
            2.0 <= upside <= 6.5 and
            bw <= 22.0 and
            slope_5d >= -2.2 and
            inst_ratio >= 1.5 and
            has_reversal_candle
        )

        if is_tier1:
            v3_signals.append({
                'd': d_str, 'idx': t_idx, 'ticker': ticker, 'code': code,
                'close': c_p, 'open': o_p, 'high': h_p, 'low': l_p,
                'tp1': round(ma20, 2), 'tp2': round(ub, 2),
                'sl': round(max(lb * 0.96, l_p * 0.99), 2),
                'upside': upside
            })

print(f'V3.0 Tier-1 Signals count across 422 days: {len(v3_signals)}')

trades = []
for s in v3_signals:
    t_idx = s['idx']
    ticker = s['ticker']
    if t_idx + 1 >= len(date_strs):
        continue
    entry_p = float(open_df[ticker].iloc[t_idx + 1])
    if pd.isna(entry_p) or entry_p <= 0:
        entry_p = s['close']

    # Next-day open confirmation: Open_{T+1} >= Low_T
    if entry_p < s['low']:
        continue

    tp1 = s['tp1']
    tp2 = s['tp2']
    sl = s['sl']
    end_idx = min(t_idx + 1 + 20, len(date_strs))

    p1_sold, p2_sold = False, False
    p1_ret, p2_ret = 0, 0
    holding_days = 0

    for day_i, c_i in enumerate(range(t_idx + 1, end_idx), 1):
        c_o = float(open_df[ticker].iloc[c_i])
        c_h = float(high_df[ticker].iloc[c_i])
        c_l = float(low_df[ticker].iloc[c_i])
        c_c = float(close_df[ticker].iloc[c_i])
        if pd.isna(c_c):
            continue

        if not p1_sold:
            if c_l <= sl:
                p = min(c_o, sl) if c_o < sl else sl
                r = (p - entry_p) / entry_p * 100.0
                p1_sold, p2_sold = True, True
                p1_ret, p2_ret = r, r
                holding_days = day_i
                break
            elif c_h >= tp1:
                p1 = max(c_o, tp1) if c_o > tp1 else tp1
                p1_sold = True
                p1_ret = (p1 - entry_p) / entry_p * 100.0
                sl = entry_p  # 啟動保本
        else:
            if c_h >= tp2:
                p2 = max(c_o, tp2) if c_o > tp2 else tp2
                p2_sold = True
                p2_ret = (p2 - entry_p) / entry_p * 100.0
                holding_days = day_i
                break
            elif c_l <= sl:
                p2 = min(c_o, sl) if c_o < sl else sl
                p2_sold = True
                p2_ret = (p2 - entry_p) / entry_p * 100.0
                holding_days = day_i
                break

        if day_i == 20:
            holding_days = 20
            if not p1_sold:
                p1_ret = (c_c - entry_p) / entry_p * 100.0
            if not p2_sold:
                p2_ret = (c_c - entry_p) / entry_p * 100.0
            p1_sold, p2_sold = True, True
            break

    if not p1_sold:
        p1_ret = (c_c - entry_p) / entry_p * 100.0
    if not p2_sold:
        p2_ret = (c_c - entry_p) / entry_p * 100.0

    tot_ret = (p1_ret + p2_ret) / 2.0
    trades.append({'return_pct': tot_ret, 'is_win': tot_ret > 0, 'days': holding_days})

df_tr = pd.DataFrame(trades)
wins = df_tr[df_tr['is_win']]
losses = df_tr[~df_tr['is_win']]
win_rate = len(wins) / len(df_tr) * 100.0
avg_ret = df_tr['return_pct'].mean()
avg_win = wins['return_pct'].mean()
avg_loss = losses['return_pct'].mean()
plr = abs(avg_win / avg_loss)
pf = wins['return_pct'].sum() / abs(losses['return_pct'].sum())

print("="*60)
print(f"【V3.0 升級版 422 交易日回測實證】")
print(f"總執行交易數: {len(df_tr)} 筆")
print(f"勝率 (Win Rate): {win_rate:.2f}%")
print(f"平均報酬率: {avg_ret:+.2f}%")
print(f"平均獲利: {avg_win:+.2f}% vs 平均虧損: {avg_loss:.2f}%")
print(f"盈虧比 (Profit/Loss Ratio): {plr:.2f}")
print(f"獲利因子 (Profit Factor): {pf:.2f}")
print(f"平均持股天數: {df_tr['days'].mean():.1f} 天")
print("="*60)
