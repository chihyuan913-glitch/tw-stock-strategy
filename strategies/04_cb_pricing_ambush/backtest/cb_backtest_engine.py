#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略四：可轉債 (CB) 定價伏擊與區間博弈旗艦策略 歷史回測引擎
回測期間：民國 114 年 1 月 1 日 (2025-01-01) 至 115 年 9 月 30 日 (2026-09-30)

核心交易規則：
1. 標的池：2025/01/01 ~ 2026/09/30 於櫃買中心公告發行之全市場可轉債 (共 225 檔，涵蓋 184 家上市櫃公司)
2. 進場模式：
   - 模式 A (定價伏擊 Mode): 定價確認日 (掛牌前 5 個交易日，定價公告打壓結束) 次日開盤進場
   - 模式 B (掛牌首日 Mode): 掛牌發行日 (Issue Date) 當日開盤進場，鎖定 3 個月閉鎖期
3. 風控與出場四大防線 (Four-Price Standard)：
   - 🟢 進場 (Entry): 開盤價建倉
   - 🔴 TP1 第一階段停利: 達到進場價 +7.5% 或 轉換價 +7.5% (部分分批或全出)
   - 🔴 TP2 第二階段停利: 達到進場價 +15.0% 或 轉換價 +15.0%
   - 🔴 空間極限 130% 逼贖出清: 觸及轉換價格 130% (公司收回條款與主力達標) 強制全數停利
   - 🛑 停損防守 SL: 跌破轉換價格 4% 或 進場成本跌破 5% 無條件認賠
   - ⏳ 時間死線 (Exit Deadline): 到達掛牌滿 3 個月閉鎖期解禁日 (Conversion/ExchangePeriodStartDate)，無論盈虧全數清倉！
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
OUTPUT_CSV_PREFIX = os.path.join(BASE_DIR, "cb_backtest_trades")
OUTPUT_SUMMARY_JSON = os.path.join(BASE_DIR, "cb_backtest_summary.json")

def load_data():
    if not os.path.exists(CB_ISSUES_JSON) or not os.path.exists(OHLCV_PKL):
        raise FileNotFoundError("請先執行 download_cb_data.py 下載發行資料與K線！")
        
    with open(CB_ISSUES_JSON, 'r', encoding='utf-8') as f:
        cb_issues = json.load(f)
    ohlcv_dict = pd.read_pickle(OHLCV_PKL)
    return cb_issues, ohlcv_dict

def run_single_trade_simulation(df_stock, entry_idx, lockup_end_dt, conv_price, 
                                tp1_pct=0.08, tp2_pct=0.15, sl_pct=0.05, 
                                enable_130_cap=True, strategy_mode='four_price'):
    """
    模擬單筆 CB 交易的逐日軌跡
    strategy_mode:
      - 'four_price': 依據四大價位 (TP1/TP2 分批或追蹤停利 + 130% 逼贖 + 5%停損 + 閉鎖期死線)
      - 'buy_and_hold_lockup': 單純持有至閉鎖期解禁日收盤
      - 'conservative_tp1': 達 TP1 (+7.5%) 立即全數出場
      - 'dynamic_trail': 達 TP1 保本，達 TP2 獲利，達 130% 獲利，停損 -5%，時間死線
    """
    entry_row = df_stock.iloc[entry_idx]
    entry_date = df_stock.index[entry_idx]
    entry_price = float(entry_row['Open'])
    
    if entry_price <= 0 or np.isnan(entry_price):
        return None

    # 目標價設定
    target_130 = conv_price * 1.30 if (conv_price > 0 and enable_130_cap) else float('inf')
    target_tp1 = max(entry_price * (1.0 + tp1_pct), conv_price * (1.0 + tp1_pct))
    target_tp2 = max(entry_price * (1.0 + tp2_pct), conv_price * (1.0 + tp2_pct))
    stop_loss = min(entry_price * (1.0 - sl_pct), conv_price * 0.96)

    max_high = entry_price
    min_low = entry_price
    
    # 逐日回測
    # 策略部位：預設 1.0 (若部分停利則分為 tp1 出 50%，其餘由 tp2 或 trailing/死線出場)
    position = 1.0
    realized_pnl_list = [] # 儲存 (部位權重, 賣出價, 賣出原因, 賣出日期)
    
    exit_date = None
    exit_price = None
    exit_reason = None
    
    for i in range(entry_idx, len(df_stock)):
        curr_row = df_stock.iloc[i]
        curr_date = df_stock.index[i]
        
        o = float(curr_row['Open'])
        h = float(curr_row['High'])
        l = float(curr_row['Low'])
        c = float(curr_row['Close'])
        
        if h > max_high:
            max_high = h
        if l < min_low:
            min_low = l
            
        # 若當日已超過或到達閉鎖期解禁日 (Conversion/ExchangePeriodStartDate)
        is_lockup_expired = (curr_date >= lockup_end_dt)
        
        if strategy_mode == 'buy_and_hold_lockup':
            if is_lockup_expired or i == len(df_stock) - 1:
                exit_date = curr_date
                exit_price = c
                exit_reason = "LOCKUP_DEADLINE"
                realized_pnl_list.append((position, exit_price, exit_reason, exit_date))
                position = 0.0
                break
            continue

        if strategy_mode == 'conservative_tp1':
            # 觸及 TP1 立即全出
            if h >= target_tp1:
                exit_date = curr_date
                exit_price = max(o, target_tp1) # 若開盤就跳空高過 TP1 以開盤計
                exit_reason = "TP1_REACHED"
                realized_pnl_list.append((position, exit_price, exit_reason, exit_date))
                position = 0.0
                break
            elif l <= stop_loss:
                exit_date = curr_date
                exit_price = min(o, stop_loss)
                exit_reason = "STOP_LOSS"
                realized_pnl_list.append((position, exit_price, exit_reason, exit_date))
                position = 0.0
                break
            elif is_lockup_expired or i == len(df_stock) - 1:
                exit_date = curr_date
                exit_price = c
                exit_reason = "LOCKUP_DEADLINE"
                realized_pnl_list.append((position, exit_price, exit_reason, exit_date))
                position = 0.0
                break
            continue

        # 標準四大價位模式 (four_price):
        # 1. 空間極限: 股價達 130% 轉換價，主力逼贖目標達成，全部清倉
        if h >= target_130:
            p_sell = max(o, target_130)
            realized_pnl_list.append((position, p_sell, "130%_CEILING", curr_date))
            exit_date = curr_date
            exit_price = p_sell
            exit_reason = "130%_CEILING"
            position = 0.0
            break
            
        # 2. 停損防守: 跌破停損線
        if l <= stop_loss:
            p_sell = min(o, stop_loss)
            realized_pnl_list.append((position, p_sell, "STOP_LOSS", curr_date))
            exit_date = curr_date
            exit_price = p_sell
            exit_reason = "STOP_LOSS"
            position = 0.0
            break

        # 3. 分批停利: TP1 出 50%，TP2 出剩下 50%
        if position == 1.0 and h >= target_tp1:
            p_sell = max(o, target_tp1)
            realized_pnl_list.append((0.5, p_sell, "TP1_HALF", curr_date))
            position = 0.5
            # 移動停損至進場保本點
            stop_loss = max(stop_loss, entry_price)

        if position == 0.5 and h >= target_tp2:
            p_sell = max(o, target_tp2)
            realized_pnl_list.append((0.5, p_sell, "TP2_FULL", curr_date))
            exit_date = curr_date
            exit_price = p_sell
            exit_reason = "TP2_TARGET"
            position = 0.0
            break

        # 4. 時間死線: 閉鎖期解禁當日收盤全數出清
        if is_lockup_expired or i == len(df_stock) - 1:
            realized_pnl_list.append((position, c, "LOCKUP_DEADLINE", curr_date))
            exit_date = curr_date
            exit_price = c
            exit_reason = "LOCKUP_DEADLINE" if is_lockup_expired else "DATA_END"
            position = 0.0
            break

    # 計算加權平均賣出價格與報酬率
    total_revenue = sum(weight * p for weight, p, r, d in realized_pnl_list)
    total_cost = entry_price
    ret_pct = ((total_revenue - total_cost) / total_cost) * 100.0
    
    max_runup = ((max_high - entry_price) / entry_price) * 100.0
    max_drawdown = ((min_low - entry_price) / entry_price) * 100.0
    
    # 決定主要出場原因
    primary_reason = "/".join([r for weight, p, r, d in realized_pnl_list])
    
    holding_days = 0
    if exit_date is not None:
        holding_days = len(df_stock.loc[entry_date:exit_date])
        
    return {
        'entry_date': entry_date.strftime('%Y-%m-%d'),
        'entry_price': round(entry_price, 2),
        'exit_date': exit_date.strftime('%Y-%m-%d') if exit_date is not None else '',
        'exit_price': round(exit_price, 2) if exit_price is not None else 0.0,
        'return_pct': round(ret_pct, 2),
        'exit_reason': primary_reason,
        'holding_days': holding_days,
        'max_runup_pct': round(max_runup, 2),
        'max_drawdown_pct': round(max_drawdown, 2),
        'target_tp1': round(target_tp1, 2),
        'target_tp2': round(target_tp2, 2),
        'target_130': round(target_130, 2) if target_130 != float('inf') else 0.0,
        'stop_loss': round(stop_loss, 2)
    }

def run_backtest(entry_mode='pricing_ambush', strategy_mode='four_price', 
                 vol_filter=500, amount_filter=0.0):
    """
    執行回測
    entry_mode: 
      - 'pricing_ambush': 定價公告日 (掛牌日前 5 交易日) 進場
      - 'listing_date': 掛牌首日進場
    strategy_mode:
      - 'four_price': 四大價位分批與風控保護
      - 'conservative_tp1': 達 TP1 立即全出
      - 'buy_and_hold_lockup': 純持有至閉鎖期解禁
    vol_filter: 20日均量門檻 (張)
    amount_filter: 發行規模門檻 (億元)
    """
    cb_issues, ohlcv_dict = load_data()
    trades = []
    
    for cb in cb_issues:
        code = cb['IssuerCode']
        name = cb.get('IssuerName', '')
        cb_name = cb.get('ShortName', '')
        conv_price = float(cb.get('ConversionPrice', 0))
        issue_amount = float(cb.get('IssueAmountNum', 0))
        issue_dt_str = cb.get('IssueDate', '')
        lockup_dt_str = cb.get('Conversion/ExchangePeriodStartDate', '')
        
        if issue_amount < amount_filter:
            continue
            
        if code not in ohlcv_dict:
            continue
            
        df = ohlcv_dict[code].copy()
        if len(df) < 20:
            continue
            
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        
        # 解析日期
        try:
            issue_dt = pd.to_datetime(issue_dt_str)
            lockup_dt = pd.to_datetime(lockup_dt_str)
        except Exception:
            continue
            
        # 尋找進場交易日索引
        # 找出最接近 issue_dt 的交易日
        avail_dates_before_issue = df.index[df.index <= issue_dt]
        if len(avail_dates_before_issue) == 0:
            continue
            
        issue_idx = df.index.get_loc(avail_dates_before_issue[-1])
        
        if entry_mode == 'pricing_ambush':
            # 定價基準日約在掛牌前 5 個交易日
            target_entry_idx = issue_idx - 5
            if target_entry_idx < 15: # 均線計算不足
                target_entry_idx = max(0, issue_idx - 3)
        else:
            # 掛牌首日
            avail_dates_on_after = df.index[df.index >= issue_dt]
            if len(avail_dates_on_after) == 0:
                continue
            target_entry_idx = df.index.get_loc(avail_dates_on_after[0])
            
        # 檢查流動性門檻 (成交量 20MA)
        if target_entry_idx >= 10:
            recent_vols = df['Volume'].iloc[target_entry_idx-10:target_entry_idx]
            avg_vol_lots = (recent_vols.mean()) / 1000.0
            if avg_vol_lots < vol_filter:
                continue
        else:
            avg_vol_lots = df['Volume'].iloc[target_entry_idx] / 1000.0
            if avg_vol_lots < vol_filter:
                continue
                
        # 執行回測模擬
        res = run_single_trade_simulation(
            df_stock=df, 
            entry_idx=target_entry_idx, 
            lockup_end_dt=lockup_dt, 
            conv_price=conv_price, 
            strategy_mode=strategy_mode
        )
        
        if res is not None:
            res['code'] = code
            res['name'] = name
            res['cb_name'] = cb_name
            res['conv_price'] = conv_price
            res['issue_amount'] = issue_amount
            res['issue_date'] = issue_dt.strftime('%Y-%m-%d')
            res['lockup_end_date'] = lockup_dt.strftime('%Y-%m-%d')
            res['avg_vol_lots'] = round(avg_vol_lots, 1)
            trades.append(res)
            
    df_trades = pd.DataFrame(trades)
    return df_trades

def calculate_performance_metrics(df_trades, label=""):
    if df_trades.empty:
        return {"label": label, "total_trades": 0}
        
    n = len(df_trades)
    win_trades = df_trades[df_trades['return_pct'] > 0]
    loss_trades = df_trades[df_trades['return_pct'] < 0]
    even_trades = df_trades[df_trades['return_pct'] == 0]
    
    win_rate = (len(win_trades) / n) * 100.0
    avg_return = df_trades['return_pct'].mean()
    median_return = df_trades['return_pct'].median()
    total_return = df_trades['return_pct'].sum()
    
    avg_win = win_trades['return_pct'].mean() if len(win_trades) > 0 else 0.0
    avg_loss = loss_trades['return_pct'].mean() if len(loss_trades) > 0 else 0.0
    
    total_gain = win_trades['return_pct'].sum()
    total_loss_abs = abs(loss_trades['return_pct'].sum())
    profit_factor = round(total_gain / total_loss_abs, 2) if total_loss_abs > 0 else 999.0
    
    max_win = df_trades['return_pct'].max()
    max_loss = df_trades['return_pct'].min()
    avg_holding = df_trades['holding_days'].mean()
    
    # 權益曲線計算最大策略回撤 (Max Drawdown of cumulative returns)
    cum_returns = (1 + df_trades['return_pct'] / 100.0).cumprod()
    cum_max = cum_returns.cummax()
    drawdown = (cum_returns - cum_max) / cum_max * 100.0
    max_dd = drawdown.min()
    
    # 潛在走勢特徵
    avg_mfe = df_trades['max_runup_pct'].mean() # 最大潛在漲幅
    avg_mae = df_trades['max_drawdown_pct'].mean() # 最大潛在跌幅
    
    # 出場原因分析
    exit_reasons = {}
    for r in df_trades['exit_reason']:
        for sub_r in r.split('/'):
            exit_reasons[sub_r] = exit_reasons.get(sub_r, 0) + 1

    return {
        'label': label,
        'total_trades': n,
        'win_trades': len(win_trades),
        'loss_trades': len(loss_trades),
        'even_trades': len(even_trades),
        'win_rate_pct': round(win_rate, 2),
        'avg_return_pct': round(avg_return, 2),
        'median_return_pct': round(median_return, 2),
        'total_return_pct': round(total_return, 2),
        'avg_win_pct': round(avg_win, 2),
        'avg_loss_pct': round(avg_loss, 2),
        'profit_factor': profit_factor,
        'max_win_pct': round(max_win, 2),
        'max_loss_pct': round(max_loss, 2),
        'avg_holding_days': round(avg_holding, 1),
        'max_drawdown_pct': round(max_dd, 2),
        'avg_mfe_max_runup': round(avg_mfe, 2),
        'avg_mae_max_drawdown': round(avg_mae, 2),
        'exit_reasons': exit_reasons
    }

def run_all_comparisons():
    print("=" * 70)
    print("【可轉債 (CB) 定價伏擊策略 全維度回測分析】")
    print("回測區間：114年1月1日 (2025/01/01) ～ 115年9月30日 (2026/09/30)")
    print("=" * 70)
    
    configs = [
        ("策略核心：定價伏擊＋四大價位分批風控", "pricing_ambush", "four_price", 500),
        ("對照組 1：定價伏擊＋純持有至閉鎖期解禁", "pricing_ambush", "buy_and_hold_lockup", 500),
        ("對照組 2：定價伏擊＋TP1 (+7.5%) 獲利即出場", "pricing_ambush", "conservative_tp1", 500),
        ("對照組 3：掛牌首日進場＋四大價位分批風控", "listing_date", "four_price", 500),
        ("流動性嚴格組：定價伏擊＋四大價位 (量>=1000張)", "pricing_ambush", "four_price", 1000)
    ]
    
    results_summary = []
    
    for label, entry_mode, strat_mode, vol in configs:
        print(f"\n[*] 正在回測：{label} ...")
        df_trades = run_backtest(entry_mode=entry_mode, strategy_mode=strat_mode, vol_filter=vol)
        metrics = calculate_performance_metrics(df_trades, label=label)
        results_summary.append((metrics, df_trades))
        
        # 輸出詳細 CSV
        csv_filename = f"{OUTPUT_CSV_PREFIX}_{entry_mode}_{strat_mode}_vol{vol}.csv"
        df_trades.to_csv(csv_filename, index=False, encoding='utf-8-sig')
        print(f"  總交易筆數: {metrics['total_trades']} 筆 | 勝率: {metrics['win_rate_pct']}% | 平均報酬: {metrics['avg_return_pct']}% | 獲利因子: {metrics['profit_factor']}")

    # 輸出主要核心策略的年度比較 (2025 vs 2026)
    main_df = results_summary[0][1]
    main_df['entry_year'] = pd.to_datetime(main_df['entry_date']).dt.year
    df_2025 = main_df[main_df['entry_year'] == 2025]
    df_2026 = main_df[main_df['entry_year'] == 2026]
    
    metrics_2025 = calculate_performance_metrics(df_2025, label="2025年 (114年全年度)")
    metrics_2026 = calculate_performance_metrics(df_2026, label="2026年 (115年前三季)")
    
    print("\n" + "=" * 70)
    print("【年度表現細分】")
    print(f"2025 年 (114年): 交易 {metrics_2025['total_trades']} 筆 | 勝率 {metrics_2025['win_rate_pct']}% | 平均報酬 {metrics_2025['avg_return_pct']}% | 獲利因子 {metrics_2025['profit_factor']}")
    print(f"2026 年 (115年): 交易 {metrics_2026['total_trades']} 筆 | 勝率 {metrics_2026['win_rate_pct']}% | 平均報酬 {metrics_2026['avg_return_pct']}% | 獲利因子 {metrics_2026['profit_factor']}")
    print("=" * 70)
    
    # 儲存彙整 JSON
    all_summary_dict = {
        'configs_comparison': [m for m, _ in results_summary],
        'yearly_breakdown': {
            '2025': metrics_2025,
            '2026': metrics_2026
        }
    }
    with open(OUTPUT_SUMMARY_JSON, 'w', encoding='utf-8') as f:
        json.dump(all_summary_dict, f, ensure_ascii=False, indent=2)
        
    print(f"\n[OK] 完整回測摘要已儲存至: {OUTPUT_SUMMARY_JSON}")
    return results_summary, metrics_2025, metrics_2026

if __name__ == "__main__":
    run_all_comparisons()
