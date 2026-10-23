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
ticker_indices = list(range(len(available_tickers)))
codes = [ticker_to_code[t] for t in available_tickers]

# 1. 向量化計算指標並轉換為純 numpy 陣列
print("[*] 正在計算技術指標矩陣...", flush=True)
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
reversal_candle_df = (close_df >= open_df) | (ls_ratio_df >= 0.25)

# 轉換為 numpy 陣列以達到微秒級存取
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
rev_arr = reversal_candle_df.values

N_DATES, N_TICKERS = close_arr.shape
print(f"[*] 數據矩陣維度: {N_DATES} 天 x {N_TICKERS} 檔股票", flush=True)

def run_simulation(strategy_name, mode="v3"):
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
            
            ma20 = ma20_arr[t_idx, tick_idx]
            lb = lb_arr[t_idx, tick_idx]
            ub = ub_arr[t_idx, tick_idx]
            if np.isnan(lb) or lb <= 0:
                continue
                
            dist_lb = dist_lb_arr[t_idx, tick_idx]
            upside = upside_arr[t_idx, tick_idx]
            
            if mode == "v1":
                # V1.0 原版
                if not (vol_lots >= 1000 and tot_lots > 0 and dist_lb <= 1.5 and upside >= 2.0):
                    continue
            else:
                # V3.0 機構升級版
                bw = bw_arr[t_idx, tick_idx]
                pct_b = pct_b_arr[t_idx, tick_idx]
                slope_5d = slope_5d_arr[t_idx, tick_idx]
                has_rev = rev_arr[t_idx, tick_idx]
                
                if not (
                    vol_lots >= 1000 and
                    -5.0 <= dist_lb <= 1.0 and
                    -0.05 <= pct_b <= 0.15 and
                    2.0 <= upside <= 6.5 and
                    bw <= 22.0 and
                    slope_5d >= -2.2 and
                    inst_ratio >= 1.5 and
                    t_lots >= -500 and
                    has_rev
                ):
                    continue
                    
            o_p = open_arr[t_idx, tick_idx]
            h_p = high_arr[t_idx, tick_idx]
            l_p = low_arr[t_idx, tick_idx]
            
            signals.append({
                'd': d_str, 'idx': t_idx, 'tick_idx': tick_idx, 'ticker': available_tickers[tick_idx],
                'close': c_p, 'open': o_p, 'high': h_p, 'low': l_p,
                'tp1': round(ma20, 2), 'tp2': round(ub, 2),
                'sl': round(max(lb * 0.96, l_p * 0.99), 2),
                'upside': upside
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
            
        # 次日跳空破昨低保護 (Next-day Confirmation)
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
                    sl = entry_p # 啟動保本停損
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
        print(f"[{strategy_name}] 無任何交易", flush=True)
        return
        
    wins = df_tr[df_tr['is_win']]
    losses = df_tr[~df_tr['is_win']]
    win_rate = len(wins) / len(df_tr) * 100.0
    avg_ret = df_tr['return_pct'].mean()
    avg_win = wins['return_pct'].mean() if len(wins) > 0 else 0.0
    avg_loss = losses['return_pct'].mean() if len(losses) > 0 else 0.0
    plr = abs(avg_win / avg_loss) if avg_loss != 0 else 999
    pf = wins['return_pct'].sum() / abs(losses['return_pct'].sum()) if len(losses) > 0 and losses['return_pct'].sum() != 0 else 999
    
    print("=" * 65, flush=True)
    print(f"【{strategy_name} 回測結果 (114/01/01 ~ 115/09/30 共 422 交易日)】", flush=True)
    print(f"總訊號數: {len(signals)} | 實戰執行交易數: {len(df_tr)} 筆", flush=True)
    print(f"勝率 (Win Rate): {win_rate:.2f}% (獲利: {len(wins)} 筆, 虧損: {len(losses)} 筆)", flush=True)
    print(f"平均單筆報酬率: {avg_ret:+.2f}%", flush=True)
    print(f"平均獲利筆報酬: {avg_win:+.2f}% | 平均虧損筆報酬: {avg_loss:.2f}%", flush=True)
    print(f"盈虧比 (Profit/Loss Ratio): {plr:.2f}", flush=True)
    print(f"獲利因子 (Profit Factor): {pf:.2f}", flush=True)
    print(f"平均持股天數: {df_tr['days'].mean():.1f} 天", flush=True)
    print("=" * 65, flush=True)

print("[*] 開始執行雙策略回測評估...", flush=True)
run_simulation("V1.0 原版策略 (無防墜落/無%b/無開口過濾)", mode="v1")
run_simulation("V3.0 機構旗艦版 (4+1黃金維度 + %b + BandWidth + 實質落底K)", mode="v3")
