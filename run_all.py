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
        "name": "布林通道下軌超跌反轉策略 (V2.0)",
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
]

def run_strategy(strat_info, line=False):
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
    parser.add_argument("--strategy", type=str, default=None, help="指定單一策略執行 (01, 02, 03)")
    args = parser.parse_args()

    print(f"============================================================")
    print(f" 🎯 台股量化選股策略最高總指揮中心 - 全策略排程巡邏")
    print(f" 🕒 啟動時間: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"============================================================")

    targets = STRATEGIES
    if args.strategy:
        targets = [s for s in STRATEGIES if args.strategy in s['id']]
        if not targets:
            print(f"[!] 找不到符合 '{args.strategy}' 的策略！")
            return

    success_count = 0
    for strat in targets:
        if run_strategy(strat, line=args.line):
            success_count += 1

    print("\n" + "=" * 70)
    print(f"🏁 總指揮中心執行總結: 完成 {success_count}/{len(targets)} 項策略。")
    print("=" * 70)

if __name__ == '__main__':
    main()
