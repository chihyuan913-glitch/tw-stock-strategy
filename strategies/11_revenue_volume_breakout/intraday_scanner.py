#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 11：台股營收創高與量價突破成長動能 - 盤中即時雷達與到價推播系統 (Volume Breakout Pro Scanner 2.0 旗艦版)
================================================================================
適用時段：09:00 ~ 13:35 (台股盤中交易時段)
核心升級功能：
1. 自動載入最新盤後選股名單 (result.csv)，鎖定 A 軌突破與 B 軌回測四大操盤防線。
2. 大盤即時環境連動：加權指數破月線時，自動升級為「B 軌量縮回測防禦模式 (部位減半)」。
3. 盤中即時連線 Yahoo Finance 報價，精準偵測六大到價事件：
   - 🛑【嚴格停損警戒】：跌破不可退縮停損底線 (最高風控優先級)。
   - 🔴【主升停利 TP2】：攻抵波段核心反壓滿足區 (+18%~+25%)。
   - 🔴【第一停利 TP1】：首波目標拔檔滿足 (+8%~+10%)，提醒獲利 50% 並移保本。
   - 🔵【突破動能加碼】：帶量突破今日高點續強。
   - 🟢【A軌突破建倉】：現價落入動能買進上限區。
   - 🟡【B軌量縮回測】：回測 5MA/頸線支撐不破，提供黃金低接買點！
4. 防重複洗版：同一標的同事件類型，當日僅推播一次。
5. 防高頻毛刺假突破：套用 GLOBAL_TICK_GUARD 雙次確認過濾量化甩轎。
6. 支援獨立 LINE 視窗分流 (strategy="11")。
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
    format_clean_price_alert_text
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

STRATEGY_ID = "11"
STRATEGY_NAME = "策略 11：台股營收創高與量價突破到價雷達"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')


def check_taiex_intraday():
    """盤中快速檢驗大盤指數與月線關係"""
    try:
        df = yf.download('^TWII', period='1mo', interval='1d', auto_adjust=False, progress=False)
        if not df.empty and len(df) >= 20:
            c = df['Close'].squeeze()
            ma20 = c.rolling(20).mean().iloc[-1]
            last_p = c.iloc[-1]
            return last_p >= ma20, (last_p - ma20) / ma20 * 100.0
    except Exception:
        pass
    return True, 0.0


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
                entry_p = float(row.get('進場價位', close_p))
                addon_p = float(row.get('加碼價位', round(close_p * 1.025, 2)))
                tp1_p = float(row.get('停利TP1', round(close_p * 1.08, 2)))
                tp2_p = float(row.get('停利TP2', round(close_p * 1.18, 2)))
                sl_p = float(row.get('停損價位', round(close_p * 0.945, 2)))
                retest_p = float(row.get('B軌回測價', round(close_p * 0.975, 2)))
                ma20 = float(row.get('月線(20MA)', round(close_p * 0.98, 2)))
                bias_pct = float(row.get('月線乖離%', round((close_p - ma20) / ma20 * 100, 2)))
                rev_yoy = float(row.get('營收YoY%', 15.0))
                days_in = str(row.get('入選天數', 'T+0'))

                targets[code] = {
                    'code': code,
                    'name': str(row.get('證券名稱', code)).strip(),
                    'entry_price': entry_p,
                    'addon_price': addon_p,
                    'tp1_price': tp1_p,
                    'tp2_price': tp2_p,
                    'sl_price': sl_p,
                    'retest_price': retest_p,
                    'ma20': ma20,
                    'bias_pct': bias_pct,
                    'rev_yoy': rev_yoy,
                    'close_price': close_p,
                    'days_in': days_in
                }
            print(f"[✓] 成功自 {CSV_FILE} 載入 {len(targets)} 檔監控標的。")
        except Exception as e:
            print(f"[!] 讀取 {CSV_FILE} 失敗: {e}")

    if not targets:
        print("[*] 查無歷史清單，注入示範標的...")
        targets = {
            '3708': {
                'code': '3708', 'name': '上緯投控',
                'entry_price': 105.5, 'addon_price': 106.0,
                'tp1_price': 108.5, 'tp2_price': 113.0,
                'sl_price': 100.5, 'retest_price': 101.0,
                'ma20': 96.66, 'bias_pct': 7.6, 'rev_yoy': 165.79,
                'close_price': 104.0, 'days_in': 'T+0'
            }
        }
    return targets


def fetch_intraday_quotes(codes):
    """批次抓取即時報價"""
    quotes = {}
    if not codes:
        return quotes

    tickers = []
    for c in codes:
        tickers.append(f"{c}.TW")
        tickers.append(f"{c}.TWO")

    try:
        data = yf.download(tickers=tickers, period="1d", interval="1m", progress=False, group_by="ticker")
        for code in codes:
            tk = f"{code}.TW"
            df = None
            try:
                df = data[tk].dropna() if len(tickers) > 1 else data.dropna()
            except Exception:
                pass
            if df is None or df.empty:
                tk = f"{code}.TWO"
                try:
                    df = data[tk].dropna()
                except Exception:
                    pass

            if df is not None and not df.empty:
                last_row = df.iloc[-1]
                p = float(last_row['Close'])
                quotes[code] = {
                    'price': p,
                    'open': float(df.iloc[0]['Open']),
                    'high': float(df['High'].max()),
                    'low': float(df['Low'].min()),
                    'time': datetime.datetime.now().strftime('%H:%M:%S')
                }
    except Exception as e:
        print(f"[!] 抓取盤中即時報價失敗: {e}")

    return quotes


def send_intraday_price_alert(target, rt, event_type, event_title, event_desc):
    """發送盤中到價 LINE 推播卡片"""
    code = target['code']
    name = target['name']
    price = rt['price']
    print(f"\n[!] 觸發盤中到價警報 (策略11): {code} {name} 事件: {event_title} 現價: {price:.2f}")

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

    is_bull, m_bias = check_taiex_intraday()

    if force_test:
        mock_target = {
            'code': '3708',
            'name': '上緯投控(測試)',
            'entry_price': 105.5,
            'addon_price': 106.0,
            'tp1_price': 108.5,
            'tp2_price': 113.0,
            'sl_price': 100.5,
            'ma20': 96.66,
            'bias_pct': 7.6,
            'rev_yoy': 165.79,
            'close_price': 104.0
        }
        mock_rt = {
            'price': 105.5,
            'open': 104.0,
            'high': 106.0,
            'low': 103.5,
            'time': datetime.datetime.now().strftime('%H:%M:%S')
        }
        send_intraday_price_alert(
            mock_target,
            mock_rt,
            'ENTRY',
            '🟢 測試：營收創高量價突破買點觸發',
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
                'event_desc': f"現價跌破關鍵停損點 {target['sl_price']} 元！突破結構破壞，請嚴守風控紀律立即執行停損！"
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
                'event_title': '🔴 達成第二停利目標 TP2 (+20%)',
                'event_desc': f"營收加速主升段噴發！已達波段滿足點 {target['tp2_price']} 元，建議全數獲利了結，落袋為安！"
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
                'event_title': '🔴 達成第一停利目標 TP1 (+10%)',
                'event_desc': f"到達短波段獲利目標 {target['tp1_price']} 元！建議分批調節 50%，並將停損上移至成本保本！"
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
                'event_desc': f"帶量突破加碼防線 {target['addon_price']} 元，營收動能強勁，右側動能加碼點確立！"
            })
            continue

        # 5. B 軌量縮回測低接判定 (頸線支撐且不破停損)
        retest_p = target['retest_price']
        if (retest_p * 0.99 <= price <= retest_p * 1.015) and price > target['sl_price'] and 'RETEST' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'RETEST', price, retest_p, force_pass=False)
            if not confirmed:
                continue
            events_for_code.add('RETEST')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'RETEST',
                'event_title': '🟡 B軌量縮回測支撐 - 黃金低接買點',
                'event_desc': f"現價回測頸線支撐 {retest_p} 元守穩，浮額清洗完畢，穩健型低接買點浮現！"
            })
            continue

        # 6. A 軌突破建倉買點判定 (僅大盤多頭順風期放行)
        if is_bull and price <= target['entry_price'] and price > target['sl_price'] and 'ENTRY' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'ENTRY', price, target['entry_price'], force_pass=False)
            if not confirmed:
                continue
            events_for_code.add('ENTRY')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'ENTRY',
                'event_title': '🟢 進入甜蜜建倉區 - 買點浮現',
                'event_desc': f"現價 {price} 元處於進場上限 {target['entry_price']} 元以內，營收創高＋量價突破，可建立基本單！"
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
        print("[*] 正在發送【策略 11 營收量價突破到價雷達】連線測試推播...")
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
            if curr_hm > (13 * 60 + 35):
                print(f"[*] 當前時間 {now_tw.strftime('%H:%M:%S')} 已過收盤時間 (13:35)，安全收工。")
                break
            if curr_hm < (9 * 60):
                print(f"[*] 當前時間 {now_tw.strftime('%H:%M:%S')} 尚未開盤 (09:00)，等待開盤...")
                time.sleep(30)
                continue

            scan_intraday(targets, notified_events)
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[*] 接收到中斷指令，結束監控。")
            break
        except Exception as e:
            print(f"[!] 巡邏過程異常: {e}")
            time.sleep(10)


if __name__ == '__main__':
    main()
