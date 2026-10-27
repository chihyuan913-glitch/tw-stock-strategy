#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 10：投信認養集中度爆發飆股 - 盤中即時雷達與到價推播系統 (Intraday Trust Sprint Scanner)
================================================================================
適用時段：09:00 ~ 13:35 (台股盤中交易時段)
核心功能：
1. 自動載入最新盤後選股名單 (result.csv)，鎖定四大操盤防線。
2. 盤中即時連線證交所 MIS / Yahoo 報價，精準偵測到價事件：
   - 🟢【建倉進場觸發】：現價落入建倉上限買進區間。
   - 🔵【突破加碼觸發】：現價帶量突破今日高點續強。
   - 🔴【分批停利觸發】：現價攻抵 TP1 或 TP2 目標。
   - 🛑【嚴格停損警戒】：現價跌破關鍵停損點 (優先推播)。
3. 防重複洗版：同一標的同事件類型，當日僅推播一次。
4. 防高頻毛刺假突破：套用 GLOBAL_TICK_GUARD 雙次確認過濾量化甩轎。
5. 支援獨立 LINE 視窗分流 (strategy="10")。
================================================================================
"""

import sys
import os
import time
import datetime
import json
import urllib.request
import argparse
import pandas as pd
import yfinance as yf

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

# 加入專案根目錄至 sys.path 以引用共用模組
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from line_sender import (
    send_to_line,
    build_price_alert_card,
    format_clean_price_alert_text,
    build_aggregated_alert_card,
    format_clean_aggregated_text
)
from algo_guards import GLOBAL_TICK_GUARD

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

STRATEGY_ID = "10"
STRATEGY_NAME = "策略 10：投信認養爆發到價雷達"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')


def load_targets():
    """優先自 result.csv 載入最新選股名單與四大價位"""
    targets = {}
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
            for _, row in df.iterrows():
                code = str(row['證券代號']).strip()
                if not code or code == 'nan':
                    continue
                close_p = float(row.get('收盤價', 100.0))
                entry_p = float(row.get('進場價位', row.get('進場參考', close_p)))
                addon_p = float(row.get('加碼價位', round(close_p * 1.025, 2)))
                tp1_p = float(row.get('停利TP1', round(close_p * 1.08, 2)))
                tp2_p = float(row.get('停利TP2', round(close_p * 1.15, 2)))
                sl_p = float(row.get('停損價位', round(close_p * 0.955, 2)))
                ma20 = float(row.get('月線(20MA)', round(close_p * 0.98, 2)))
                bias_pct = float(row.get('月線乖離%', round((close_p - ma20) / ma20 * 100, 2)))

                targets[code] = {
                    'code': code,
                    'name': str(row.get('證券名稱', code)).strip(),
                    'entry_price': entry_p,
                    'addon_price': addon_p,
                    'tp1_price': tp1_p,
                    'tp2_price': tp2_p,
                    'sl_price': sl_p,
                    'ma20': ma20,
                    'bias_pct': bias_pct,
                    'trust_days': int(row.get('投信連買天數', 3)),
                    'trust_lots': int(row.get('今日投信買超(張)', 0)),
                    'trust_ratio': float(row.get('投信佔成交量%', 5.0))
                }
            if targets:
                print(f"[✓] 已自 result.csv 載入 {len(targets)} 檔投信認養精選標的。")
                return targets
        except Exception as e:
            print(f"[!] 讀取 result.csv 失敗: {e}")

    return targets


def fetch_intraday_quotes(symbols):
    """抓取即時盤中成交行情"""
    quotes = {}
    if not symbols:
        return quotes

    try:
        tse_keys = [f"tse_{c}.tw" for c in symbols]
        otc_keys = [f"otc_{c}.tw" for c in symbols]
        all_keys = "|".join(tse_keys + otc_keys)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={all_keys}&json=1&delay=0"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            msg_arr = data.get('msgArray', [])
            for item in msg_arr:
                code = item.get('c', '').strip()
                if code in symbols:
                    price_str = item.get('z', item.get('y', ''))
                    if price_str and price_str != '-':
                        price = float(price_str)
                        quotes[code] = {
                            'price': price,
                            'open': float(item.get('o', price)),
                            'high': float(item.get('h', price)),
                            'low': float(item.get('l', price)),
                            'time': item.get('t', '')
                        }
    except Exception:
        pass

    missing = [c for c in symbols if c not in quotes]
    if missing:
        try:
            tickers = [f"{c}.TW" for c in missing]
            data = yf.download(tickers, period='1d', interval='1m', progress=False)
            if not data.empty:
                for c in missing:
                    t_key = f"{c}.TW"
                    try:
                        sub = data if len(missing) == 1 else (data.xs(t_key, axis=1, level=1) if t_key in data.columns.levels[1] else None)
                        if sub is not None and not sub.empty:
                            last_row = sub.dropna().iloc[-1]
                            price = float(last_row['Close'])
                            quotes[c] = {
                                'price': price,
                                'open': float(last_row['Open']),
                                'high': float(last_row['High']),
                                'low': float(last_row['Low']),
                                'time': datetime.datetime.now().strftime('%H:%M:%S')
                            }
                    except Exception:
                        pass
        except Exception:
            pass

    return quotes


def send_intraday_price_alert(target, rt, event_type, event_title, event_desc):
    """發送盤中到價 LINE 推播卡片"""
    code = target['code']
    name = target['name']
    price = rt['price']
    print(f"\n[!] 觸發盤中到價警報 (策略10): {code} {name} 事件: {event_title} 現價: {price:.2f}")

    card = build_price_alert_card(target, rt, event_type, event_title, event_desc, strategy_id=STRATEGY_ID)
    success = send_to_line(card, strategy=STRATEGY_ID)
    if not success:
        clean_text = format_clean_price_alert_text(target, rt, event_type, event_title, event_desc, strategy_id=STRATEGY_ID)
        send_to_line(clean_text, strategy=STRATEGY_ID)


def scan_intraday(targets, notified_events, force_test=False):
    """執行單次盤中到價掃描"""
    if not targets and not force_test:
        print("[!] 監控母池為空。")
        return

    if force_test:
        mock_target = {
            'code': '2330',
            'name': '台積電(測試)',
            'entry_price': 1050.0,
            'addon_price': 1065.0,
            'tp1_price': 1100.0,
            'tp2_price': 1150.0,
            'sl_price': 1010.0,
            'ma20': 1020.0,
            'bias_pct': 2.94,
            'trust_days': 4,
            'trust_lots': 1500,
            'trust_ratio': 8.5
        }
        mock_rt = {
            'price': 1050.0,
            'open': 1045.0,
            'high': 1055.0,
            'low': 1040.0,
            'time': datetime.datetime.now().strftime('%H:%M:%S')
        }
        send_intraday_price_alert(
            mock_target,
            mock_rt,
            'ENTRY',
            '🟢 測試：建倉買點觸發',
            '【盤中雷達連線測試】系統即時監控中，到達四大防線將即時推播！'
        )
        return

    codes = list(targets.keys())
    rt_quotes = fetch_intraday_quotes(codes)
    triggered_alerts = []

    for code, target in targets.items():
        rt = rt_quotes.get(code)
        if not rt or rt.get('price') is None:
            continue
        price = rt['price']
        events_for_code = notified_events.setdefault(code, set())

        # 1. 停損警戒判定 (優先級最高)
        if price <= target['sl_price'] and 'STOP_LOSS' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'STOP_LOSS', price, target['sl_price'], force_pass=False)
            if not confirmed:
                continue
            events_for_code.add('STOP_LOSS')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'STOP_LOSS',
                'event_title': '🛑 跌破關鍵防線 - 停損離場警報',
                'event_desc': '股價跌破關鍵停損點！投信籌碼防守失守，請嚴守風控紀律立即執行停損！'
            })
            continue

        # 2. 停利 TP2 判定
        if price >= target['tp2_price'] and 'TP2' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'TP2', price, target['tp2_price'], force_pass=False)
            if not confirmed:
                continue
            events_for_code.add('TP2')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'TP2',
                'event_title': '🔴 達成第二停利目標 TP2 (+15%)',
                'event_desc': '投信認養高潮噴發！已達主升波段滿足點，建議全數獲利了結，落袋為安！'
            })
            continue

        # 3. 停利 TP1 判定
        if price >= target['tp1_price'] and 'TP1' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'TP1', price, target['tp1_price'], force_pass=False)
            if not confirmed:
                continue
            events_for_code.add('TP1')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'TP1',
                'event_title': '🔴 達成第一停利目標 TP1 (+8%)',
                'event_desc': '到達短波段獲利目標！建議分批調節 1/3~1/2，鎖定投信作帳波段利潤！'
            })
            continue

        # 4. 突破加碼判定
        if price >= target['addon_price'] and 'ADDON' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'ADDON', price, target['addon_price'], force_pass=False)
            if not confirmed:
                continue
            events_for_code.add('ADDON')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'ADDON',
                'event_title': '🔵 突破今日高點 - 動能加碼確認',
                'event_desc': '帶量突破今日高點續強，投信買盤積極推升，右側動能加碼點確立！'
            })
            continue

        # 5. 建倉買點判定
        if price <= target['entry_price'] and 'ENTRY' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'ENTRY', price, target['entry_price'], force_pass=False)
            if not confirmed:
                continue
            events_for_code.add('ENTRY')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'ENTRY',
                'event_title': '🟢 進入甜蜜建倉區 - 買點浮現',
                'event_desc': '現價回測進入安全建倉區，投信連續吃貨認養，可分批佈局建倉！'
            })
            continue

    if not triggered_alerts:
        return

    for a in triggered_alerts:
        send_intraday_price_alert(a['target'], a['rt'], a['event_code'], a['event_title'], a['event_desc'])


def main():
    parser = argparse.ArgumentParser(description=STRATEGY_NAME)
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔 (秒)，預設 60 秒")
    parser.add_argument("--once", action="store_true", help="執行單次巡檢後結束")
    parser.add_argument("--test-push", action="store_true", help="發送測試到價推播以驗證 LINE 連線")

    args = parser.parse_args()

    targets = load_targets()
    notified_events = {}

    if args.test_push:
        print("[*] 正在發送【策略 10 投信認養到價雷達】連線測試推播...")
        scan_intraday({}, notified_events, force_test=True)
        print("[✓] 測試推播已發送！")
        return

    if args.once:
        print("[*] 執行單次巡邏掃描...")
        scan_intraday(targets, notified_events)
        print("[✓] 巡邏完畢。")
        return

    print(f"[*] 啟動【{STRATEGY_NAME}】長駐即時監控（每 {args.interval} 秒輪詢一次）...")
    while True:
        try:
            now_tw = datetime.datetime.now(TZ_TW)
            curr_hm = now_tw.hour * 60 + now_tw.minute
            if not (9 * 60 <= curr_hm <= 13 * 60 + 35):
                if curr_hm > (13 * 60 + 35):
                    print("[✓] 今日台股收盤 (13:35)，即時雷達安全下線。")
                    break

            scan_intraday(targets, notified_events)
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[*] 使用者中斷，雷達停止。")
            break
        except Exception as e:
            print(f"[!] 輪詢過程異常: {e}")
            time.sleep(10)


if __name__ == '__main__':
    main()
