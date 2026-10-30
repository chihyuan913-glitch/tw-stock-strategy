#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 04：可轉債 (CB) 定價伏擊盤中即時雷達與到價推播系統 V4.0 全域規範版
================================================================================
適用時段：09:00 ~ 13:35 (台股交易時段)
核心功能：
1. 即時追蹤已宣布/生效/已定價之 CB 候選股盤中走勢 (由 result.csv 自動連動)。
2. 盤中即時比對「操盤四大防線」：
   - 🟢【伏擊進場觸發】：現價落入預設建倉價位區間 (買進上限以內)。
   - 🔵【右側加碼觸發】：現價向上突破加碼價位 (破高/站穩 5MA)。
   - 🔴【分批停利觸發】：現價達標 TP1 (轉換價上方) 或 TP2 (波段主升)。
   - 🛑【緊急停損警報】：現價跌破關鍵停損底線 (優先級最高)。
   - ⏳【空間極限觸發】：現價達標轉換價 130% 逼轉強制贖回線。
3. 【高頻防毛刺過濾 (GLOBAL_TICK_GUARD)】：過濾 1-tick 假突破誘多與假跌破甩轎。
4. 【台股未開盤日防護 (Rule 21)】：休市日自動攔截不啟動、不推送打擾。
5. 【明日首選核心標記 (Rule 24)】：Tier-1 核心標的高亮標註 🔥【今日核心推薦首選】。
6. 【防重複洗版】：同一標的之同一事件類型，當日僅推播一次。
================================================================================
"""

import sys
import os
import time
import datetime
import json
import urllib.request
import argparse
from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

# 加入專案根目錄至 sys.path 以便共用模組
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from line_sender import (
    send_to_line,
    build_price_alert_card,
    format_clean_price_alert_text,
    build_aggregated_alert_card,
    format_clean_aggregated_text
)
from algo_guards import GLOBAL_TICK_GUARD
from market_schedule import check_market_open_or_exit, should_suppress_push

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

STRATEGY_DIR = Path(__file__).resolve().parent
POOL_FILE = STRATEGY_DIR / "cb_pool.json"
CSV_FILE = STRATEGY_DIR / "result.csv"


def parse_price_num(val: Any, default: float = 0.0) -> float:
    """輔助解析可能包含區間符號『～』或括號備註之數字"""
    if val is None or pd.isna(val):
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if '～' in s:
        s = s.split('～')[-1].split('(')[0].strip()
    elif '(' in s:
        s = s.split('(')[0].strip()
    try:
        return float(s)
    except Exception:
        return default


def load_cb_targets() -> Dict[str, Dict[str, Any]]:
    """載入監控標的與操盤價位"""
    targets = {}

    # 1. 優先從最新的 result.csv 載入精準四大防線價位
    if CSV_FILE.exists():
        try:
            df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
            for _, row in df.iterrows():
                code = str(row['代號']).strip()
                if not code or code == 'nan':
                    continue

                entry_val = row.get('建議進場價') if pd.notna(row.get('建議進場價')) else row.get('進場價位')
                addon_val = row.get('動能加碼價') if pd.notna(row.get('動能加碼價')) else row.get('加碼價位')
                tp1_val = row.get('停利TP1') if pd.notna(row.get('停利TP1')) else row.get('第一次停利價位')
                tp2_val = row.get('停利TP2') if pd.notna(row.get('停利TP2')) else row.get('第二次停利價位')
                sl_val = row.get('嚴格停損價') if pd.notna(row.get('嚴格停損價')) else row.get('停損價位')
                cap130_val = row.get('空間極限130%', 0)

                targets[code] = {
                    'code': code,
                    'name': str(row.get('名稱', '')).strip(),
                    'tier_label': str(row.get('梯隊標籤', '🟢 Tier-1 核心精選')),
                    'stars': str(row.get('星級', '★★★★☆')),
                    'score': row.get('評分', 80),
                    'entry_price': parse_price_num(entry_val),
                    'addon_price': parse_price_num(addon_val),
                    'tp1_price': parse_price_num(tp1_val),
                    'tp2_price': parse_price_num(tp2_val),
                    'sl_price': parse_price_num(sl_val),
                    'lockup_end': str(row.get('最後執行死線', '掛牌滿3個月當日')),
                    'target_130': parse_price_num(cap130_val),
                    'exit_rule': str(row.get('退場規則說明', '')),
                    'cb_name': str(row.get('CB期次', '')),
                    'purpose': str(row.get('資金用途', '')),
                    'futures_contract': str(row.get('期貨契約', '')),
                    'futures_margin': row.get('1口保證金(約)', 0)
                }
            print(f"[✓] 已自 result.csv 載入 {len(targets)} 檔精準監控標的。")
            return targets
        except Exception as e:
            print(f"[!] 讀取 result.csv 失敗，改用 cb_pool.json: {e}")

    # 2. 若無 result.csv 則自 cb_pool.json 載入基礎設定
    if POOL_FILE.exists():
        try:
            with open(POOL_FILE, 'r', encoding='utf-8') as f:
                pool = json.load(f)
                for item in pool:
                    code = str(item['code']).strip()
                    conv_p = float(item.get('conversion_price') or 100.0)
                    targets[code] = {
                        'code': code,
                        'name': item.get('name', ''),
                        'tier_label': '🟢 Tier-1 核心精選',
                        'stars': '★★★★☆',
                        'score': 80,
                        'entry_price': round(conv_p * 1.015, 2),
                        'addon_price': round(conv_p * 1.035, 2),
                        'tp1_price': round(conv_p * 1.12, 2),
                        'tp2_price': round(conv_p * 1.22, 2),
                        'sl_price': round(conv_p * 0.92, 2),
                        'lockup_end': item.get('lockup_end_date', '掛牌滿3個月當日'),
                        'target_130': round(conv_p * 1.30, 2),
                        'exit_rule': item.get('exit_rule', ''),
                        'cb_name': item.get('cb_name', ''),
                        'purpose': item.get('purpose', '')
                    }
        except Exception as e:
            print(f"[!] 讀取 cb_pool.json 失敗: {e}")
    return targets


def fetch_realtime_quote(code: str, market: str = 'TW') -> Optional[Dict[str, Any]]:
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
                        'price': round(float(price), 2),
                        'high': round(float(meta.get('regularMarketDayHigh', price)), 2),
                        'low': round(float(meta.get('regularMarketDayLow', price)), 2),
                        'vol': int(meta.get('regularMarketVolume', 0) // 1000),
                        'time': datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
                    }
        except Exception:
            continue
    return None


def send_intraday_price_alert(target: Dict[str, Any], rt: Dict[str, Any], event_type: str, event_title: str, event_desc: str):
    """發送盤中到價 LINE 推播卡片 (採用大字體、零引線、四大價位一目了然之高對比 Flex 卡)"""
    code = target['code']
    name = target['name']
    price = rt['price']

    # Rule XXIV: 明日首選與核心推薦分級警報標記
    tier = str(target.get('tier_label', ''))
    stars = str(target.get('stars', ''))
    if 'Tier-1' in tier or '★★★★★' in stars:
        event_title = f"🔥【今日核心推薦首選】{event_title}"

    print(f"\n[!] 觸發盤中到價警報 (策略04): {code} {name} 事件: {event_title} 現價: {price}")

    card = build_price_alert_card(target, rt, event_type, event_title, event_desc, strategy_id="04")
    success = send_to_line(card, strategy="04")
    if not success:
        clean_text = format_clean_price_alert_text(target, rt, event_type, event_title, event_desc, strategy_id="04")
        send_to_line(clean_text, strategy="04")


def send_aggregated_price_alerts(alerts: list):
    """將同週期多檔 CB 伏擊到價事件整合為單一戰訊卡發送，大幅節省 90% LINE 訊息則數並避免洗版"""
    now_str = datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
    print(f"\n[⚡] 發送可轉債伏擊多檔合併戰訊卡 (共 {len(alerts)} 檔標的，僅消耗 1 則訊息額度！)")

    card = build_aggregated_alert_card(alerts, strategy_id="04", now_str=now_str)
    success = send_to_line(card, strategy="04")
    if not success:
        clean_text = format_clean_aggregated_text(alerts, strategy_id="04", now_str=now_str)
        send_to_line(clean_text, strategy="04")


def scan_once(targets: Dict[str, Dict[str, Any]], notified_events: dict, force_test: bool = False):
    """執行一次盤中到價掃描 (支援多檔合併發送)"""
    now_str = datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
    print(f"\n[*] [{now_str}] 正在檢查 {len(targets)} 檔 CB 標的即時到價情況...")

    triggered_alerts = []

    for code, target in targets.items():
        rt = fetch_realtime_quote(code)
        if not rt:
            continue

        price = rt['price']
        fut_contract = str(target.get('futures_contract', '')).strip()
        fut_label = f" [股期: {fut_contract}]" if fut_contract and fut_contract not in ('--', 'nan', 'None') else ""
        print(f"  • {code} {target['name']}{fut_label}: 現價 {price} (進場上限 {target['entry_price']}, 加碼 {target['addon_price']}, TP1 {target['tp1_price']}, 停損 {target['sl_price']})")

        events_for_code = notified_events.setdefault(code, set())

        # 1. 停損警戒判定 (優先級最高)
        if price <= target['sl_price'] and 'STOP_LOSS' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'STOP_LOSS', price, target['sl_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('STOP_LOSS')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'STOP_LOSS',
                'event_title': '🛑 跌破防守線 - 停損離場警報',
                'event_desc': '股價已跌破關鍵停損點！請嚴守紀律立即執行停損離場，保留實力。'
            })
            continue

        # 2. 空間極限 130% 逼轉判定
        if target['target_130'] > 0 and price >= target['target_130'] and 'LIMIT_130' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'LIMIT_130', price, target['target_130'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('LIMIT_130')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'LIMIT_130',
                'event_title': '⏳ 觸及130%空間極限 - 強制清倉',
                'event_desc': '股價已達轉換價 130%，公司隨時將啟動強制贖回條款，主力逼轉目標已達成，請全數獲利了結！'
            })
            continue

        # 3. 停利 TP2 判定
        if price >= target['tp2_price'] and 'TP2' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'TP2', price, target['tp2_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('TP2')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'TP2',
                'event_title': '🔴 達成第二停利目標 TP2',
                'event_desc': '股價已達波段主升滿足區，建議再獲利了結 1/2，並將剩餘部位停損點上移至成本保本。'
            })
            continue

        # 4. 停利 TP1 判定
        if price >= target['tp1_price'] and 'TP1' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'TP1', price, target['tp1_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('TP1')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'TP1',
                'event_title': '🔴 達成第一停利目標 TP1',
                'event_desc': '股價已觸碰轉換價格阻力區，短線壓盤反彈目標達成，建議先落袋為安 1/3 至 1/2 部位！'
            })
            continue

        # 5. 右側突破加碼判定
        if price >= target['addon_price'] and 'ADDON' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'ADDON', price, target['addon_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('ADDON')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'ADDON',
                'event_title': '🔵 帶量突破加碼點',
                'event_desc': '現價已突破定價週高點/5MA阻力，壓盤結束、右側動能展開，可依計畫加碼 50%！'
            })
            continue

        # 6. 左側伏擊進場判定 (現價處於甜蜜建倉區間：建議買進上限價以下且穩守防守安全緩衝帶)
        min_entry_zone = max(target['sl_price'] * 1.01, target['entry_price'] * 0.97)
        if min_entry_zone <= price <= target['entry_price'] and 'ENTRY' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'ENTRY', price, target['entry_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('ENTRY')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'ENTRY',
                'event_title': '🟢 伏擊進場買點觸發',
                'event_desc': f'現價 {price} 元 處於甜蜜進場區間 ({round(min_entry_zone, 1)} ~ {target["entry_price"]} 元) 且穩守防守線，賣壓竭盡量縮，可直接以現價或限價立即進場建倉！'
            })
            continue

        # 測試模式：強制推播首選標的
        if force_test and 'TEST' not in events_for_code:
            events_for_code.add('TEST')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'TEST',
                'event_title': '🟢 測試推播：伏擊進場買點',
                'event_desc': '【盤中雷達連線測試】系統即時監控中，到價將即時推播！'
            })
            break

    # 派發推播：單檔發送詳細卡，多檔發送合併戰訊卡
    if not triggered_alerts:
        return

    if len(triggered_alerts) == 1 or force_test:
        a = triggered_alerts[0]
        send_intraday_price_alert(a['target'], a['rt'], a['event_code'], a['event_title'], a['event_desc'])
    else:
        send_aggregated_price_alerts(triggered_alerts)


def run_intraday_scanner(interval: int = 180, once: bool = False, force_test: bool = False, force: bool = False):
    """盤中雷達常駐迴圈"""
    print("=" * 70)
    print("🚀 啟動【策略04：可轉債 (CB) 定價伏擊盤中即時雷達與到價推播系統 V4.0】")
    print("=" * 70)

    # 檢查開盤日狀態 (Rule 21 未開盤日全面停止推送)
    if not (force or force_test):
        check_market_open_or_exit(force=False)

    targets = load_cb_targets()
    if not targets:
        print("[X] 無可監控之 CB 標的，請確認 result.csv 或 cb_pool.json。")
        return

    notified_events = {}

    while True:
        now = datetime.datetime.now(TZ_TW)
        now_time = now.time()
        start_time = datetime.time(9, 0)
        end_time = datetime.time(13, 35)

        # 非開盤時間防護 (若不是 --once 或 --force-test 或 --force)
        if not (once or force_test or force) and (now_time < start_time or now_time > end_time):
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
    parser = argparse.ArgumentParser(description="策略 04 可轉債 (CB) 盤中到價即時雷達推播 V4.0")
    parser.add_argument("--interval", type=int, default=180, help="輪詢間隔秒數 (預設 180 秒)")
    parser.add_argument("--once", action="store_true", help="僅執行一次掃描後退出")
    parser.add_argument("--test-push", "--force-test", dest="force_test", action="store_true", help="強制發送一次測試到價推播至 LINE 驗證")
    parser.add_argument("--force", action="store_true", help="強制執行，忽略休市日與開盤守門檢查")
    args = parser.parse_args()

    run_intraday_scanner(interval=args.interval, once=args.once, force_test=args.force_test, force=args.force)


if __name__ == '__main__':
    main()
