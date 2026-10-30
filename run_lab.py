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
    ScreenerFusionBridge,
)


def main():
    parser = argparse.ArgumentParser(description="台股量化策略實驗室 - 自動化回測工具")
    parser.add_argument("--strategy", "-s", type=str, default="01", help="策略代號 (01: 布林超跌, 02: 法人起漲)")
    parser.add_argument("--symbol", type=str, default="2330", help="回測標的代碼 (預設: 2330)")
    parser.add_argument("--cash", type=float, default=1_000_000.0, help="初始資金 (預設: 100萬)")
    parser.add_argument("--scenario", type=str, default="bb_reversal", help="測試行情情境 (bb_reversal / trend_up / chop)")
    parser.add_argument("--days", type=int, default=100, help="模擬天數 (預設: 100天)")
    parser.add_argument("--list", action="store_true", help="列出實驗室所有可用策略")
    parser.add_argument("--daily-advisor", action="store_true", help="自動讀取今日 01~11 全實盤選股池執行回測，產出明日建議買賣清單")
    parser.add_argument("--leaderboard", action="store_true", help="執行 14 支核心與複合因子策略橫向 PK 排行榜評比")
    parser.add_argument("--line", action="store_true", help="將實驗室產出之明日買賣建議戰報推播至 LINE")
    parser.add_argument("--top-n", type=int, default=2, help="每支實盤策略抽取前 N 檔個股回測 (預設: 2)")
    args = parser.parse_args()

    if args.leaderboard:
        storage = MarketStorage()
        loader = MarketDataLoader(storage)
        lb = ScreenerFusionBridge.generate_strategy_leaderboard(
            storage=storage,
            loader=loader,
            initial_cash=args.cash,
        )
        print("\n================ 🏆 量化策略實驗室・全策略橫向 PK 排行榜 ================")
        for r in lb["leaderboard"]:
            print(
                f"  #{r['rank']:02d} | {r['grade']} | {r['name']:<32} | "
                f"報酬 {r['total_return_pct']:+6.2f}% | 勝率 {r['win_rate_pct']:5.1f}% | "
                f"夏普 {r['sharpe_ratio']:5.2f} | Calmar {r['calmar_ratio']:5.2f} | 總分 {r['score']:5.1f}"
            )
        print("============================================================================\n")
        return

    if args.daily_advisor:
        storage = MarketStorage()
        loader = MarketDataLoader(storage)
        report = ScreenerFusionBridge.generate_next_day_advisor_report(
            storage=storage,
            loader=loader,
            top_n_per_strategy=args.top_n,
            initial_cash=args.cash,
        )
        mr = report.get("market_regime", {})
        print("\n================ 🔮 今日盤後選股池自動回測 ➔ 明日 (T+1) 買賣決策清單 (V3.0) ================")
        print(
            f"  🧭 大盤位階: {mr.get('label', '震盪偏多')} | 建議現金保留: {mr.get('cash_target_pct', 30)}% | "
            f"可動用資金: NT${report.get('effective_capital', args.cash):,.0f}"
        )
        print(
            f"  📊 共評估 {report['total_evaluated']} 檔 | "
            f"🟢優先買進: {report['strong_buy_count']} | "
            f"🟡拉回低接: {report['buy_on_dip_count']} | "
            f"🔴建議放空: {report['short_sell_count']} | "
            f"🛑淘汰剔除: {report['filtered_out_count']}"
        )
        print("-" * 92)
        for r in report["recommendations"]:
            print(
                f"  {r['tomorrow_action']:<18} | {r['symbol']} {r['name']:<6} | 來源: {r['source_strategy']} ({r['verdict']})"
            )
            print(
                f"     ↳ 歷史回測: 報酬 {r['hist_return_pct']:+.2f}%, 勝率 {r['hist_win_rate_pct']:.1f}%, 夏普 {r['hist_sharpe']:.2f}, 盈虧比 1:{r['rr_ratio']}, 期望值 {r['expectancy_pct']:+.2f}%"
            )
            fut_str = f" / 或股期 {r['recommended_futures_lots']} 口" if r.get("recommended_futures_lots", 0) > 0 else ""
            print(
                f"     ↳ 明日四防線: 🟢進場 ${r['entry_price']} | 🔵加碼 ${r['addon_price']} | 🔴TP1 ${r['tp1_price']} / TP2 ${r['tp2_price']} | 🛑停損 ${r['stop_loss_price']} (建議 {r['recommended_shares']:,} 股{fut_str})"
            )
        out_csv = os.path.join(ROOT_DIR, "lab", "data", "tomorrow_trading_advisor.csv")
        with open(out_csv, "w", encoding="utf-8-sig") as f:
            f.write(report["csv_content"])
        print("-" * 92)
        print(f"[✓] 明日買賣建議清單已匯出至: {out_csv}\n")
        if args.line:
            line_text = ScreenerFusionBridge.format_advisor_line_text(report)
            try:
                from line_sender import send_to_line
                send_to_line(line_text, strategy="01")
                print("[✓] 已推播明日買賣建議戰報至 LINE！")
            except Exception as e:
                print(f"[!] LINE 推播略過: {e}")
        return

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

    # 準備回測數據 (優先採用 yfinance 真實歷史行情)
    print(f"[*] 載入標的行情 [{args.symbol}] (優先使用 yfinance 真實日 K)...")
    df = loader.ensure_real_or_fallback_series(
        symbol=args.symbol,
        scenario=args.scenario,
        days=args.days,
    )
    meta = storage.get_stock_meta(args.symbol)
    src_tag = "yfinance 真實歷史行情" if (meta and meta.get("market") == "REAL_YFINANCE") else f"情境模擬序列 ({args.scenario})"
    print(f"[✓] 標的行情載入完成：共 {len(df)} 根 K 棒 (來源: {src_tag})")

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
