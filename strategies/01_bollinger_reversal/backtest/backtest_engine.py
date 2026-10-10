#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略一：台股布林通道下軌超跌反轉選股策略 V2.0 歷史回測引擎
回測區間：民國 114 年 1 月 1 日 (2025-01-01) 到 115 年 9 月底 (2026-09-30)

選股條件：
1. 日成交量 >= 1000 張
2. 股價位於布林通道下軌 1% 之內或是跌破 5% 之內 (-5.0% <= (Close - LB) / LB <= +1.0%)
3. 三大法人合計買超 > 0，且買超佔比 >= 1.5%，投信無恐慌拋售 (投信賣超 <= 500 張)
4. 型態止跌濾網：留下影線 (>=25%)、收紅K 或 未收在最低
5. 盈虧比空間：距布林中軌 (20MA) 潛在反彈空間 >= 2.0%
6. 智慧評分與星級評等

交易執行與風控規則：
- 進場：訊號次日開盤價進場 (Next Open)；同時對比訊號日收盤價基準 (Signal Close)
- 停利 TP1：布林中軌 (20MA)
- 停利 TP2：布林上軌 (Upper Band)
- 停損 SL：跌破布林下軌 5% (LB * 0.95)
- 最長持有期間：20 個交易日 (逾期以當日收盤平倉)
"""

import sys
import os
import sqlite3
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
DB_PATH = os.path.join(BASE_DIR, "t86.sqlite")
OHLCV_PATH = os.path.join(BASE_DIR, "ohlcv.pkl")
OUTPUT_CSV = os.path.join(BASE_DIR, "backtest_trades.csv")
OUTPUT_SUMMARY_JSON = os.path.join(BASE_DIR, "backtest_summary.json")

def calculate_candlestick_features(open_p, high_p, low_p, close_p):
    amplitude = max(high_p - low_p, 0.001)
    lower_shadow = max(min(open_p, close_p) - low_p, 0.0)
    lower_shadow_ratio = lower_shadow / amplitude
    is_bullish = close_p >= open_p
    not_closed_at_low = close_p > (low_p + amplitude * 0.10)
    is_reversal = (lower_shadow_ratio >= 0.25) or is_bullish or not_closed_at_low

    pattern_desc = []
    if lower_shadow_ratio >= 0.35:
        pattern_desc.append("長下影線")
    elif lower_shadow_ratio >= 0.20:
        pattern_desc.append("帶下影線")
    if is_bullish:
        pattern_desc.append("收紅K")
    else:
        pattern_desc.append("收黑K")

    return {
        'lower_shadow_ratio': lower_shadow_ratio,
        'is_bullish': is_bullish,
        'is_reversal': is_reversal,
        'pattern_desc': "＋".join(pattern_desc)
    }

def calculate_score(inst_ratio, foreign_lots, trust_lots, candle_feat, upside_pct):
    if inst_ratio >= 10.0:
        s_chip = 25
    elif inst_ratio >= 5.0:
        s_chip = 20
    elif inst_ratio >= 2.5:
        s_chip = 15
    else:
        s_chip = 10

    is_dual = (foreign_lots > 0) and (trust_lots > 0)
    if is_dual:
        s_purity = 25
    elif trust_lots > 0:
        s_purity = 20
    elif foreign_lots > 0 and trust_lots == 0:
        s_purity = 15
    else:
        s_purity = 10

    ls_ratio = candle_feat['lower_shadow_ratio']
    is_bull = candle_feat['is_bullish']
    if is_bull and ls_ratio >= 0.30:
        s_candle = 25
    elif ls_ratio >= 0.30 or (is_bull and ls_ratio >= 0.15):
        s_candle = 20
    elif is_bull or ls_ratio >= 0.20:
        s_candle = 15
    elif candle_feat['is_reversal']:
        s_candle = 10
    else:
        s_candle = 0

    if upside_pct >= 6.0:
        s_upside = 25
    elif upside_pct >= 4.0:
        s_upside = 20
    elif upside_pct >= 2.5:
        s_upside = 15
    elif upside_pct >= 1.0:
        s_upside = 10
    else:
        s_upside = 5

    total_score = s_chip + s_purity + s_candle + s_upside

    if total_score >= 80:
        stars = "★★★★★"
    elif total_score >= 65:
        stars = "★★★★☆"
    elif total_score >= 50:
        stars = "★★★☆☆"
    else:
        stars = "★★☆☆☆"

    return total_score, stars, is_dual

def run_backtest():
    print("[*] 正在載入回測數據...")
    if not os.path.exists(OHLCV_PATH):
        print(f"[X] 找不到股價歷史檔: {OHLCV_PATH}，請先執行 download_data.py")
        return
    if not os.path.exists(DB_PATH):
        print(f"[X] 找不到籌碼資料庫: {DB_PATH}，請先執行 download_data.py")
        return

    with open(OHLCV_PATH, 'rb') as f:
        ohlcv_raw = pickle.load(f)

    # 籌碼資料讀取至記憶體 dict
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
    print(f"[OK] 成功載入 {len(chips_dict)} 筆三大法人歷史籌碼資料。")

    # 整理 OHLCV 結構
    # 支援 multi-index 或 tuple 列
    open_df = ohlcv_raw['Open']
    high_df = ohlcv_raw['High']
    low_df = ohlcv_raw['Low']
    close_df = ohlcv_raw['Close']
    volume_df = ohlcv_raw['Volume']

    # 日期索引轉換為 YYYYMMDD
    date_strs = [d.strftime('%Y%m%d') for d in close_df.index]
    date_to_idx = {d: i for i, d in enumerate(date_strs)}

    # 回測區間過濾: 20250101 ~ 20260930
    eval_dates = [d for d in date_strs if '20250101' <= d <= '20260930']
    print(f"[*] 回測交易日數: {len(eval_dates)} 日 ({eval_dates[0]} ~ {eval_dates[-1]})")

    # 取得可用個股代碼
    available_tickers = list(close_df.columns)
    ticker_to_code = {}
    for t in available_tickers:
        code = t.split('.')[0]
        ticker_to_code[t] = code

    signals = []

    STOCK_NAMES_PATH = os.path.join(BASE_DIR, "stock_names.json")
    stock_names = {}
    if os.path.exists(STOCK_NAMES_PATH):
        try:
            import json
            with open(STOCK_NAMES_PATH, 'r', encoding='utf-8') as f:
                stock_names = json.load(f)
        except Exception:
            pass

    print("[*] 正在進行逐日策略篩選...")
    for d_str in eval_dates:
        t_idx = date_to_idx[d_str]
        if t_idx < 20:
            continue  # 前 20 日供布林通道預熱

        for ticker in available_tickers:
            code = ticker_to_code[ticker]
            # 檢查籌碼是否存在
            chip = chips_dict.get((d_str, code))
            if not chip:
                continue

            # 價量取值
            close_val = close_df[ticker].iloc[t_idx]
            vol_val = volume_df[ticker].iloc[t_idx]
            open_val = open_df[ticker].iloc[t_idx]
            high_val = high_df[ticker].iloc[t_idx]
            low_val = low_df[ticker].iloc[t_idx]

            if pd.isna(close_val) or pd.isna(vol_val) or vol_val <= 0 or close_val <= 0:
                continue

            # 條件 1: 日成交量 >= 1000 張 (1,000,000 股)
            vol_lots = int(vol_val // 1000)
            if vol_lots < 1000:
                continue

            tot_lots = int(chip['total'] // 1000)
            f_lots = int(chip['foreign'] // 1000)
            t_lots = int(chip['trust'] // 1000)
            d_lots = int(chip['dealer'] // 1000)

            # 條件 3: 三大法人合計買超 > 0 且 買超佔比 >= 1.5%
            inst_ratio = (tot_lots / vol_lots * 100.0) if vol_lots > 0 else 0.0
            if tot_lots <= 0 or inst_ratio < 1.5:
                continue

            # 條件 3(副): 投信無恐慌拋售 (投信賣超 <= 500 張)
            if t_lots < -500:
                continue

            # 條件 4: K線止跌型態
            candle_feat = calculate_candlestick_features(open_val, high_val, low_val, close_val)
            if not candle_feat['is_reversal']:
                continue

            # 條件 2 & 5: 計算 20 日布林通道與反彈空間
            # 取過去 20 日 Close (含當日)
            sub_series = close_df[ticker].iloc[: t_idx + 1].dropna()
            if len(sub_series) < 20:
                continue
            hist_closes = sub_series.iloc[-20:].values

            ma20 = float(np.mean(hist_closes))
            std20 = float(np.std(hist_closes, ddof=0))
            lower_band = ma20 - 2.0 * std20
            upper_band = ma20 + 2.0 * std20

            if lower_band <= 0:
                continue

            dist_lb_pct = (close_val - lower_band) / lower_band * 100.0
            # 條件 2: 位於布林通道下軌 1% 之內或是跌破 5% 之內
            if not (-5.0 <= dist_lb_pct <= 1.0):
                continue

            # 條件 5: 距布林中軌反彈空間 >= 2.0%
            upside_pct = (ma20 - close_val) / close_val * 100.0
            if upside_pct < 2.0:
                continue

            # 智慧評分
            score, stars, is_dual = calculate_score(
                inst_ratio=inst_ratio,
                foreign_lots=f_lots,
                trust_lots=t_lots,
                candle_feat=candle_feat,
                upside_pct=upside_pct
            )

            # 四大防線設定
            p_entry = round(float(close_val), 2)
            p_addon = round(float(max(high_val, close_val * 1.025)), 2)
            p_tp1 = round(float(ma20), 2)
            p_tp2 = round(float(upper_band), 2)
            p_sl = round(float(lower_band * 0.95), 2)

            signals.append({
                'signal_date': d_str,
                'signal_idx': t_idx,
                'ticker': ticker,
                'code': code,
                'name': stock_names.get(code, f'台股_{code}'),
                'close': p_entry,
                'entry_price_ref': p_entry,
                'addon_price': p_addon,
                'tp1': p_tp1,
                'tp2': p_tp2,
                'sl': p_sl,
                'lower_band': round(lower_band, 2),
                'middle_band': round(ma20, 2),
                'upper_band': round(upper_band, 2),
                'upside_pct': round(upside_pct, 2),
                'dist_lb_pct': round(dist_lb_pct, 2),
                'vol_lots': vol_lots,
                'tot_lots': tot_lots,
                'inst_ratio': round(inst_ratio, 2),
                'foreign_lots': f_lots,
                'trust_lots': t_lots,
                'is_dual': is_dual,
                'score': score,
                'stars': stars,
                'pattern': candle_feat['pattern_desc']
            })

    print(f"[OK] 回測期間共篩選出 {len(signals)} 個符合條件之進場訊號。")

    # 模擬交易路徑追蹤 (次日開盤進場，最多持有 20 個交易日)
    trades = []
    max_hold_days = 20

    for s in signals:
        t_idx = s['signal_idx']
        ticker = s['ticker']
        tp1 = s['tp1']
        tp2 = s['tp2']
        sl = s['sl']

        # 次日進場
        if t_idx + 1 >= len(date_strs):
            continue  # 最後一天無次日

        entry_date = date_strs[t_idx + 1]
        entry_price = float(open_df[ticker].iloc[t_idx + 1])
        if pd.isna(entry_price) or entry_price <= 0:
            entry_price = s['close']  # 備用收盤價

        # 追蹤後續走勢
        exit_date = None
        exit_price = None
        exit_reason = None
        holding_days = 0
        hit_tp1 = False
        hit_tp2 = False
        hit_sl = False

        # 逐日檢查
        end_idx = min(t_idx + 1 + max_hold_days, len(date_strs))
        for day_i, curr_idx in enumerate(range(t_idx + 1, end_idx), 1):
            curr_date = date_strs[curr_idx]
            curr_o = float(open_df[ticker].iloc[curr_idx])
            curr_h = float(high_df[ticker].iloc[curr_idx])
            curr_l = float(low_df[ticker].iloc[curr_idx])
            curr_c = float(close_df[ticker].iloc[curr_idx])

            if pd.isna(curr_c):
                continue

            # 檢查是否同時碰觸或觸及
            touch_sl = (curr_l <= sl)
            touch_tp1 = (curr_h >= tp1)
            touch_tp2 = (curr_h >= tp2)

            if touch_tp2:
                hit_tp2 = True
            if touch_tp1:
                hit_tp1 = True
            if touch_sl:
                hit_sl = True

            # 判定出場
            # 優先權：若同一天開低跳空破停損，先止損；若同一天又碰 TP 又碰 SL，依開盤位置判定
            if touch_sl and touch_tp1:
                # 若開盤就低於停損，則直接停損
                if curr_o <= sl:
                    exit_reason = "STOP_LOSS"
                    exit_price = curr_o
                    exit_date = curr_date
                    holding_days = day_i
                    break
                else:
                    # 保守視為停損防守
                    exit_reason = "STOP_LOSS"
                    exit_price = sl
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

            # 若達到第 20 日尚未出場，收盤平倉
            if day_i == max_hold_days:
                exit_reason = "TIME_EXIT"
                exit_price = curr_c
                exit_date = curr_date
                holding_days = day_i
                break

        if exit_reason is None:
            # 期間結束仍持有
            last_idx = end_idx - 1
            exit_reason = "HOLDING_END"
            exit_price = float(close_df[ticker].iloc[last_idx])
            exit_date = date_strs[last_idx]
            holding_days = end_idx - (t_idx + 1)

        # 計算損益
        ret_pct = (exit_price - entry_price) / entry_price * 100.0
        # 訊號日收盤進場作為對照基準
        ret_ref_pct = (exit_price - s['close']) / s['close'] * 100.0

        trades.append({
            **s,
            'entry_date': entry_date,
            'entry_price': round(entry_price, 2),
            'exit_date': exit_date,
            'exit_price': round(exit_price, 2),
            'exit_reason': exit_reason,
            'holding_days': holding_days,
            'return_pct': round(ret_pct, 2),
            'return_ref_pct': round(ret_ref_pct, 2),
            'is_win': ret_pct > 0,
            'hit_tp1': hit_tp1,
            'hit_tp2': hit_tp2,
            'hit_sl': hit_sl
        })

    df_trades = pd.DataFrame(trades)
    df_trades.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    print(f"[✓] 交易明細已匯出至 {OUTPUT_CSV}")

    # 統計指標計算
    total_trades = len(df_trades)
    tp1_count = len(df_trades[df_trades['exit_reason'] == 'TP1'])
    sl_count = len(df_trades[df_trades['exit_reason'] == 'STOP_LOSS'])
    time_exit_count = len(df_trades[df_trades['exit_reason'] == 'TIME_EXIT'])
    holding_end_count = len(df_trades[df_trades['exit_reason'] == 'HOLDING_END'])

    # 包含持股期間內曾觸及 TP1 / TP2 / SL 的累積總次數
    ever_tp1_count = int(df_trades['hit_tp1'].sum())
    ever_tp2_count = int(df_trades['hit_tp2'].sum())
    ever_sl_count = int(df_trades['hit_sl'].sum())

    win_trades = df_trades[df_trades['return_pct'] > 0]
    loss_trades = df_trades[df_trades['return_pct'] < 0]
    flat_trades = df_trades[df_trades['return_pct'] == 0]

    win_rate = (len(win_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
    tp_sl_ratio = (tp1_count / (tp1_count + sl_count) * 100.0) if (tp1_count + sl_count) > 0 else 0.0

    avg_return = df_trades['return_pct'].mean()
    avg_win = win_trades['return_pct'].mean() if len(win_trades) > 0 else 0.0
    avg_loss = loss_trades['return_pct'].mean() if len(loss_trades) > 0 else 0.0
    avg_holding = df_trades['holding_days'].mean()

    # 盈虧比 (Profit/Loss Ratio)
    profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
    # 總獲利 / 總虧損 (Profit Factor)
    total_profit = win_trades['return_pct'].sum()
    total_loss = abs(loss_trades['return_pct'].sum())
    profit_factor = (total_profit / total_loss) if total_loss > 0 else 0.0

    # 星級分組統計
    stars_summary = {}
    for st in ['★★★★★', '★★★★☆', '★★★☆☆']:
        sub = df_trades[df_trades['stars'] == st]
        if len(sub) > 0:
            sub_win = sub[sub['return_pct'] > 0]
            sub_tp = sub[sub['exit_reason'] == 'TP1']
            sub_sl = sub[sub['exit_reason'] == 'STOP_LOSS']
            stars_summary[st] = {
                'count': len(sub),
                'win_rate': round(len(sub_win) / len(sub) * 100.0, 2),
                'tp1_hits': len(sub_tp),
                'sl_hits': len(sub_sl),
                'avg_return': round(sub['return_pct'].mean(), 2)
            }

    # 季度統計
    df_trades['quarter'] = pd.to_datetime(df_trades['signal_date']).dt.to_period('Q').astype(str)
    quarterly_summary = {}
    for q, q_df in df_trades.groupby('quarter'):
        q_win = q_df[q_df['return_pct'] > 0]
        quarterly_summary[q] = {
            'count': len(q_df),
            'win_rate': round(len(q_win) / len(q_df) * 100.0, 2),
            'tp1_count': len(q_df[q_df['exit_reason'] == 'TP1']),
            'sl_count': len(q_df[q_df['exit_reason'] == 'STOP_LOSS']),
            'avg_return': round(q_df['return_pct'].mean(), 2)
        }

    summary = {
        'backtest_period': f"2025/01/01 ~ 2026/09/30 (民國 114 年 1 月 1 日 ~ 115 年 9 月底)",
        'trading_days': len(eval_dates),
        'total_signals': len(signals),
        'total_trades': total_trades,
        'win_trades_count': len(win_trades),
        'loss_trades_count': len(loss_trades),
        'flat_trades_count': len(flat_trades),
        'win_rate_pct': round(win_rate, 2),
        'tp_vs_sl_win_rate_pct': round(tp_sl_ratio, 2),
        'exit_reasons': {
            'TP1_hit': tp1_count,
            'TP1_pct': round(tp1_count / total_trades * 100.0, 2),
            'STOP_LOSS_hit': sl_count,
            'STOP_LOSS_pct': round(sl_count / total_trades * 100.0, 2),
            'TIME_EXIT': time_exit_count,
            'TIME_EXIT_pct': round(time_exit_count / total_trades * 100.0, 2),
            'HOLDING_END': holding_end_count
        },
        'cumulative_touch_counts': {
            'ever_hit_tp1': ever_tp1_count,
            'ever_hit_tp2': ever_tp2_count,
            'ever_hit_sl': ever_sl_count
        },
        'returns': {
            'avg_return_pct': round(avg_return, 2),
            'avg_win_pct': round(avg_win, 2),
            'avg_loss_pct': round(avg_loss, 2),
            'profit_loss_ratio': round(profit_loss_ratio, 2),
            'profit_factor': round(profit_factor, 2),
            'max_win_pct': round(df_trades['return_pct'].max(), 2),
            'max_loss_pct': round(df_trades['return_pct'].min(), 2)
        },
        'avg_holding_days': round(avg_holding, 1),
        'stars_summary': stars_summary,
        'quarterly_summary': quarterly_summary
    }

    import json
    with open(OUTPUT_SUMMARY_JSON, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "="*50)
    print("【回測成果總覽】")
    print(f"回測區間: {summary['backtest_period']}")
    print(f"總進場筆數: {total_trades} 筆")
    print(f"勝率 (結算正報酬): {win_rate:.2f}% ({len(win_trades)} 勝 / {len(loss_trades)} 負)")
    print(f"達標勝率 (TP1 / (TP1+SL)): {tp_sl_ratio:.2f}%")
    print(f"觸及停利 (TP1) 出場: {tp1_count} 次 ({summary['exit_reasons']['TP1_pct']}%)")
    print(f"觸及停損 (SL) 出場: {sl_count} 次 ({summary['exit_reasons']['STOP_LOSS_pct']}%)")
    print(f"持股滿20日平倉: {time_exit_count} 次 ({summary['exit_reasons']['TIME_EXIT_pct']}%)")
    print(f"持股期間曾觸及 TP1 累積次數: {ever_tp1_count} 次")
    print(f"持股期間曾觸及 TP2 累積次數: {ever_tp2_count} 次")
    print(f"持股期間曾觸及 SL 累積次數: {ever_sl_count} 次")
    print(f"平均單筆報酬率: {avg_return:+.2f}%")
    print(f"盈虧比 (Avg Win / Avg Loss): {profit_loss_ratio:.2f}")
    print(f"獲利因子 (Profit Factor): {profit_factor:.2f}")
    print(f"平均持股天數: {avg_holding:.1f} 天")
    print("="*50)

if __name__ == '__main__':
    run_backtest()
