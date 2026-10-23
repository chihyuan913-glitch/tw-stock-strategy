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
chips_dict = {(r[0], r[1]): (r[2], r[3], r[4], r[5]) for r in cur.fetchall()}
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
codes = [ticker_to_code[t] for t in available_tickers]

# 指標矩陣
ma20_df = close_df.rolling(20, min_periods=20).mean()
std20_df = close_df.rolling(20, min_periods=20).std(ddof=0)
lb_df = ma20_df - 2.0 * std20_df
ub_df = ma20_df + 2.0 * std20_df
bw_df = (ub_df - lb_df) / ma20_df * 100.0
pct_b_df = (close_df - lb_df) / (ub_df - lb_df)
dist_lb_df = (close_df - lb_df) / lb_df * 100.0
upside_df = (ma20_df - close_df) / close_df * 100.0
ma20_5d_df = ma20_df.shift(5)
slope_5d_df = (ma20_df - ma20_5d_df) / ma20_5d_df * 100.0

amp_df = np.maximum(high_df - low_df, 0.001)
lower_shadow_df = np.maximum(np.minimum(open_df, close_df) - low_df, 0.0)
ls_ratio_df = lower_shadow_df / amp_df

close_arr = close_df.values
open_arr = open_df.values
high_arr = high_df.values
low_arr = low_df.values
vol_arr = volume_df.values
ma20_arr = ma20_df.values
lb_arr = lb_df.values
ub_arr = ub_df.values
bw_arr = bw_df.values
pct_b_arr = pct_b_df.values
dist_lb_arr = dist_lb_df.values
upside_arr = upside_df.values
slope_5d_arr = slope_5d_df.values
ls_ratio_arr = ls_ratio_df.values

N_DATES, N_TICKERS = close_arr.shape

def evaluate_combination(name, min_inst_ratio=1.5, max_bw=22.0, min_slope=-2.2, max_upside=6.5, ls_th=0.25, tp_half=False, require_trust_positive=False):
    signals = []
    for d_str in eval_dates:
        t_idx = date_to_idx[d_str]
        if t_idx < 25:
            continue
            
        for tick_idx in range(N_TICKERS):
            code = codes[tick_idx]
            chip = chips_dict.get((d_str, code))
            if not chip:
                continue
                
            c_p = close_arr[t_idx, tick_idx]
            v_p = vol_arr[t_idx, tick_idx]
            if np.isnan(c_p) or np.isnan(v_p) or v_p <= 0 or c_p <= 0:
                continue
                
            vol_lots = int(v_p // 1000)
            tot_lots = int(chip[3] // 1000)
            t_lots = int(chip[1] // 1000)
            inst_ratio = (tot_lots / vol_lots * 100.0) if vol_lots > 0 else 0.0
            
            if vol_lots < 1000 or inst_ratio < min_inst_ratio or t_lots < -500:
                continue
            if require_trust_positive and t_lots <= 0:
                continue
                
            ma20 = ma20_arr[t_idx, tick_idx]
            lb = lb_arr[t_idx, tick_idx]
            ub = ub_arr[t_idx, tick_idx]
            if np.isnan(lb) or lb <= 0:
                continue
                
            dist_lb = dist_lb_arr[t_idx, tick_idx]
            pct_b = pct_b_arr[t_idx, tick_idx]
            upside = upside_arr[t_idx, tick_idx]
            bw = bw_arr[t_idx, tick_idx]
            slope_5d = slope_5d_arr[t_idx, tick_idx]
            
            o_p = open_arr[t_idx, tick_idx]
            h_p = high_arr[t_idx, tick_idx]
            l_p = low_arr[t_idx, tick_idx]
            
            ls_r = ls_ratio_arr[t_idx, tick_idx]
            has_rev = (c_p >= o_p) or (ls_r >= ls_th)
            
            if not (
                -5.0 <= dist_lb <= 1.0 and
                -0.05 <= pct_b <= 0.15 and
                2.0 <= upside <= max_upside and
                bw <= max_bw and
                slope_5d >= min_slope and
                has_rev
            ):
                continue
                
            # TP1 計算：若 tp_half 為 True，則以 (c_p + ma20)/2 (反彈中位) 為 TP1，ma20 為 TP2
            if tp_half:
                target_tp1 = round((c_p + ma20) / 2.0, 2)
                target_tp2 = round(ma20, 2)
            else:
                target_tp1 = round(ma20, 2)
                target_tp2 = round(ub, 2)
                
            signals.append({
                'd': d_str, 'idx': t_idx, 'tick_idx': tick_idx,
                'close': c_p, 'open': o_p, 'high': h_p, 'low': l_p,
                'tp1': target_tp1, 'tp2': target_tp2,
                'sl': round(max(lb * 0.96, l_p * 0.99), 2),
            })
            
    trades = []
    for s in signals:
        t_idx = s['idx']
        tick_idx = s['tick_idx']
        if t_idx + 1 >= N_DATES:
            continue
            
        entry_p = open_arr[t_idx + 1, tick_idx]
        if np.isnan(entry_p) or entry_p <= 0:
            entry_p = s['close']
        if entry_p < s['low']:
            continue
            
        tp1 = s['tp1']
        tp2 = s['tp2']
        sl = s['sl']
        end_idx = min(t_idx + 1 + 20, N_DATES)
        
        p1_sold, p2_sold = False, False
        p1_ret, p2_ret = 0, 0
        holding_days = 0
        
        for day_i, c_i in enumerate(range(t_idx + 1, end_idx), 1):
            c_o = open_arr[c_i, tick_idx]
            c_h = high_arr[c_i, tick_idx]
            c_l = low_arr[c_i, tick_idx]
            c_c = close_arr[c_i, tick_idx]
            if np.isnan(c_c):
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
                    sl = entry_p
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
    if len(df_tr) == 0:
        return
        
    wins = df_tr[df_tr['is_win']]
    losses = df_tr[~df_tr['is_win']]
    win_rate = len(wins) / len(df_tr) * 100.0
    avg_ret = df_tr['return_pct'].mean()
    avg_win = wins['return_pct'].mean() if len(wins) > 0 else 0.0
    avg_loss = losses['return_pct'].mean() if len(losses) > 0 else 0.0
    plr = abs(avg_win / avg_loss) if avg_loss != 0 else 999
    pf = wins['return_pct'].sum() / abs(losses['return_pct'].sum()) if len(losses) > 0 and losses['return_pct'].sum() != 0 else 999
    
    print(f"| {name:<35} | 交易: {len(df_tr):>3} | 勝率: {win_rate:>5.2f}% | 均報: {avg_ret:>+5.2f}% | 盈虧比: {plr:>4.2f} | 獲利因子: {pf:>4.2f} | 天數: {df_tr['days'].mean():>3.1f} |", flush=True)

print("=" * 105, flush=True)
print(f"| {'方案名稱':<35} | {'交易數':<6} | {'勝率':<8} | {'平均報酬':<8} | {'盈虧比':<8} | {'獲利因子':<8} | {'持股天':<6} |", flush=True)
print("=" * 105, flush=True)

evaluate_combination("1. 基準 V3.0 (TP1=20MA, TP2=UB)", min_inst_ratio=1.5, max_bw=22.0, min_slope=-2.2, max_upside=6.5)
evaluate_combination("2. 法人佔比提高 (inst_ratio >= 2.5%)", min_inst_ratio=2.5, max_bw=22.0, min_slope=-2.2, max_upside=6.5)
evaluate_combination("3. 法人佔比提高 (inst_ratio >= 3.5%)", min_inst_ratio=3.5, max_bw=22.0, min_slope=-2.2, max_upside=6.5)
evaluate_combination("4. 強制投信買超 (t_lots > 0)", min_inst_ratio=1.5, max_bw=22.0, min_slope=-2.2, max_upside=6.5, require_trust_positive=True)
evaluate_combination("5. 縮減帶寬 (BandWidth <= 18%)", min_inst_ratio=1.5, max_bw=18.0, min_slope=-2.2, max_upside=6.5)
evaluate_combination("6. 嚴格趨勢平緩 (Slope5D >= -1.5%)", min_inst_ratio=1.5, max_bw=22.0, min_slope=-1.5, max_upside=6.5)
evaluate_combination("7. 強化下影線濾網 (LS >= 33%)", min_inst_ratio=1.5, max_bw=22.0, min_slope=-2.2, max_upside=6.5, ls_th=0.33)
evaluate_combination("8. 分段停利: TP1=半中軌, TP2=20MA", min_inst_ratio=1.5, max_bw=22.0, min_slope=-2.2, max_upside=6.5, tp_half=True)
evaluate_combination("9. 組合優化: 法人>=2.0% + TP1=半中軌", min_inst_ratio=2.0, max_bw=20.0, min_slope=-2.0, max_upside=6.0, tp_half=True)
evaluate_combination("10. 旗艦組合: 法人>=2.5% + 帶寬<=20%", min_inst_ratio=2.5, max_bw=20.0, min_slope=-1.8, max_upside=6.0)
print("=" * 105, flush=True)
