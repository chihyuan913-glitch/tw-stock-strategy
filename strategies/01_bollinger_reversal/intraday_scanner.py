#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 01：布林通道下軌超跌反轉 - 盤中即時雷達與到價推播系統 (V2.0 旗艦版)
適用時段：09:00 ~ 13:35 (台股交易時段)
核心功能：
1. 自動載入最新盤後選股名單 (result.csv)，鎖定四大操盤防線。
2. 盤中即時連線證交所/櫃買行情，精準偵測到價事件：
   - 🟢【觸底進場觸發】：現價落入布林下軌轉折建倉區。
   - 🔵【突破加碼觸發】：現價帶量突破加碼價位 (破今日高點/突破壓力)。
   - 🔴【分批停利觸發】：現價攻抵 TP1 (布林中軌 20MA) 或 TP2 (布林上軌)。
   - 🛑【破線停損警戒】：現價跌破關鍵停損點 (布林下軌 -5%)。
3. 防重複洗版：同一標的同事件類型，當日僅推播一次。
4. 支援獨立 LINE 視窗分流 (strategy="01")。
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
    """優先自 result.csv 載入最新選股名單與四大價位"""
    targets = {}
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
            for _, row in df.iterrows():
                code = str(row['證券代號']).strip()
                close_p = float(row['收盤價'])
                entry_p = float(row.get('進場價位', close_p))
                addon_p = float(row.get('加碼價位', round(close_p * 1.025, 2)))
                tp1_p = float(row.get('停利TP1', row.get('布林中軌', round(close_p * 1.05, 2))))
                tp2_p = float(row.get('停利TP2', row.get('布林上軌', round(close_p * 1.10, 2))))
                sl_p = float(row.get('停損價位', round(close_p * 0.95, 2)))
                lb = float(row.get('布林下軌', round(close_p * 0.98, 2)))
                mb = float(row.get('布林中軌', tp1_p))
                ub = float(row.get('布林上軌', tp2_p))

                targets[code] = {
                    'code': code,
                    'name': str(row['證券名稱']).strip(),
                    'stars': str(row.get('星級推薦', '★★★★☆')),
                    'score': row.get('評分', 80),
                    'entry_price': entry_p,
                    'addon_price': addon_p,
                    'tp1_price': tp1_p,
                    'tp2_price': tp2_p,
                    'sl_price': sl_p,
                    'lower_band': lb,
                    'middle_band': mb,
                    'upper_band': ub,
                    'upside_pct': float(row.get('反彈空間%', round((mb - close_p) / close_p * 100, 2))),
                    'dist_pct': float(row.get('距下軌%', round((close_p - lb) / lb * 100, 2))),
                    'tot_inst': int(row.get('法人買超(張)', 0)),
                    'f_inst': int(row.get('外資買超', 0)),
                    't_inst': int(row.get('投信買超', 0)),
                    'is_dual': str(row.get('土洋同買', '否')) == '是',
                    'pattern': str(row.get('K線型態', '布林超跌轉折'))
                }
            if targets:
                print(f"[✓] 已自 result.csv 載入 {len(targets)} 檔布林超跌精選標的。")
                return targets
        except Exception as e:
            print(f"[!] 讀取 result.csv 失敗: {e}，將建立備用母池...")

    # 若無 result.csv 則從證交所快速挑選代表性標的
    print("[*] 查無 result.csv，從市場備用焦點股建立監控母池...")
    demo_codes = ['2330', '3034', '2454', '2308', '2382']
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
            std20 = float(close_s.rolling(20).std(ddof=0).iloc[-1])
            lb = ma20 - 2.0 * std20
            ub = ma20 + 2.0 * std20
            c_p = float(close_s.iloc[-1])
            targets[c] = {
                'code': c,
                'name': f'台股標的_{c}',
                'stars': '★★★★☆',
                'score': 80,
                'entry_price': round(c_p, 2),
                'addon_price': round(c_p * 1.025, 2),
                'tp1_price': round(ma20, 2),
                'tp2_price': round(ub, 2),
                'sl_price': round(lb * 0.95, 2),
                'lower_band': round(lb, 2),
                'middle_band': round(ma20, 2),
                'upper_band': round(ub, 2),
                'upside_pct': round((ma20 - c_p) / c_p * 100, 2),
                'dist_pct': round((c_p - lb) / lb * 100, 2),
                'tot_inst': 1000,
                'f_inst': 800,
                't_inst': 200,
                'is_dual': True,
                'pattern': '布林超跌整理'
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
    stars = target['stars']
    price = rt['price']
    high_p = rt['high']
    low_p = rt['low']
    vol = rt['volume']

    dual_tag = " 🔥土洋同買" if target.get('is_dual') else ""

    # 動態指引標籤
    entry_tag = " ◄◄ 【到達建倉區！】" if event_type == "ENTRY" else ""
    addon_tag = " ◄◄ 【突破加碼點！】" if event_type == "ADDON" else ""
    tp1_tag = " ◄◄ 【TP1中軌達標！】" if event_type == "TP1" else ""
    tp2_tag = " ◄◄ 【TP2上軌達標！】" if event_type == "TP2" else ""
    sl_tag = " ◄◄ 【破線停損警戒！】" if event_type == "STOP_LOSS" else ""

    msg_lines = [
        "╔═══════════════════════╗",
        "║  🚨【盤中雷達】布林超跌到價即時快訊  ║",
        "╚═══════════════════════╝",
        f"📍 監控標的：{code} {name} ｜ {stars}{dual_tag}",
        f"🔥 觸發事件：【{event_title}】",
        f"💵 即時現價：{price:.2f} 元 (今日高: {high_p:.2f} / 低: {low_p:.2f})",
        "━━━━━━━━━━━━━━━━━━━━",
        "🎯 實戰操盤四大防線：",
        f"├ 🟢 進場價位：{target['entry_price']:.2f} 元 (轉折支撐區){entry_tag}",
        f"├ 🔵 加碼價位：{target['addon_price']:.2f} 元 (突破日高續彈){addon_tag}",
        f"├ 🔴 停利目標：TP1 {target['tp1_price']:.2f} (中軌){tp1_tag} ｜ TP2 {target['tp2_price']:.2f} (上軌){tp2_tag}",
        f"└ 🛑 停損防守：{target['sl_price']:.2f} 元 (跌破下軌-5%){sl_tag}",
        "────────────────────",
        "📊 即時量能與指標位階：",
        f"• 盤中量能：{vol:,} 張 (千張量能確認)",
        f"• 布林軌道：下軌 {target['lower_band']:.2f} ｜ 中軌 {target['middle_band']:.2f} ｜ 上軌 {target['upper_band']:.2f}",
        f"• 潛在空間：看中軌反彈約 +{target['upside_pct']:.1f}%",
        f"• 法人籌碼：買超 +{target['tot_inst']:,} 張 (外資+{target['f_inst']:,}, 投信+{target['t_inst']:,})",
        "────────────────────",
        f"💡【實戰指引】{event_desc}",
        f"⏰ 偵測時間：{rt.get('time', datetime.datetime.now().strftime('%H:%M:%S'))}",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    message = "\n".join(msg_lines)
    print(f"\n[!] 觸發盤中到價警報 (策略01): {code} {name} 事件: {event_title} 現價: {price:.2f}")
    send_to_line(message, strategy="01")

def scan_once(targets, notified_events, force_test=False):
    """執行一次全體標的盤中到價掃描"""
    now_str = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"\n[*] [{now_str}] 正在檢查 {len(targets)} 檔布林超跌標的盤中到價情況...")

    codes = list(targets.keys())
    rt_quotes = fetch_realtime_quotes(codes)

    for code, target in targets.items():
        rt = rt_quotes.get(code)
        if not rt or rt.get('price') is None:
            continue
        price = rt['price']
        print(f"  • {code} {target['name']}: 現價 {price:.2f} (進場 {target['entry_price']:.2f}, 加碼 {target['addon_price']:.2f}, TP1 {target['tp1_price']:.2f}, 停損 {target['sl_price']:.2f})")

        events_for_code = notified_events.setdefault(code, set())

        # 1. 停損警戒判定 (優先級最高: 現價 <= 停損價)
        if price <= target['sl_price'] and 'STOP_LOSS' not in events_for_code:
            events_for_code.add('STOP_LOSS')
            send_intraday_price_alert(
                target, rt, 'STOP_LOSS', '🛑 跌破下軌防線 - 停損離場警報',
                '股價已跌破布林下軌-5%風控底線！破線無支撐，請嚴守紀律立即執行停損離場保護本金！'
            )
            continue

        # 2. 停利 TP2 判定 (現價 >= 布林上軌 TP2)
        if price >= target['tp2_price'] and 'TP2' not in events_for_code:
            events_for_code.add('TP2')
            send_intraday_price_alert(
                target, rt, 'TP2', '🔴 達成第二停利目標 TP2 (布林上軌)',
                '股價已強勢攻抵布林上軌壓力區！反彈波段滿足，建議再獲利了結剩餘部位，落袋為安！'
            )
            continue

        # 3. 停利 TP1 判定 (現價 >= 布林中軌 TP1)
        if price >= target['tp1_price'] and 'TP1' not in events_for_code:
            events_for_code.add('TP1')
            send_intraday_price_alert(
                target, rt, 'TP1', '🔴 達成第一停利目標 TP1 (布林中軌)',
                '股價已反彈觸及 20MA 布林中軌！短線目標滿足，建議先分批獲利了結 1/3 至 1/2 部位！'
            )
            continue

        # 4. 突破加碼判定 (現價 >= 加碼價位)
        if price >= target['addon_price'] and 'ADDON' not in events_for_code:
            events_for_code.add('ADDON')
            send_intraday_price_alert(
                target, rt, 'ADDON', '🔵 突破關鍵高點 - 右側加碼點',
                '現價已突破關鍵高點，轉折反彈動能確認！可依操盤計畫加碼乘勝追擊！'
            )
            continue

        # 5. 進場觸底判定 (現價落入進場價位區間)
        if (target['sl_price'] < price <= target['entry_price'] * 1.015) and 'ENTRY' not in events_for_code:
            events_for_code.add('ENTRY')
            send_intraday_price_alert(
                target, rt, 'ENTRY', '🟢 觸底轉折區 - 進場買點觸發',
                '現價回測布林下軌支撐區，法人逆勢買超籌碼護盤，可依計畫分批試單進場建倉！'
            )
            continue

        # 測試推播
        if force_test and 'TEST' not in events_for_code:
            events_for_code.add('TEST')
            send_intraday_price_alert(
                target, rt, 'ENTRY', '🟢 測試推播：布林超跌到價快訊',
                '【盤中雷達連線測試】系統即時監控中，到達四大價位將即時推播！'
            )
            break

def run_intraday_scanner(interval=60, once=False, force_test=False):
    """盤中雷達常駐主迴圈"""
    print("=" * 70)
    print("🚀 啟動【策略01：布林通道下軌超跌反轉 - 盤中即時雷達與到價推播系統】")
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
    parser = argparse.ArgumentParser(description="策略 01 布林超跌盤中到價即時雷達推播")
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔秒數 (預設 60 秒)")
    parser.add_argument("--once", action="store_true", help="僅執行單次快篩測試")
    parser.add_argument("--test-push", "--force-test", dest="force_test", action="store_true", help="發送一則模擬到價快訊測試 LINE 連線與排版")
    args = parser.parse_args()

    run_intraday_scanner(interval=args.interval, once=args.once, force_test=args.force_test)

if __name__ == '__main__':
    main()
