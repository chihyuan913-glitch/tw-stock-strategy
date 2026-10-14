#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 05：個股期貨法人強勢波段多方 - 盤中即時雷達與到價推播系統 (Intraday Futures Long Scanner)
適用時段：09:00 ~ 13:35 (台股盤中交易時段)
核心功能：
1. 自動載入最新多方選股名單 (result.csv)，鎖定四大操盤防線與股票期貨近月主力合約。
2. 盤中即時連線證交所/櫃買行情，精準偵測四大防線到價事件：
   - 🟢【黃金起漲建倉】：現價回測支撐或落入多單標準建倉區間。
   - 🔵【突破加碼觸發】：現價突破加碼價位 (過今日高點 1 檔確認主升段續強)。
   - 🔴【分批停利觸發】：現價攻抵 TP1 (+10%) 或 TP2 (+16%) 波段滿足點。
   - 🛑【破線停損警戒】：現價跌破月線防線與停損價位，強制停損出場。
3. 整合個股期貨保證金試算 (一口表彰2張現貨，保證金約現值 13.5%)。
4. 防重複洗版：同一標的同事件類型，當日僅推播一次。
5. 盤中防毛刺確認機制 (GLOBAL_TICK_GUARD)，過濾量化高頻單 1-tick 假突破。
6. 支援獨立 LINE 視窗分流 (strategy="05")。
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

def load_targets():
    """優先自 result.csv 載入最新波段多方選股名單與四大價位"""
    targets = {}
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
            for _, row in df.iterrows():
                code = str(row['證券代號']).strip()
                close_p = float(row['收盤價'])
                entry_p = float(row.get('進場價位', row.get('多單進場', close_p)))
                addon_p = float(row.get('加碼價位', row.get('突破加碼', round(close_p * 1.03, 2))))
                tp1_p = float(row.get('停利TP1', round(close_p * 1.10, 2)))
                tp2_p = float(row.get('停利TP2', round(close_p * 1.16, 2)))
                sl_p = float(row.get('停損價位', row.get('停損防守', round(close_p * 0.965, 2))))
                ma20 = float(row.get('月線(20MA)', round(close_p * 0.97, 2)))
                pos_bias = float(row.get('月線正乖離%', round((close_p - ma20) / ma20 * 100, 2)))
                contract = str(row.get('期貨契約', '--')).strip()
                month = str(row.get('期貨月份', '')).strip()
                fut_close = float(row.get('期貨收盤價', close_p))
                basis_desc = str(row.get('價差(Basis)', '')).strip()
                margin = int(row.get('1口保證金(約)', int(round(fut_close * 2000 * 0.135, -1))))
                stars = str(row.get('星級推薦', '★★★★★')).strip()

                fut_oi = int(row.get('期貨未平倉(OI)', row.get('futures_oi', 0)))
                settlement_tag = str(row.get('結算倒數', '')).strip()
                next_month = str(row.get('次月合約', '')).strip()
                next_entry = float(row.get('次月進場參考', row.get('next_entry', entry_p)))
                next_oi = int(row.get('次月未平倉(OI)', 0))
                settlement_reminder = str(row.get('結算操盤建議', '')).strip()

                targets[code] = {
                    'code': code,
                    'name': str(row['證券名稱']).strip(),
                    'contract': contract,
                    'contract_sym': contract,
                    'month': month,
                    'futures_close': fut_close,
                    'basis_desc': basis_desc,
                    'margin_1lot': margin,
                    'stars': stars,
                    'futures_oi': fut_oi,
                    'settlement_tag': settlement_tag,
                    'next_month': next_month,
                    'next_entry': next_entry,
                    'next_oi': next_oi,
                    'settlement_reminder': settlement_reminder,
                    'entry_price': entry_p,
                    'addon_price': addon_p,
                    'tp1_price': tp1_p,
                    'tp2_price': tp2_p,
                    'sl_price': sl_p,
                    'ma20': ma20,
                    'pos_bias': pos_bias,
                    'v5_avg': int(row.get('5日均量(張)', 1000)),
                    'inst_buy_ratio': float(row.get('法人買超佔比%', 12.0)),
                    'f_3d': int(row.get('外資3日買超(張)', 0)),
                    't_3d': int(row.get('投信3日買超(張)', 0))
                }
            if targets:
                print(f"[✓] 已自 result.csv 載入 {len(targets)} 檔個股期貨波段多方精選標的。")
                if targets:
                    sample_target = next(iter(targets.values()))
                    st_tag = sample_target.get('settlement_tag')
                    if st_tag:
                        print(f"[*] 【期貨結算週期戰情】{st_tag}")
                return targets
        except Exception as e:
            print(f"[!] 讀取 result.csv 失敗: {e}，將建立備用母池...")

    # 若無 result.csv 則建立代表性個股期貨多方標的池
    print("[*] 查無 result.csv，從市場個股期貨核心權值股建立多方監控母池...")
    demo_codes = ['2330', '2454', '2317']
    tickers = [f"{c}.TW" for c in demo_codes]
    try:
        df_yf = yf.download(tickers, period='2mo', progress=False, group_by='ticker')
        demo_contracts = {'2330': ('QFF', '台積電'), '2454': ('DHF', '聯發科'), '2317': ('DH', '鴻海')}
        for c in demo_codes:
            t_key = f"{c}.TW"
            sub_df = df_yf[t_key] if t_key in df_yf.columns.levels[0] else None
            if sub_df is None or 'Close' not in sub_df:
                continue
            close_s = sub_df['Close'].dropna()
            if len(close_s) < 20:
                continue
            c_val = round(float(close_s.iloc[-1]), 2)
            ma20_val = round(float(close_s.rolling(20).mean().iloc[-1]), 2)
            contract_sym, c_name = demo_contracts.get(c, ('--', f'代號{c}'))
            margin_est = int(round(c_val * 2000 * 0.135, -1))

            targets[c] = {
                'code': c,
                'name': c_name,
                'contract': contract_sym,
                'contract_sym': contract_sym,
                'month': '近月',
                'futures_close': c_val,
                'basis_desc': '平價差 +0.00 (擬合)',
                'margin_1lot': margin_est,
                'stars': '★★★★★',
                'entry_price': c_val,
                'addon_price': round(c_val * 1.03, 2),
                'tp1_price': round(ma20_val * 1.10, 2),
                'tp2_price': round(ma20_val * 1.16, 2),
                'sl_price': round(min(ma20_val * 0.985, c_val * 0.965), 2),
                'ma20': ma20_val,
                'pos_bias': round((c_val - ma20_val) / ma20_val * 100, 2),
                'v5_avg': 5000,
                'inst_buy_ratio': 15.0,
                'f_3d': 2500,
                't_3d': 1200
            }
        print(f"[✓] 已建立備用監控母池共 {len(targets)} 檔代表性個股期標的。")
    except Exception as e:
        print(f"[!] 建立備用母池異常: {e}")

    return targets


def fetch_realtime_quotes(stock_codes):
    """連線證交所/櫃買 MIS 系統取得盤中即時行情"""
    if not stock_codes:
        return {}
    results = {}
    chunk_size = 50
    codes = list(stock_codes)

    for i in range(0, len(codes), chunk_size):
        chunk = codes[i:i + chunk_size]
        channel_str = "|".join([f"tse_{c}.tw|otc_{c}.tw" for c in chunk])
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={channel_str}&json=1&delay=0"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
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

                    price = float(z) if z != '-' and z != '' else (float(y) if y != '-' and y != '' else None)
                    low_p = float(l) if l != '-' and l != '' else price
                    high_p = float(h) if h != '-' and h != '' else price
                    open_p = float(o) if o != '-' and o != '' else price
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
    """發送盤中到價 LINE 推播卡片 (策略 05 專屬視窗分流)"""
    code = target['code']
    name = target['name']
    price = rt['price']
    print(f"\n[!] 觸發盤中波段多方到價警報 (策略05): {code} {name} 事件: {event_title} 現價: {price:.2f}")

    # 若處於結算日前五日且為進場/加碼事件，自動附加次月新倉開倉提醒
    settle_tag = target.get('settlement_tag', '')
    next_m = target.get('next_month', '')
    next_e = target.get('next_entry', 0.0)
    if next_m and ('⚠️' in str(settle_tag) or '開次月' in str(settle_tag)) and event_type in ['ENTRY', 'ADDON']:
        event_desc += f"\n💡【結算提醒】近月合約到期在即，新建部位強烈建議直接開立次月新倉 ({next_m}月，進場參考價約 {next_e:.2f})！"

    card = build_price_alert_card(target, rt, event_type, event_title, event_desc, strategy_id="05")
    success = send_to_line(card, strategy="05")
    if not success:
        clean_text = format_clean_price_alert_text(target, rt, event_type, event_title, event_desc, strategy_id="05")
        send_to_line(clean_text, strategy="05")


def send_aggregated_price_alerts(alerts):
    """將同週期多檔多方到價事件整合為單一戰訊卡發送"""
    now_str = datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
    print(f"\n[⚡] 發送波段多方多檔合併戰訊卡 (共 {len(alerts)} 檔標的)")

    card = build_aggregated_alert_card(alerts, strategy_id="05", now_str=now_str)
    success = send_to_line(card, strategy="05")
    if not success:
        clean_text = format_clean_aggregated_text(alerts, strategy_id="05", now_str=now_str)
        send_to_line(clean_text, strategy="05")


def scan_once(targets, notified_events, force_test=False):
    """執行一次全體標的盤中到價掃描 (支援多檔合併發送)"""
    now_str = datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
    print(f"\n[*] [{now_str}] 正在檢查 {len(targets)} 檔個股期貨多方標的盤中到價情況...")

    codes = list(targets.keys())
    rt_quotes = fetch_realtime_quotes(codes)
    triggered_alerts = []

    for code, target in targets.items():
        rt = rt_quotes.get(code)
        if not rt or rt.get('price') is None:
            continue
        price = rt['price']
        oi_str = f", OI {target['futures_oi']:,}口" if target.get('futures_oi') else ""
        print(f"  • {code} {target['name']}: 現價 {price:.2f} (進場 {target['entry_price']:.2f}, 加碼 {target['addon_price']:.2f}, TP1 {target['tp1_price']:.2f}, 停損 {target['sl_price']:.2f}{oi_str})")


        events_for_code = notified_events.setdefault(code, set())

        # 1. 停損防守判定 (優先級最高: 現價 <= 停損價 / 跌破月線)
        if price <= target['sl_price'] and 'STOP_LOSS' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'STOP_LOSS', price, target['sl_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('STOP_LOSS')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'STOP_LOSS',
                'event_title': '🛑 跌破關鍵防守線 - 多單停損警戒！',
                'event_desc': '股價已跌破月線防線與嚴格停損價！多頭慣性受損，請嚴守紀律立即市價平倉停損，絕不死抗！'
            })
            continue

        # 2. 第二停利達標觸發 (TP2: 月線正乖離 +16%)
        if price >= target['tp2_price'] and 'TP2' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'TP2', price, target['tp2_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('TP2')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'TP2',
                'event_title': '🔴 主升段波段停利滿足點 (TP2 +16%) 達成！',
                'event_desc': f'股價大漲突破 TP2 滿足點！波段利潤已大幅拉開，建議獲利全數落袋為安，鎖定大波段暴利！'
            })
            continue

        # 3. 第一停利達標觸發 (TP1: 月線正乖離 +10%)
        if price >= target['tp1_price'] and 'TP1' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'TP1', price, target['tp1_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('TP1')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'TP1',
                'event_title': '🔴 短波段停利目標 (TP1 +10%) 達成！',
                'event_desc': f'股價衝過 TP1 滿足區！獲利已逾 10%，建議分批獲利了結 1/3~1/2，餘股續抱上看 TP2！'
            })
            continue

        # 4. 突破加碼觸發 (現價 >= 加碼價位)
        if price >= target['addon_price'] and 'ADDON' not in events_for_code:
            confirmed, cmsg = GLOBAL_TICK_GUARD.check_confirmation(code, 'ADDON', price, target['addon_price'], force_pass=force_test)
            if not confirmed:
                print(f"    [🛡️ 防毛刺攔截] {code} {target['name']}: {cmsg}")
                continue
            events_for_code.add('ADDON')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'ADDON',
                'event_title': '🔵 突破關鍵高點 - 右側動能加碼點！',
                'event_desc': f'現價帶量摜破日高或突破整理防線！多方動能續強，符合右側加碼追擊條件！'
            })
            continue

        # 5. 建倉進場觸發 (回測在建倉區間附近)
        if abs(price - target['entry_price']) / target['entry_price'] <= 0.008 and 'ENTRY' not in events_for_code:
            events_for_code.add('ENTRY')
            triggered_alerts.append({
                'target': target, 'rt': rt, 'event_code': 'ENTRY',
                'event_title': '🟢 進入黃金建倉區間 - 多單買點浮現',
                'event_desc': f'現價回測月線或平台頸線支撐有守，盈虧比極佳，可依紀律分批佈局個股期多單！'
            })

    # 多檔到價合併發送
    if len(triggered_alerts) == 1:
        a = triggered_alerts[0]
        send_intraday_price_alert(a['target'], a['rt'], a['event_code'], a['event_title'], a['event_desc'])
    elif len(triggered_alerts) > 1:
        send_aggregated_price_alerts(triggered_alerts)


def main():
    parser = argparse.ArgumentParser(description="台股策略 05：個股期貨法人強勢波段多方 - 盤中即時雷達")
    parser.add_argument("--interval", type=int, default=60, help="輪詢掃描週期 (秒)，預設 60 秒")
    parser.add_argument("--once", action="store_true", help="僅執行單次快篩即退出")
    parser.add_argument("--test-push", action="store_true", help="發送一則模擬到價快訊至 LINE 以驗證連線與排版")
    args = parser.parse_args()

    print("=" * 70)
    print("🚀 啟動【策略05：個股期貨法人強勢波段多方 - 盤中即時雷達與到價推播系統】")
    print("=" * 70)

    targets = load_targets()
    if not targets:
        print("[!] 無任何監控標的，退出程式。")
        return

    # 若指定 --test-push 則模擬發送一筆警報並退出
    if args.test_push:
        first_code = list(targets.keys())[0]
        t = targets[first_code]
        dummy_rt = {
            'price': t['entry_price'],
            'open': t['entry_price'],
            'high': round(t['entry_price'] * 1.015, 2),
            'low': round(t['entry_price'] * 0.99, 2),
            'volume': 8888,
            'time': datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
        }
        send_intraday_price_alert(
            t, dummy_rt,
            event_type="ENTRY",
            event_title="🟢 測試推播：個股期貨波段多方到價快訊",
            event_desc="本訊息為系統測試推播。四大防線與期貨合約展示正常，請嚴守操作紀律！"
        )
        print("\n[✓] 測試掃描完成！")
        return

    notified_events = {}

    if args.once:
        scan_once(targets, notified_events)
        print("\n[✓] 單次快篩巡邏結束。")
        return

    print(f"[*] 開始盤中即時監控 (每 {args.interval} 秒掃描一次)，按 Ctrl+C 可停止...")
    try:
        while True:
            now_dt = datetime.datetime.now(TZ_TW)
            # 檢查盤中交易時段 (09:00 ~ 13:35)
            market_open = now_dt.replace(hour=9, minute=0, second=0, microsecond=0)
            market_close = now_dt.replace(hour=13, minute=35, second=0, microsecond=0)

            if now_dt < market_open:
                wait_sec = (market_open - now_dt).total_seconds()
                print(f"[*] 尚未開盤 (開盤時間 09:00)，等待開盤中... ({int(wait_sec)} 秒)")
                time.sleep(min(wait_sec, 60))
                continue
            elif now_dt > market_close:
                print(f"[*] 盤中交易時段已結束 ({now_dt.strftime('%H:%M:%S')} > 13:35)，雷達自動安全退出。")
                break

            scan_once(targets, notified_events)
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n[!] 收到中斷信號，停止監控。")


if __name__ == '__main__':
    main()
