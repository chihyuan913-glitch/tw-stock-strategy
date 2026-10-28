#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 全策略一鍵排程執行引擎 (Master Runner)
功能：依序執行 strategies/ 下的所有策略盤後選股器，產出報表並推播 LINE 通知。
"""

import os
import sys
import argparse
import subprocess
import datetime
from pathlib import Path

# Windows 命令列編碼保護
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent

STRATEGIES = [
    {
        "id": "01_bollinger_reversal",
        "name": "布林通道下軌超跌反轉策略 (V2.5 雙層降級容錯版)",
        "script": ROOT_DIR / "strategies" / "01_bollinger_reversal" / "screener.py",
        "export": "result.csv",
    },
    {
        "id": "02_institutional_momentum",
        "name": "法人籌碼集中起漲策略 (主升段動能)",
        "script": ROOT_DIR / "strategies" / "02_institutional_momentum" / "screener.py",
        "export": "result.csv",
    },
    {
        "id": "03_short_momentum",
        "name": "法人出貨破線做空與股票期貨避險策略",
        "script": ROOT_DIR / "strategies" / "03_short_momentum" / "screener.py",
        "export": "result.csv",
    },
    {
        "id": "04_cb_pricing_ambush",
        "name": "可轉債 (CB) 定價伏擊與區間博弈策略",
        "script": ROOT_DIR / "strategies" / "04_cb_pricing_ambush" / "screener.py",
        "export": "result.csv",
    },
    {
        "id": "05_stock_futures_long",
        "name": "個股期貨法人強勢波段多方策略 (期貨價位優先)",
        "script": ROOT_DIR / "strategies" / "05_stock_futures_long" / "screener.py",
        "export": "result.csv",
    },
    {
        "id": "06_volume_surge",
        "name": "盤中成交量異常爆量起漲動能策略",
        "script": ROOT_DIR / "strategies" / "06_volume_surge" / "screener.py",
        "export": "result.csv",
    },
    {
        "id": "07_margin_call_ambush",
        "name": "高融資使用率×低維持率斷頭伏擊與浮額清洗策略",
        "script": ROOT_DIR / "strategies" / "07_margin_call_ambush" / "screener.py",
        "export": "result.csv",
    },
    {
        "id": "08_group_window_dressing",
        "name": "台股集團作帳與母子聯動伏擊策略",
        "script": ROOT_DIR / "strategies" / "08_group_window_dressing" / "screener.py",
        "export": "result.csv",
    },
    {
        "id": "09_treasury_stock_buyback",
        "name": "庫藏股宣告護盤與低估值事件策略",
        "script": ROOT_DIR / "strategies" / "09_treasury_stock_buyback" / "screener.py",
        "export": "result.csv",
    },
    {
        "id": "10_investment_trust_sprint",
        "name": "投信認養集中度爆發飆股策略",
        "script": ROOT_DIR / "strategies" / "10_investment_trust_sprint" / "screener.py",
        "export": "result.csv",
    },
]

def run_strategy(strat_info, line=False, force=False):
    print("\n" + "=" * 70)
    print(f"🚀 [執行策略] {strat_info['name']}")
    print(f"📁 腳本位置: {strat_info['script']}")
    print("=" * 70)

    if not strat_info['script'].exists():
        print(f"[X] 找不到腳本: {strat_info['script']}")
        return False

    cmd = [
        sys.executable,
        str(strat_info['script']),
        "--export",
        strat_info['export'],
    ]
    if line:
        cmd.append("--line")
    if force:
        cmd.append("--force")

    try:
        # 在策略所在目錄內執行，確保相對路徑正常
        cwd = strat_info['script'].parent
        ret = subprocess.run(cmd, cwd=cwd, check=True)
        print(f"[✓] {strat_info['name']} 執行成功！")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[!] {strat_info['name']} 執行失敗，錯誤代碼: {e.returncode}")
        return False
    except Exception as e:
        print(f"[!] 執行過程發生未預期錯誤: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="台股選股策略總指揮 - 一鍵執行所有策略")
    parser.add_argument("--line", action="store_true", help="執行後推播各策略結果至 LINE")
    parser.add_argument("--strategy", type=str, default=None, help="指定單一策略執行 (01, 02, 03, 04)")
    parser.add_argument("--force", action="store_true", help="強制執行策略，忽略台股休市守衛檢查")
    args = parser.parse_args()

    print(f"============================================================")
    print(f" 🎯 台股量化選股策略最高總指揮中心 - 全策略排程巡邏")
    print(f" 🕒 啟動時間: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"============================================================")

    from market_schedule import check_market_open_or_exit
    if not check_market_open_or_exit(context_name="全策略盤後選股排程", allow_force=True):
        return

    targets = list(STRATEGIES)
    known_ids = {s["id"] for s in targets}
    strat_root = ROOT_DIR / "strategies"
    if strat_root.is_dir():
        for child in sorted(strat_root.iterdir()):
            if child.is_dir() and child.name not in known_ids and not child.name.startswith(".") and child.name != "legacy":
                screener_py = child / "screener.py"
                if screener_py.exists():
                    targets.append({
                        "id": child.name,
                        "name": f"動態載入策略 ({child.name})",
                        "script": screener_py,
                        "export": "result.csv",
                    })
    if args.strategy:
        targets = [s for s in targets if args.strategy in s['id']]
        if not targets:
            print(f"[!] 找不到符合 '{args.strategy}' 的策略！")
            return

    success_count = 0
    for strat in targets:
        if run_strategy(strat, line=args.line, force=args.force):
            success_count += 1

    print("\n" + "=" * 70)
    print(f"🏁 總指揮中心執行總結: 完成 {success_count}/{len(targets)} 項策略。")
    print("=" * 70)

    # 自動跨日滾動校準雷達監控池四大防線與移動停利
    try:
        from radar_manager import rollover_watchlist_defense
        rollover_watchlist_defense(verbose=True)
    except Exception as e:
        print(f"[!] 雷達監控池跨日滾動校準跳過或失敗: {e}")

    # 自動同步至量化策略實驗室：執行盤後選股池自動回測體檢並更新明日 (T+1) 買賣建議
    try:
        from lab.data.storage import MarketStorage
        from lab.data.loader import MarketDataLoader
        from lab.engine.fusion import ScreenerFusionBridge

        lab_storage = MarketStorage()
        lab_loader = MarketDataLoader(storage=lab_storage)
        adv_report = ScreenerFusionBridge.generate_next_day_advisor_report(
            storage=lab_storage,
            loader=lab_loader,
            top_n_per_strategy=2,
        )
        out_csv = ROOT_DIR / "lab" / "data" / "tomorrow_trading_advisor.csv"
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        out_csv.write_text(adv_report["csv_content"], encoding="utf-8-sig")

        # 若啟用 --line 則同步將明日 (T+1) 買賣建議戰報推播至 LINE
        if args.line:
            try:
                line_text = ScreenerFusionBridge.format_advisor_line_text(adv_report)
                from line_sender import send_to_line
                send_to_line(line_text, strategy="01")
                print("[✓] 已成功推播量化實驗室明日買賣建議決策戰報至 LINE！")
            except Exception as e_push:
                print(f"[!] 量化實驗室明日買賣建議 LINE 推播跳過或失敗: {e_push}")
        print(
            f"[✓] 量化實驗室自動回測完成：共檢驗 {adv_report['total_evaluated']} 檔，"
            f"🟢明日優先買進 {adv_report['strong_buy_count']} 檔，已寫入 {out_csv}"
        )
    except Exception as e:
        print(f"[!] 量化實驗室自動回測同步跳過或失敗: {e}")

    # 自動同步每日精選標的至籌碼K線 (CMoney) 自選股
    try:
        from cmoney_sync import sync_stocks_to_cmoney
        sync_stocks_to_cmoney(verbose=True)
    except Exception as e:
        print(f"[!] 籌碼K線自選股同步跳過或失敗: {e}")

if __name__ == '__main__':
    main()
