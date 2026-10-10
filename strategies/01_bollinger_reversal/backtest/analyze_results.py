#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

CSV_PATH = os.path.join(os.path.dirname(__file__), 'backtest_trades.csv')
df = pd.read_csv(CSV_PATH)

print('=== 訊號日收盤價進場 vs 次日開盤價進場 ===')
win_ref = df[df['return_ref_pct'] > 0]
print(f'收盤基準勝率: {len(win_ref)/len(df)*100:.2f}%, 平均報酬: {df["return_ref_pct"].mean():.2f}%')
print(f'次日開盤勝率: {df["is_win"].sum()/len(df)*100:.2f}%, 平均報酬: {df["return_pct"].mean():.2f}%')

print('\n=== 土洋同買分析 ===')
for is_dual in [True, False]:
    sub = df[df['is_dual'] == is_dual]
    win_sub = sub[sub['is_win']]
    tp_sub = sub[sub['exit_reason'] == 'TP1']
    sl_sub = sub[sub['exit_reason'] == 'STOP_LOSS']
    print(f'土洋同買={is_dual}: 筆數={len(sub)}, 正報酬勝率={len(win_sub)/len(sub)*100:.2f}%, 達標勝率={len(tp_sub)/(len(tp_sub)+len(sl_sub))*100:.2f}%, 平均報酬={sub["return_pct"].mean():.2f}%')

print('\n=== 距中軌反彈空間分組 ===')
for space_min, space_max in [(2.0, 4.0), (4.0, 6.0), (6.0, 999.0)]:
    sub = df[(df['upside_pct'] >= space_min) & (df['upside_pct'] < space_max)]
    if len(sub) > 0:
        win_sub = sub[sub['is_win']]
        print(f'空間 {space_min}%~{space_max}%: 筆數={len(sub)}, 勝率={len(win_sub)/len(sub)*100:.2f}%, 平均報酬={sub["return_pct"].mean():.2f}%')

print('\n=== 報酬率分佈 ===')
bins = [-999, -10, -5, 0, 5, 10, 999]
labels = ['<-10%', '-10%~-5%', '-5%~0%', '0%~5%', '5%~10%', '>10%']
df['ret_bin'] = pd.cut(df['return_pct'], bins=bins, labels=labels)
print(df['ret_bin'].value_counts()[labels])

print('\n=== 代表性飆漲獲利案例 (Top 5) ===')
top_wins = df.sort_values(by='return_pct', ascending=False).head(5)
for _, r in top_wins.iterrows():
    print(f"{r['signal_date']} {r['code']} {r['name']} ({r['stars']}): 報酬 +{r['return_pct']}%, 持有 {r['holding_days']} 天, 出場理由: {r['exit_reason']}")

print('\n=== 代表性停損案例 (Top 5 最大虧損) ===')
top_loss = df.sort_values(by='return_pct', ascending=True).head(5)
for _, r in top_loss.iterrows():
    print(f"{r['signal_date']} {r['code']} {r['name']} ({r['stars']}): 報酬 {r['return_pct']}%, 持有 {r['holding_days']} 天, 出場理由: {r['exit_reason']}")
