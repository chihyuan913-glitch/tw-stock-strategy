#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 04：可轉債 (CB) 定價伏擊盤中即時雷達與到價推播系統 (Intraday Real-time Scanner)
適用時段：09:00 ~ 13:35 (台股交易時段)
核心功能：
1. 即時追蹤已宣布/生效/已定價之 CB 候選股盤中走勢。
2. 盤中即時比對「操盤四大防線」：
   - 🟢【伏擊進場觸發】：現價落入預設建倉價位區間。
   - 🔵【右側加碼觸發】：現價向上突破加碼價位 (破高/站穩 5MA)。
   - 🔴【分批停利觸發】：現價達標 TP1 (轉換價上方) 或 TP2 (波段前高)。
   - 🛑【緊急停損警報】：現價跌破關鍵停損底線。
   - ⏳【空間極限觸發】：現價達標轉換價 130% 逼轉強制贖回線。
3. 強制附帶「個股最後執行死線 (Exit Deadline)」防轉股賣壓。
4. 防重複洗版：同一標的之同一事件類型，當日僅推播一次。
"""

import sys
import os
import time
import datetime
import json
import urllib.request
import argparse
import pandas as pd
import numpy as np

# 加入專案根目錄至 sys.path 以便共用模組
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from line_sender import send_to_line

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

POOL_FILE = os.path.join(os.path.dirname(__file__), 'cb_pool.json')
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')

def load_cb_targets():
    """載入監控標的與操盤價位"""
    targets = {}
    
    # 優先從最新的 result.csv 載入精準四大價位
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
            for _, row in df.iterrows():
                code = str(row['代號']).strip()
                # 解析進場區間 "1635.0 ～ 1710.0"
                entry_str = str(row['進場價位'])
                entry_low, entry_high = None, None
                if '～' in entry_str:
                    parts = entry_str.split('～')
                    entry_low = float(parts[0].strip())
                    entry_high = float(parts[1].strip())
                else:
                    entry_low = float(entry_str) * 0.98
                    entry_high = float(entry_str)
                    
                targets[code] = {
                    'code': code,
                    'name': str(row['名稱']).strip(),
                    'stars': str(row.get('星級', '★★★★☆')),
                    'score': row.get('評分', 80),
                    'entry_low': entry_low,
                    'entry_high': entry_high,
                    'addon_price': float(row['加碼價位']),
                    'tp1_price': float(row['停利TP1']),
                    'tp2_price': float(row['停利TP2']),
                    'sl_price': float(row['停損價位']),
                    'lockup_end': str(row['最後執行死線']),
                    'target_130': float(row['空間極限130%']),
                    'exit_rule': str(row.get('退場規則說明', '')),
                    'cb_name': str(row.get('CB期次', '')),
                    'purpose': str(row.get('資金用途', ''))
                }
            print(f"[✓] 已自 result.csv 載入 {len(targets)} 檔精準監控標的。")
            return targets
        except Exception as e:
            print(f"[!] 讀取 result.csv 失敗，改用 cb_pool.json: {e}")

    # 若無 result.csv 則自 cb_pool.json 載入基礎設定
    if os.path.exists(POOL_FILE):
        with open(POOL_FILE, 'r', encoding='utf-8') as f:
            pool = json.load(f)
            for item in pool:
                code = str(item['code']).strip()
                conv_p = item.get('conversion_price') or 100.0
                targets[code] = {
                    'code': code,
                    'name': item['name'],
                    'stars': '★★★★☆',
                    'score': 80,
                    'entry_low': conv_p * 0.98,
                    'entry_high': conv_p * 1.03,
                    'addon_price': conv_p * 1.05,
                    'tp1_price': conv_p * 1.10,
                    'tp2_price': conv_p * 1.20,
                    'sl_price': conv_p * 0.95,
                    'lockup_end': item.get('lockup_end_date', '掛牌滿3個月當日'),
                    'target_130': conv_p * 1.30,
                    'exit_rule': item.get('exit_rule', ''),
                    'cb_name': item.get('cb_name', ''),
                    'purpose': item.get('purpose', '')
                }
    return targets

def fetch_realtime_quote(code, market='TW'):
    """從 Yahoo Finance 抓取盤中最新行情與高低點"""
    suffixes = [market, 'TW', 'TWO']
    for suffix in suffixes:
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{code}.{suffix}?interval=1m&range=1d'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                meta = data['chart']['result'][0]['meta']
                price = meta.get('regularMarketPrice')
                if price is not None:
                    return {
                        'price': round(float(price), 1),
                        'high': round(float(meta.get('regularMarketDayHigh', price)), 1),
                        'low': round(float(meta.get('regularMarketDayLow', price)), 1),
                        'vol': int(meta.get('regularMarketVolume', 0) // 1000),
                        'time': datetime.datetime.now().strftime('%H:%M:%S')
                    }
        except Exception:
            continue
    return None

def send_intraday_price_alert(target, rt, event_type, event_title, event_desc):
    """發送盤中到價 LINE 推播卡片 (包含操盤指引與強制退場死線)"""
    code = target['code']
    name = target['name']
    stars = target['stars']
    price = rt['price']
    high_p = rt['high']
    low_p = rt['low']
    cb_name = target['cb_name']
    
    msg_lines = [
        "╔═══════════════════════╗",
        "║  🚨【盤中雷達】可轉債CB到價即時快訊  ║",
        "╚═══════════════════════╝",
        f"📍 監控標的：{code} {name} ｜ {stars}",
        f"🔥 觸發事件：【{event_title}】",
        f"💵 即時現價：{price} 元 (今日高: {high_p} / 低: {low_p})",
        f"📌 CB 期次：{cb_name}",
        "━━━━━━━━━━━━━━━━━━━━",
        "🎯 實戰操盤執行指引："
    ]

    # 動態指引標籤
    entry_tag = " ◄◄ 【盤中已達標！】" if event_type == "ENTRY" else ""
    addon_tag = " ◄◄ 【突破加碼點！】" if event_type == "ADDON" else ""
    tp1_tag = " ◄◄ 【TP1 已達標！】" if event_type == "TP1" else ""
    tp2_tag = " ◄◄ 【TP2 已達標！】" if event_type == "TP2" else ""
    sl_tag = " ◄◄ 【破線停損警戒！】" if event_type == "STOP_LOSS" else ""

    msg_lines.extend([
        f"├ 🟢 進場區間：{target['entry_low']} ～ {target['entry_high']} 元{entry_tag}",
        f"├ 🔵 加碼價位：{target['addon_price']} 元{addon_tag}",
        f"├ 🔴 停利目標：TP1 {target['tp1_price']} 元{tp1_tag} ｜ TP2 {target['tp2_price']} 元{tp2_tag}",
        f"└ 🛑 停損防守：{target['sl_price']} 元{sl_tag}",
        "────────────────────",
        "⏳ 個股最後執行死線 (強制退場)：",
        f"• 時間極限：{target['lockup_end']}",
        f"  └ 說明：{target['exit_rule']}",
        f"• 空間極限：{target['target_130']} 元 (達標強制獲利終止)",
        "────────────────────",
        f"💡【實戰提醒】{event_desc}",
        f"⏰ 偵測時間：{rt['time']}",
        "━━━━━━━━━━━━━━━━━━━━"
    ])

    message = "\n".join(msg_lines)
    print(f"\n[!] 觸發盤中到價警報：{code} {name} 事件: {event_title} 現價: {price}")
    send_to_line(message, strategy="04")

def scan_once(targets, notified_events, force_test=False):
    """執行一次盤中到價掃描"""
    now_str = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"\n[*] [{now_str}] 正在檢查 {len(targets)} 檔 CB 標的即時到價情況...")

    for code, target in targets.items():
        rt = fetch_realtime_quote(code)
        if not rt:
            continue

        price = rt['price']
        print(f"  • {code} {target['name']}: 現價 {price} (進場 {target['entry_low']}~{target['entry_high']}, 加碼 {target['addon_price']}, TP1 {target['tp1_price']}, 停損 {target['sl_price']})")

        events_for_code = notified_events.setdefault(code, set())

        # 1. 停損警戒判定 (優先級最高)
        if price <= target['sl_price'] and 'STOP_LOSS' not in events_for_code:
            events_for_code.add('STOP_LOSS')
            send_intraday_price_alert(
                target, rt, 'STOP_LOSS', '🛑 跌破防守線 - 停損離場警報',
                '股價已跌破關鍵停損點！請嚴守紀律立即執行停損離場，保留實力。'
            )
            continue

        # 2. 空間極限 130% 逼轉判定
        if price >= target['target_130'] and 'LIMIT_130' not in events_for_code:
            events_for_code.add('LIMIT_130')
            send_intraday_price_alert(
                target, rt, 'LIMIT_130', '⏳ 觸及130%空間極限 - 強制清倉',
                '股價已達轉換價 130%，公司隨時將啟動強制贖回條款，主力目標已達成，請全數獲利了結！'
            )
            continue

        # 3. 停利 TP2 判定
        if price >= target['tp2_price'] and 'TP2' not in events_for_code:
            events_for_code.add('TP2')
            send_intraday_price_alert(
                target, rt, 'TP2', '🔴 達成第二停利目標 TP2',
                '股價已達前波起跌頸線壓力區，建議再獲利了結 1/3，並將剩餘部位停損點上移至成本保本。'
            )
            continue

        # 4. 停利 TP1 判定
        if price >= target['tp1_price'] and 'TP1' not in events_for_code:
            events_for_code.add('TP1')
            send_intraday_price_alert(
                target, rt, 'TP1', '🔴 達成第一停利目標 TP1',
                '股價已觸碰轉換價格阻力區，短線壓盤反彈目標達成，建議先落袋為安 1/3 至 1/2 部位！'
            )
            continue

        # 5. 右側突破加碼判定
        if price >= target['addon_price'] and 'ADDON' not in events_for_code:
            events_for_code.add('ADDON')
            send_intraday_price_alert(
                target, rt, 'ADDON', '🔵 帶量突破加碼點',
                '現價已突破定價週高點/5MA阻力，壓盤結束、右側動能展開，可依計畫加碼 70%！'
            )
            continue

        # 6. 左側伏擊進場區間判定
        if target['entry_low'] <= price <= target['entry_high'] and 'ENTRY' not in events_for_code:
            events_for_code.add('ENTRY')
            send_intraday_price_alert(
                target, rt, 'ENTRY', '🟢 伏擊進場買點觸發',
                '現價已回測至季線/轉換價黃金伏擊區，賣壓竭盡量縮，可依計畫分批試單 30% 建倉！'
            )
            continue

        # 測試模式：強制推播首選標的
        if force_test and 'TEST' not in events_for_code:
            events_for_code.add('TEST')
            send_intraday_price_alert(
                target, rt, 'ENTRY', '🟢 測試推播：伏擊進場買點',
                '【盤中雷達連線測試】系統即時監控中，到價將即時推播！'
            )
            break

def run_intraday_scanner(interval=180, once=False, force_test=False):
    """盤中雷達常駐迴圈"""
    print("=" * 70)
    print("🚀 啟動【策略04：可轉債 (CB) 定價伏擊盤中即時雷達與到價推播系統】")
    print("=" * 70)

    targets = load_cb_targets()
    if not targets:
        print("[X] 無可監控之 CB 標的，請確認 result.csv 或 cb_pool.json。")
        return

    notified_events = {}

    while True:
        now = datetime.datetime.now()
        now_time = now.time()
        start_time = datetime.time(9, 0)
        end_time = datetime.time(13, 35)

        # 非開盤時間防護 (若不是 --once 或 --force-test)
        if not (once or force_test) and (now_time < start_time or now_time > end_time):
            if now_time > end_time:
                print(f"[*] [{now.strftime('%H:%M:%S')}] 今日盤中交易已結束 (超過 13:35)，盤中雷達休眠。")
                break
            else:
                print(f"[*] [{now.strftime('%H:%M:%S')}] 尚未開盤，等待 09:00 開盤中...")
                time.sleep(30)
                continue

        scan_once(targets, notified_events, force_test=force_test)

        if once or force_test:
            print("\n[✓] 測試掃描完成！")
            break

        print(f"[*] 等待 {interval} 秒後進行下一次盤中巡邏... (按 Ctrl+C 可停止)")
        time.sleep(interval)

def main():
    parser = argparse.ArgumentParser(description="策略 04 可轉債 (CB) 盤中到價即時雷達推播")
    parser.add_argument("--interval", type=int, default=180, help="輪詢間隔秒數 (預設 180 秒)")
    parser.add_argument("--once", action="store_true", help="僅執行一次掃描後退出")
    parser.add_argument("--test-push", "--force-test", dest="force_test", action="store_true", help="強制發送一次測試到價推播至 LINE 驗證")
    args = parser.parse_args()

    run_intraday_scanner(interval=args.interval, once=args.once, force_test=args.force_test)

if __name__ == '__main__':
    main()
