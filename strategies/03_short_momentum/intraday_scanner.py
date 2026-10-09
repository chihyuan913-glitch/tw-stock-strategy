#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 03：法人出貨破線做空與個股期貨避險 - 盤中即時雷達與到價推播系統 (Intraday Short Scanner)
適用時段：09:00 ~ 13:35 (台股盤中交易時段)
核心功能：
1. 自動載入最新做空選股名單 (result.csv)，鎖定四大操盤防線與個股期貨合約。
2. 盤中即時連線證交所/櫃買行情，精準偵測空方到價事件：
   - 🟢【起跌建倉觸發】：現價落入空單標準進場區間。
   - 🔵【破低加空觸發】：現價摜破加碼價位 (破今日低點續崩加空)。
   - 🔴【分批停利觸發】：現價下殺攻抵 TP1 (-12%) 或 TP2 (-20%) 空單回補點。
   - 🛑【破線停損警戒】：現價突破月線反壓與停損價位，強制停損回補。
3. 整合個股期貨保證金試算 (一口表彰2張現貨，保證金約現值 13.5%)。
4. 防重複洗版：同一標的同事件類型，當日僅推播一次。
5. 支援獨立 LINE 視窗分流 (strategy="03")。
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

# 加入專案根目錄至 sys.path 以便引用共用模組 (line_sender)
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

CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')

def load_targets():
    """優先自 result.csv 載入最新做空選股名單與四大價位"""
    targets = {}
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
            for _, row in df.iterrows():
                code = str(row['證券代號']).strip()
                close_p = float(row['收盤價'])
                entry_p = float(row.get('進場價位', row.get('空單進場', close_p)))
                addon_p = float(row.get('加碼價位', row.get('加空價位', round(close_p * 0.985, 2))))
                tp1_p = float(row.get('停利TP1', round(close_p * 0.88, 2)))
                tp2_p = float(row.get('停利TP2', round(close_p * 0.80, 2)))
                sl_p = float(row.get('停損價位', row.get('停損回補', round(close_p * 1.025, 2))))
                ma20 = float(row.get('月線(20MA)', round(close_p * 1.02, 2)))
                neg_bias = float(row.get('月線負乖離%', round((close_p - ma20) / ma20 * 100, 2)))
                contract = str(row.get('期貨契約', '--')).strip()
                margin = int(row.get('1口保證金(約)', int(round(close_p * 2000 * 0.135, -1))))

                targets[code] = {
                    'code': code,
                    'name': str(row['證券名稱']).strip(),
                    'contract': contract,
                    'margin_1lot': margin,
                    'entry_price': entry_p,
                    'addon_price': addon_p,
                    'tp1_price': tp1_p,
                    'tp2_price': tp2_p,
                    'sl_price': sl_p,
                    'ma20': ma20,
                    'neg_bias': neg_bias,
                    'v5_avg': int(row.get('5日均量(張)', 1000)),
                    'inst_sell_ratio': float(row.get('法人賣超佔比%', 10.0)),
                    'f_3d': int(row.get('外資3日賣超(張)', 0)),
                    't_3d': int(row.get('投信3日賣超(張)', 0))
                }
            if targets:
                print(f"[✓] 已自 result.csv 載入 {len(targets)} 檔做空避險精選標的。")
                return targets
        except Exception as e:
            print(f"[!] 讀取 result.csv 失敗: {e}，將建立備用母池...")

    # 若無 result.csv 則建立代表性做空標的池
    print("[*] 查無 result.csv，從市場弱勢焦點股建立做空監控母池...")
    demo_codes = ['2603', '2609', '2615']
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
                'name': f'弱勢焦點_{c}',
                'contract': 'CZF',
                'margin_1lot': int(round(c_p * 2000 * 0.135, -1)),
                'entry_price': round(c_p, 2),
                'addon_price': round(c_p * 0.985, 2),
                'tp1_price': round(c_p * 0.88, 2),
                'tp2_price': round(c_p * 0.80, 2),
                'sl_price': round(ma20 * 1.01, 2),
                'ma20': round(ma20, 2),
                'neg_bias': round((c_p - ma20) / ma20 * 100, 2),
                'v5_avg': 5000,
                'inst_sell_ratio': 15.0,
                'f_3d': -3500,
                't_3d': -1200
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
    """發送盤中到價 LINE 推播卡片 (採用旗艦卡片 UI 與操盤四大防線)"""
    code = target['code']
    name = target['name']
    contract = target['contract']
    price = rt['price']
    high_p = rt['high']
    low_p = rt['low']
    ma20 = target['ma20']
    neg_bias = round((price - ma20) / ma20 * 100, 2)

    # 動態指引標籤
    entry_tag = " ◄◄ 【空單進場基準！】" if event_type == "ENTRY" else ""
    addon_tag = " ◄◄ 【破低加空追擊！】" if event_type == "ADDON" else ""
    tp1_tag = " ◄◄ 【TP1達標(-12%)！】" if event_type == "TP1" else ""
    tp2_tag = " ◄◄ 【TP2達標(-20%)！】" if event_type == "TP2" else ""
    sl_tag = " ◄◄ 【站上月線停損！】" if event_type == "STOP_LOSS" else ""

    msg_lines = [
        "╔═══════════════════════╗",
        "║  🚨【盤中雷達】弱勢做空到價即時快訊  ║",
        "╚═══════════════════════╝",
        f"📍 監控標的：{code} {name} ｜ ⚡ 股期: {contract}",
        f"🔥 觸發事件：【{event_title}】",
        f"💵 即時現價：{price:.2f} 元 (今日高: {high_p:.2f} / 低: {low_p:.2f})",
        "━━━━━━━━━━━━━━━━━━━━",
        "🎯 實戰操盤四大防線：",
        f"├ 🟢 進場價位：{target['entry_price']:.2f} 元 (空單進場基準){entry_tag}",
        f"├ 🔵 加碼價位：{target['addon_price']:.2f} 元 (破低加空追擊){addon_tag}",
        f"├ 🔴 停利目標：TP1 {target['tp1_price']:.2f} (-12%){tp1_tag} ｜ TP2 {target['tp2_price']:.2f} (-20%){tp2_tag}",
        f"└ 🛑 停損防守：{target['sl_price']:.2f} 元 (站上月線反壓){sl_tag}",
        "────────────────────",
        "💰 股期保證金與籌碼：",
        f"• 1口保證金：約 {target['margin_1lot']:,} 元 (表彰2張現貨)",
        f"• 均線反壓：月線 {ma20:.2f} 元 (下彎蓋頭反壓) ｜ 負乖離 {neg_bias:.2f}%",
        f"• 法人賣超：佔比 {target['inst_sell_ratio']:.1f}% (外資3日{target['f_3d']:,}張, 投信3日{target['t_3d']:,}張)",
        "────────────────────",
        f"💡【實戰指引】{event_desc}",
        f"⏰ 偵測時間：{rt.get('time', datetime.datetime.now().strftime('%H:%M:%S'))}",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    message = "\n".join(msg_lines)
    print(f"\n[!] 觸發盤中做空到價警報 (策略03): {code} {name} 事件: {event_title} 現價: {price:.2f}")
    send_to_line(message, strategy="03")

def scan_once(targets, notified_events, force_test=False):
    """執行一次全體標的盤中到價掃描"""
    now_str = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"\n[*] [{now_str}] 正在檢查 {len(targets)} 檔弱勢做空標的盤中到價情況...")

    codes = list(targets.keys())
    rt_quotes = fetch_realtime_quotes(codes)

    for code, target in targets.items():
        rt = rt_quotes.get(code)
        if not rt or rt.get('price') is None:
            continue
        price = rt['price']
        print(f"  • {code} {target['name']}: 現價 {price:.2f} (進場 {target['entry_price']:.2f}, 加空 {target['addon_price']:.2f}, TP1 {target['tp1_price']:.2f}, 停損 {target['sl_price']:.2f})")

        events_for_code = notified_events.setdefault(code, set())

        # 1. 停損回補判定 (優先級最高: 現價 >= 停損價 / 站上月線)
        if price >= target['sl_price'] and 'STOP_LOSS' not in events_for_code:
            events_for_code.add('STOP_LOSS')
            send_intraday_price_alert(
                target, rt, 'STOP_LOSS', '🛑 突破月線反壓 - 空單停損回補警報',
                '股價已強勢站上月線反壓防線！下跌慣性破壞，請嚴守紀律立即回補空單停損，絕不死抗！'
            )
            continue

        # 2. 停利 TP2 判定 (現價 <= TP2)
        if price <= target['tp2_price'] and 'TP2' not in events_for_code:
            events_for_code.add('TP2')
            send_intraday_price_alert(
                target, rt, 'TP2', '🔴 空單達成第二停利目標 TP2 (-20%)',
                '股價重挫達標波段回補目標 TP2 (-20%)！利潤豐厚，建議大幅獲利了結回補空單部位！'
            )
            continue

        # 3. 停利 TP1 判定 (現價 <= TP1)
        if price <= target['tp1_price'] and 'TP1' not in events_for_code:
            events_for_code.add('TP1')
            send_intraday_price_alert(
                target, rt, 'TP1', '🔴 空單達成第一停利目標 TP1 (-12%)',
                '股價下殺達標第一停利目標 TP1 (-12%)！短線跌勢滿足，建議分批回補 1/3 至 1/2 空單！'
            )
            continue

        # 4. 破低加空判定 (現價 <= 加碼加空價位)
        if price <= target['addon_price'] and 'ADDON' not in events_for_code:
            events_for_code.add('ADDON')
            send_intraday_price_alert(
                target, rt, 'ADDON', '🔵 摜破關鍵低點 - 空單加空追擊',
                '現價摜破關鍵低點，破線殺盤動能再起！可依操盤計畫加碼放空 1 口股票期貨乘勝追擊！'
            )
            continue

        # 5. 空單進場判定 (現價在進場區間)
        if (target['addon_price'] < price <= target['entry_price'] * 1.005) and 'ENTRY' not in events_for_code:
            events_for_code.add('ENTRY')
            send_intraday_price_alert(
                target, rt, 'ENTRY', '🟢 空單起跌建倉點觸發',
                '現價跌破月線且法人大賣砸盤，空單進場基準點浮現！可依計畫建立個股期貨空單避險部位！'
            )
            continue

        # 測試推播
        if force_test and 'TEST' not in events_for_code:
            events_for_code.add('TEST')
            send_intraday_price_alert(
                target, rt, 'ENTRY', '🟢 測試推播：弱勢做空到價快訊',
                '【盤中雷達連線測試】系統即時監控中，到達四大價位將即時推播！'
            )
            break

def run_intraday_scanner(interval=60, once=False, force_test=False):
    """盤中雷達常駐主迴圈"""
    print("=" * 70)
    print("🚀 啟動【策略03：法人出貨破線做空與個股期貨避險 - 盤中即時雷達與到價推播系統】")
    print("=" * 70)

    targets = load_targets()
    if not targets:
        print("[X] 無可監控之標的，請先確認 result.csv 或聯網狀態。")
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
    parser = argparse.ArgumentParser(description="策略 03 弱勢做空盤中到價即時雷達推播")
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔秒數 (預設 60 秒)")
    parser.add_argument("--once", action="store_true", help="僅執行單次快篩測試")
    parser.add_argument("--test-push", "--force-test", dest="force_test", action="store_true", help="發送一則模擬到價快訊測試 LINE 連線與排版")
    args = parser.parse_args()

    run_intraday_scanner(interval=args.interval, once=args.once, force_test=args.force_test)

if __name__ == '__main__':
    main()
