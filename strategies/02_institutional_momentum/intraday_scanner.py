#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 02：法人籌碼集中起漲 - 盤中即時雷達與到價推播系統 (Intraday Momentum Scanner V3.0)
適用時段：09:00 ~ 13:35 (台股盤中交易時段)

【核心功能與風控升級亮點】：
1. 自動載入最新盤後選股名單 (result.csv)，提取 Tier-1 / Tier-2 精選標的與四大操盤防線。
2. 盤中即時連線證交所/櫃買行情 (mis.twse API，Yahoo Finance 備援)，即時偵測五大到價事件：
   - 🛑【風控停損警戒】：
     * 盤中觸及 -5.0% 災難硬停損，立即發送停損離場警戒！
     * 13:00~13:30 尾盤有效跌破月線 -2% 關鍵防線，確認收盤破線離場！
   - 🔴【波段分批停利】：
     * 達成 TP1：起漲動能達標，提醒獲利了結 1/2 部位並拉至成本價保本 (波段目標 +15%)。
     * 達成 TP2：主升段等幅滿足，提醒全數獲利了結落袋為安 (波段目標 +20%)。
   - 🔵【突破加碼確認】：現價帶量突破加碼價位 (破今日高點/關鍵壓力)，提醒右側順勢加碼。
   - 🟢【起漲進場建倉】：現價落入月線起漲甜蜜建倉區間，提醒買點浮現。
3. 全面套用防毛刺確認機制 (GLOBAL_TICK_GUARD)，防範高頻演算法 1-tick 假突破誘多與假跌破甩轎。
4. 採用高對比大字體卡片排版 (build_price_alert_card)，四大價位柔和彩底獨立展示，一目了然。
5. 多檔合併戰訊卡：同週期多檔觸發時自動彙整發送，省去洗版困擾。
6. 支援獨立 LINE 視窗分流 (strategy="02") 與測試推播 (--test-push)。
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

# 加入專案根目錄至 sys.path 以便引用共用模組
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

CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')


def safe_float(val, default=0.0):
    try:
        if val is None or pd.isna(val):
            return default
        return float(val)
    except Exception:
        return default


def safe_int(val, default=0):
    try:
        if val is None or pd.isna(val):
            return default
        return int(float(val))
    except Exception:
        return default


def load_targets():
    """優先自 result.csv 載入最新選股名單與四大價位"""
    targets = {}
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
            # 若包含生命週期狀態，排除已確認除帳的標的
            if '生命週期狀態' in df.columns:
                df = df[df['生命週期狀態'] != 'CLOSED'].copy()
                
            for _, row in df.iterrows():
                code = str(row['證券代號']).strip()
                if not code or code == 'nan':
                    continue
                close_p = safe_float(row.get('收盤價'), 0.0)
                if close_p <= 0:
                    continue
                entry_p = safe_float(row.get('進場價位', row.get('進場參考', close_p)), close_p)
                addon_p = safe_float(row.get('加碼價位'), round(close_p * 1.025, 2))
                tp1_p = safe_float(row.get('停利TP1'), round(close_p * 1.055, 2))
                tp2_p = safe_float(row.get('停利TP2'), round(close_p * 1.09, 2))
                sl_p = safe_float(row.get('停損價位'), round(close_p * 0.95, 2))
                ma20 = safe_float(row.get('月線(20MA)'), round(close_p * 0.98, 2))
                bias_pct = safe_float(row.get('月線乖離%'), round((close_p - ma20) / ma20 * 100, 2))

                targets[code] = {
                    'code': code,
                    'name': str(row['證券名稱']).strip(),
                    'tier': str(row.get('梯隊標籤', '🟢 Tier-1 主力鎖碼')),
                    'stars': str(row.get('星級推薦', '★★★★☆')),
                    'score': safe_int(row.get('評分'), 80),
                    'close_price': close_p,
                    'entry_price': entry_p,
                    'addon_price': addon_p,
                    'tp1_price': tp1_p,
                    'tp2_price': tp2_p,
                    'sl_price': sl_p,
                    'ma20': ma20,
                    'bias_pct': bias_pct,
                    'v5_avg': safe_int(row.get('5日均量(張)'), 1000),
                    'inst_ratio': safe_float(row.get('法人買超佔比%'), 10.0),
                    'f_3d': safe_int(row.get('外資3日買超(張)'), 0),
                    't_3d': safe_int(row.get('投信3日買超(張)'), 0),
                    'inst_today': safe_int(row.get('當日法人買超(張)'), 0)
                }
            if targets:
                print(f"[✓] 已自 result.csv 載入 {len(targets)} 檔法人起漲精選標的。")
                return targets
        except Exception as e:
            print(f"[!] 讀取 result.csv 失敗: {e}，將建立備用母池...")

    # 若無 result.csv 則建立代表性標的池
    print("[*] 查無 result.csv，從市場焦點股建立起漲監控母池...")
    demo_codes = ['2606', '9904', '3005', '2347', '2317']
    tickers = [f"{c}.TW" for c in demo_codes]
    try:
        df_yf = yf.download(tickers, period='2mo', progress=False, group_by='ticker')
        for c in demo_codes:
            t_key = f"{c}.TW"
            sub_df = df_yf[t_key] if t_key in df_yf.columns.levels[0] else None
            if sub_df is None or 'Close' not in sub_df:
                continue
            close_s = sub_df['Close'].dropna()
            if len(close_s) < 20:
                continue
            ma20 = float(close_s.rolling(20).mean().iloc[-1])
            c_p = float(close_s.iloc[-1])
            targets[c] = {
                'code': c,
                'name': f'起漲焦點_{c}',
                'tier': '🟢 Tier-1 主力鎖碼',
                'stars': '★★★★☆',
                'score': 82,
                'close_price': c_p,
                'entry_price': round(c_p * 1.005, 2),
                'addon_price': round(c_p * 1.025, 2),
                'tp1_price': round(c_p * 1.055, 2),
                'tp2_price': round(c_p * 1.09, 2),
                'sl_price': round(max(ma20 * 0.98, c_p * 0.95), 2),
                'ma20': round(ma20, 2),
                'bias_pct': round((c_p - ma20) / ma20 * 100, 2),
                'v5_avg': 3000,
                'inst_ratio': 15.0,
                'f_3d': 2500,
                't_3d': 500,
                'inst_today': 1500
            }
    except Exception as e:
        print(f"[!] 建立備用母池失敗: {e}")
    return targets


def fetch_realtime_quotes(stock_codes):
    """向證交所/櫃買 mis.twse 批次查詢即時現價與高低點 (支援 Yahoo Finance 備援)"""
    if not stock_codes:
        return {}
    results = {}
    chunk_size = 50
    codes = list(stock_codes)

    for i in range(0, len(codes), chunk_size):
        chunk = codes[i:i + chunk_size]
        channel_str = "|".join([f"tse_{c}.tw|otc_{c}.tw" for c in chunk])
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={channel_str}&json=1&delay=0"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                for item in data.get('msgArray', []):
                    c = item.get('c', '')
                    z = item.get('z', '-')
                    y = item.get('y', '-')
                    o = item.get('o', '-')
                    h = item.get('h', '-')
                    l = item.get('l', '-')
                    v = item.get('v', '0')

                    price = float(z) if z not in ('-', '') else (float(y) if y not in ('-', '') else None)
                    low_p = float(l) if l not in ('-', '') else price
                    high_p = float(h) if h not in ('-', '') else price
                    open_p = float(o) if o not in ('-', '') else price
                    volume = int(v) if v and v.isdigit() else 0

                    if price is not None and c:
                        results[c] = {
                            'price': price,
                            'open': open_p,
                            'high': high_p,
                            'low': low_p,
                            'volume': volume,
                            'time': item.get('t', datetime.datetime.now().strftime('%H:%M:%S'))
                        }
        except Exception:
            pass

    # 針對 MIS 缺漏的標的，使用 Yahoo Finance 備援
    missing = [c for c in codes if c not in results or results[c].get('price') is None]
    for c in missing:
        for suffix in ['TW', 'TWO']:
            try:
                url_yf = f'https://query1.finance.yahoo.com/v8/finance/chart/{c}.{suffix}?interval=1m&range=1d'
                req_yf = urllib.request.Request(url_yf, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req_yf, timeout=4) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    meta = data['chart']['result'][0]['meta']
                    p = meta.get('regularMarketPrice')
                    if p is not None:
                        results[c] = {
                            'price': round(float(p), 2),
                            'open': round(float(meta.get('regularMarketOpen', p)), 2),
                            'high': round(float(meta.get('regularMarketDayHigh', p)), 2),
                            'low': round(float(meta.get('regularMarketDayLow', p)), 2),
                            'volume': int(meta.get('regularMarketVolume', 0) // 1000),
                            'time': datetime.datetime.now().strftime('%H:%M:%S')
                        }
                        break
            except Exception:
                continue

    return results


def send_intraday_price_alert(target, rt, event_type, event_title, event_desc):
    """發送盤中到價 LINE 推播卡片 (採用大字體、零引線、四大價位一目了然之高對比 Flex 卡)"""
    code = target['code']
    name = target['name']
    price = rt['price']
    print(f"\n[!] 觸發盤中到價警報 (策略02): {code} {name} 事件: {event_title} 現價: {price:.2f}")

    card = build_price_alert_card(target, rt, event_type, event_title, event_desc, strategy_id="02")
    success = send_to_line(card, strategy="02")
    if not success:
        clean_text = format_clean_price_alert_text(target, rt, event_type, event_title, event_desc, strategy_id="02")
        send_to_line(clean_text, strategy="02")


def send_aggregated_price_alerts(alerts):
    """將同週期多檔到價事件整合為單一戰訊卡發送，大幅節省 90% LINE 訊息則數並避免洗版"""
    now_str = datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
    print(f"\n[⚡] 發送法人起漲多檔合併戰訊卡 (共 {len(alerts)} 檔標的，僅消耗 1 則訊息額度！)")

    card = build_aggregated_alert_card(alerts, strategy_id="02", now_str=now_str)
    success = send_to_line(card, strategy="02")
    if not success:
        clean_text = format_clean_aggregated_text(alerts, strategy_id="02", now_str=now_str)
        send_to_line(clean_text, strategy="02")


def scan_once(targets, notified_events, force_test=False):
    """執行一次全體標的盤中到價掃描 (支援防毛刺與多檔合併發送)"""
    now = datetime.datetime.now(TZ_TW)
    now_str = now.strftime('%H:%M:%S')
    now_time = now.time()
    print(f"\n[*] [{now_str}] 正在巡邏 {len(targets)} 檔法人起漲標的盤中到價情況...")

    codes = list(targets.keys())
    rt_quotes = fetch_realtime_quotes(codes)
    triggered_alerts = []

    for code, target in targets.items():
        rt = rt_quotes.get(code)
        if not rt or rt.get('price') is None:
            continue
        price = rt['price']
        print(f"  • {code} {target['name']}: 現價 {price:.2f} (進場 {target['entry_price']:.2f}, 加碼 {target['addon_price']:.2f}, TP1 {target['tp1_price']:.2f}, 停損 {target['sl_price']:.2f})")

        events_for_code = notified_events.setdefault(code, set())

        # 1. 停損警戒判定 (優先級最高)
        # (1) 盤中急跌硬停損 -5.0%
        # (2) 跌破設定停損點 sl_price (或尾盤 13:00~13:30 確認收盤跌破月線 -2%)
        is_hard_stop = (price <= target['sl_price']) or ((price - target['entry_price']) / target['entry_price'] <= -0.05)
        is_close_breakdown = (now_time >= datetime.time(13, 0)) and (price < target['ma20'] * 0.98)
        
        if (is_hard_stop or is_close_breakdown) and 'STOP_LOSS' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'STOP_LOSS', price, target['sl_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('STOP_LOSS')
            desc = '股價已觸發盤中 -5.0% 災難硬停損防守線！' if is_hard_stop else '尾盤確認收破月線-2%關鍵防守線！'
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'STOP_LOSS',
                'event_title': '🛑 跌破關鍵防線 - 嚴格停損警戒',
                'event_desc': f'{desc}法人主力防線失守，請嚴守風控紀律立即執行停損離場保護本金！'
            })
            continue

        # 2. 停利 TP2 判定 (波段主升段等幅滿足: 現價 >= TP2)
        if price >= target['tp2_price'] and 'TP2' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'TP2', price, target['tp2_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('TP2')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'TP2',
                'event_title': '🔴 達成第二波段停利目標 TP2',
                'event_desc': '股價強勢攻抵主升段目標 TP2！波段利潤已獲極大化，建議全數獲利了結，獲利放入口袋！(波段目標 +20%)'
            })
            continue

        # 3. 停利 TP1 判定 (起漲動能達標: 現價 >= TP1)
        if price >= target['tp1_price'] and 'TP1' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'TP1', price, target['tp1_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('TP1')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'TP1',
                'event_title': '🔴 達成第一波段停利目標 TP1',
                'event_desc': '股價攻抵第一波段目標 TP1！起漲動能發酵，建議先分批獲利了結 1/2 部位，並將剩餘部位移動停損點上移至成本價保本！(波段目標 +15%)'
            })
            continue

        # 4. 突破加碼判定 (帶量突破: 現價 >= 加碼價位)
        if price >= target['addon_price'] and 'ADDON' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'ADDON', price, target['addon_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('ADDON')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'ADDON',
                'event_title': '🔵 突破關鍵高點 - 右側加碼點',
                'event_desc': '現價帶量突破關鍵壓力與加碼防線，法人主力持續吃貨發動，可依操盤計畫加碼乘勝追擊！'
            })
            continue

        # 5. 進場起漲判定 (現價落入起漲甜蜜區)
        if (target['sl_price'] < price <= target['entry_price'] * 1.015) and 'ENTRY' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'ENTRY', price, target['entry_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('ENTRY')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'ENTRY',
                'event_title': '🟢 起漲安全區 - 進場買點觸發',
                'event_desc': '現價處於月線正乖離起漲黃金安全區，法人主力籌碼集中背書，可依操盤計畫分批進場建倉！'
            })
            continue

        # 測試推播
        if force_test and 'TEST' not in events_for_code:
            events_for_code.add('TEST')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'TEST',
                'event_title': '🟢 測試推播：法人起漲到價快訊 (V3.0)',
                'event_desc': '【盤中雷達連線測試】系統即時監控中，到達四大防線將即時推播！'
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


def run_intraday_scanner(interval=60, once=False, force_test=False):
    """盤中雷達常駐主迴圈"""
    print("=" * 75)
    print("🚀 啟動【策略02：法人籌碼集中起漲 - 盤中即時雷達與到價推播系統 V3.0】")
    print("=" * 75)

    targets = load_targets()
    if not targets:
        print("[X] 無可監控之標的，請先確認 result.csv 或聯網狀態。")
        return

    notified_events = {}

    while True:
        now = datetime.datetime.now(TZ_TW)
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
    parser = argparse.ArgumentParser(description="策略 02 法人起漲盤中到價即時雷達推播 (V3.0)")
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔秒數 (預設 60 秒)")
    parser.add_argument("--once", action="store_true", help="僅執行單次快篩測試")
    parser.add_argument("--test-push", "--force-test", dest="force_test", action="store_true", help="發送一則模擬到價快訊測試 LINE 連線與排版")
    args = parser.parse_args()

    run_intraday_scanner(interval=args.interval, once=args.once, force_test=args.force_test)


if __name__ == '__main__':
    main()
