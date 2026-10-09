#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 全策略盤中到價即時雷達總調度引擎 (Master Intraday Runner)
功能：
1. 一鍵執行所有策略的盤中到價掃描。
2. 支援 --test-push 一鍵測試全策略 LINE 到價推播連線與卡片排版。
3. 支援 --once 單次快篩巡邏。
4. 支援排程或長駐輪詢。
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
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent

INTRADAY_STRATEGIES = [
    {
        "id": "01_bollinger_reversal",
        "name": "策略 01：布林通道下軌超跌反轉到價雷達",
        "script": ROOT_DIR / "strategies" / "01_bollinger_reversal" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "01_bollinger_reversal"
    },
    {
        "id": "02_institutional_momentum",
        "name": "策略 02：法人籌碼集中起漲到價雷達",
        "script": ROOT_DIR / "strategies" / "02_institutional_momentum" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "02_institutional_momentum"
    },
    {
        "id": "03_short_momentum",
        "name": "策略 03：弱勢破線做空與個股期貨避險到價雷達",
        "script": ROOT_DIR / "strategies" / "03_short_momentum" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "03_short_momentum"
    },
    {
        "id": "04_cb_pricing_ambush",
        "name": "策略 04：可轉債 (CB) 定價伏擊到價雷達",
        "script": ROOT_DIR / "strategies" / "04_cb_pricing_ambush" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "04_cb_pricing_ambush"
    },
]

def run_intraday_single(strat, extra_args):
    print("\n" + "=" * 70)
    print(f"🚀 [執行到價掃描] {strat['name']}")
    print(f"📁 腳本位置: {strat['script']}")
    print("=" * 70)

    if not strat['script'].exists():
        print(f"[X] 找不到腳本: {strat['script']}")
        return False

    cmd = [sys.executable, str(strat['script'])] + extra_args

    try:
        ret = subprocess.run(cmd, cwd=strat['cwd'], check=True)
        print(f"[✓] {strat['name']} 執行成功！")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[!] {strat['name']} 執行失敗，代碼: {e.returncode}")
        return False
    except Exception as e:
        print(f"[!] 發生未預期錯誤: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="台股總指揮中心 - 全策略盤中到價即時雷達總調度")
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔秒數 (預設 60 秒)")
    parser.add_argument("--once", action="store_true", help="執行單次快篩巡邏後結束")
    parser.add_argument("--test-push", "--force-test", dest="test_push", action="store_true", help="一鍵發送全策略模擬到價快訊至 LINE 測試連線")
    parser.add_argument("--strategy", type=str, default=None, help="指定單一策略 (01, 02, 03, 04)")
    args = parser.parse_args()

    print(f"============================================================")
    print(f" 🎯 台股量化策略總指揮中心 - 全策略盤中到價即時雷達")
    print(f" 🕒 啟動時間: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"============================================================")

    targets = INTRADAY_STRATEGIES
    if args.strategy:
        targets = [s for s in INTRADAY_STRATEGIES if args.strategy in s['id']]
        if not targets:
            print(f"[!] 找不到符合 '{args.strategy}' 的策略！")
            return

    extra_args = []
    if args.test_push:
        extra_args.append("--test-push")
    elif args.once:
        extra_args.append("--once")
    else:
        extra_args.extend(["--interval", str(args.interval)])

    success = 0
    for strat in targets:
        if run_intraday_single(strat, extra_args):
            success += 1

    print("\n" + "=" * 70)
    print(f"🏁 全策略到價調度總結: 完成 {success}/{len(targets)} 項策略。")
    print("=" * 70)

if __name__ == '__main__':
    main()
