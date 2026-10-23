#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化策略實驗室 - 獨立 CLI 執行器 (run_lab.py)
支援終端快速執行回測、參數掃描、列印績效度量與產生獨立戰報。
"""

import sys
import os
import argparse
import json

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

# 加入工作區根目錄
ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lab import (
    MarketStorage,
    MarketDataLoader,
    BacktestEngine,
    StrategyRegistry,
)


def main():
    parser = argparse.ArgumentParser(description="台股量化策略實驗室 - 自動化回測工具")
    parser.add_argument("--strategy", "-s", type=str, default="01", help="策略代號 (01: 布林超跌, 02: 法人起漲)")
    parser.add_argument("--symbol", type=str, default="2330", help="回測標的代碼 (預設: 2330)")
    parser.add_argument("--cash", type=float, default=1_000_000.0, help="初始資金 (預設: 100萬)")
    parser.add_argument("--scenario", type=str, default="bb_reversal", help="測試行情情境 (bb_reversal / trend_up / chop)")
    parser.add_argument("--days", type=int, default=100, help="模擬天數 (預設: 100天)")
    parser.add_argument("--list", action="store_true", help="列出實驗室所有可用策略")
    args = parser.parse_args()

    if args.list:
        print("\n================== 🔬 台股量化策略實驗室 - 策略庫 ==================")
        strats = StrategyRegistry.list_strategies()
        for s in strats:
            print(f"[{s['id']}] {s['name']}")
            print(f"    說明: {s['description']}")
            print(f"    預設參數: {json.dumps(s['default_params'], ensure_ascii=False)}")
        print("===================================================================\n")
        return

    print(f"\n[*] 啟動量化回測實驗室: 策略 [{args.strategy}], 標的 [{args.symbol}], 初始資金 [NT${args.cash:,.0f}]")
    storage = MarketStorage()
    loader = MarketDataLoader(storage)

    # 準備回測數據
    df = storage.get_daily_bars(args.symbol)
    if df.empty or len(df) < 30:
        print(f"[*] 生成標準測試行情序列 (情境: {args.scenario}, 天數: {args.days})...")
        df = loader.generate_mock_series(symbol=args.symbol, scenario=args.scenario, days=args.days)

    market_data = {args.symbol: df}
    strategy = StrategyRegistry.get(args.strategy)
    if not strategy:
        print(f"[!] 找不到策略代號 {args.strategy}")
        return

    print(f"[*] 撮合引擎運行中 (零未來函數、四大防線狀態機、T+2交割)...")
    engine = BacktestEngine(initial_cash=args.cash)
    res = engine.run(market_data=market_data, signal_generator=strategy.generate_signals)

    m = res.metrics
    print("\n======================= 📊 回測績效報告 =======================")
    print(f"  初始資金:        NT${m['initial_cash']:>12,.0f}")
    print(f"  最終權益:        NT${m['final_equity']:>12,.0f}")
    print(f"  總報酬率:        {m['total_return_pct']:>11.2f}%")
    print(f"  年化報酬 (CAGR): {m['cagr_pct']:>11.2f}%")
    print(f"  最大回撤 (MDD):  {m['max_drawdown_pct']:>11.2f}%")
    print(f"  卡瑪比率 (Calmar):{m.get('calmar_ratio', 0.0):>11.2f}")
    print(f"  夏普值 (Sharpe): {m['sharpe_ratio']:>12.2f}")
    print(f"  索提諾 (Sortino):{m['sortino_ratio']:>12.2f}")
    print(f"  總交易筆數:      {m['total_closed_trades']:>12d}")
    print(f"  交易勝率:        {m['win_rate']:>11.2f}%  ({m['win_count']} 勝 / {m['loss_count']} 負)")
    print(f"  獲利因子 (PF):   {m['profit_factor']:>12.2f}")
    print(f"  平均單筆報酬:    {m['avg_trade_pnl_pct']:>11.2f}%")
    print(f"  平均持倉天數:    {m.get('avg_holding_days', 0.0):>11.1f} 天")
    print(f"  最長連勝/連敗:   {m.get('max_cons_wins', 0)} 勝 / {m.get('max_cons_losses', 0)} 敗")
    print("===============================================================\n")

    if res.trade_history:
        print("[*] 最近交易明細:")
        for t in res.trade_history[-6:]:
            pnl_str = f", PnL: NT${t.realized_pnl:+,.0f} ({t.pnl_pct:+.2f}%), 持有 {t.holding_days} 天" if (t.action.startswith("SELL") or "COVER" in t.action) else ""
            print(f"  - [{t.date}] [{t.direction:<5}] {t.action:<16} {t.shares:>5d}股 @ NT${t.price:>6.2f} ({t.reason}){pnl_str}")
        print()


if __name__ == "__main__":
    main()
