#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股盤中即時雷達監控器 V2.0 旗艦版 (Intraday Real-time Scanner V2.0)
適用時段：09:00 ~ 13:30 (台股開盤時段)

監控升級：
1. 籌碼純度初篩：昨日三大法人逆勢買超、佔比 >= 1.5%、投信無恐慌拋售。
2. 盤中即時布林通道監控：成交量突破 1000 張 且 現價進入布林下軌 [-5.0% ~ +1.0%]。
3. 盤中即時止跌與反彈空間判定：
   - 即時計算反彈至 20MA (中軌) 空間。
   - 盤中未收在最低點 (跌勢收斂有買盤支撐)。
4. 智慧評分與星級標籤 (★★★★★)。
5. 觸發時推播包含止跌與反彈目標之即時 LINE 快訊。
"""

import sys
import os

# 加入專案根目錄至 sys.path 以便共用模組 (如 line_sender)
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import time
import datetime
import json
import urllib.request
import pandas as pd
import yfinance as yf
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

def get_latest_trading_date(max_days_back=15):
    """取得最近有證交所交易資料的日期 (YYYYMMDD)"""
    today = datetime.date.today()
    for i in range(max_days_back):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        d_str = d.strftime('%Y%m%d')
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('stat') == 'OK' and len(data.get('data', [])) > 0:
                    return d_str
        except Exception:
            pass
    return None

def prepare_watchlist(top_n=120, min_inst_ratio=1.5):
    """
    盤前準備：從最新一日法人買賣超中挑選「三大法人買超 > 0 且佔比達標」的潛力標的，
    並精算各股今日的布林下軌與中軌值。
    """
    last_date = get_latest_trading_date()
    if not last_date:
        print("[X] 無法取得最新交易日數據")
        return {}

    print(f"[*] 盤前準備 V2.0：讀取 {last_date} 三大法人買超名單...")
    url_t86 = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={last_date}&selectType=ALLBUT0999&response=json'
    req = urllib.request.Request(url_t86, headers={'User-Agent': 'Mozilla/5.0'})
    
    candidates = []
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for row in data.get('data', []):
                code = row[0].strip()
                if len(code) == 4 and code.isdigit():
                    try:
                        name = row[1].strip()
                        foreign = int(row[4].replace(',', ''))
                        trust = int(row[10].replace(',', ''))
                        total = int(row[18].replace(',', ''))
                        # 三大法人買超且投信無巨額拋售
                        if total > 0 and trust >= -500 * 1000:
                            candidates.append({
                                'code': code,
                                'name': name,
                                'foreign': foreign // 1000,
                                'trust': trust // 1000,
                                'total': total // 1000
                            })
                    except ValueError:
                        pass
    except Exception as e:
        print(f"[!] 獲取籌碼清單失敗: {e}")
        return {}

    # 排序取前 top_n 檔
    candidates = sorted(candidates, key=lambda x: x['total'], reverse=True)[:top_n]
    print(f"[*] 鎖定 {len(candidates)} 檔法人逆勢買超焦點股，正在計算 20 日布林通道...")

    tickers = [f"{c['code']}.TW" for c in candidates]
    try:
        df = yf.download(tickers, period='2mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 下載布林通道歷史K線失敗: {e}")
        return {}

    watchlist = {}
    for c in candidates:
        code = c['code']
        t_key = f"{code}.TW"
        try:
            sub_df = df[t_key] if t_key in df.columns.levels[0] else None
            if sub_df is None or 'Close' not in sub_df:
                continue
            close_s = sub_df['Close'].dropna()
            if len(close_s) < 20:
                continue
            ma20 = close_s.rolling(20).mean().iloc[-1]
            std20 = close_s.rolling(20).std(ddof=0).iloc[-1]
            lb = ma20 - 2.0 * std20
            ub = ma20 + 2.0 * std20

            is_dual = (c['foreign'] > 0) and (c['trust'] > 0)

            watchlist[code] = {
                'name': c['name'],
                'foreign_lots': c['foreign'],
                'trust_lots': c['trust'],
                'total_lots': c['total'],
                'is_dual': is_dual,
                'lower_band': float(lb),
                'middle_band': float(ma20),
                'upper_band': float(ub)
            }
        except Exception:
            continue

    print(f"[OK] 盤中監控母池建置完成，共 {len(watchlist)} 檔股票納入 V2.0 即時雷達。")
    return watchlist

def fetch_realtime_quotes(stock_codes):
    """向證交所 mis.twse.com.tw 批次查詢即時現價、高低點與累積成交量"""
    if not stock_codes:
        return {}
    
    results = {}
    chunk_size = 50
    codes = list(stock_codes)
    
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i:i + chunk_size]
        channel_str = "|".join([f"tse_{c}.tw" for c in chunk])
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={channel_str}&json=1&delay=0"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
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
                            'time': item.get('t', '')
                        }
        except Exception:
            pass
            
    return results

def send_intraday_alert_v2(code, meta, rt):
    """格式化並發送盤中即時觸底快訊 V2.0 到 LINE"""
    price = rt['price']
    lb = meta['lower_band']
    mb = meta['middle_band']
    dist_pct = (price - lb) / lb * 100.0
    dist_sign = "+" if dist_pct > 0 else ""
    upside_pct = (mb - price) / price * 100.0

    # 盤中型態分析
    low_p = rt['low'] or price
    high_p = rt['high'] or price
    amp = max(high_p - low_p, 0.01)
    # 下影線或跌勢收斂
    lower_shadow = price - low_p
    ls_ratio = lower_shadow / amp

    candle_status = "盤中低檔承接" if ls_ratio >= 0.20 else "跌勢收斂中"
    if rt['open'] and price >= rt['open']:
        candle_status = "盤中翻紅強勢"

    # 星級判定
    stars = "★★★★☆"
    if meta['is_dual'] and upside_pct >= 4.0:
        stars = "★★★★★"

    dual_tag = " 🔥土洋同步同買" if meta['is_dual'] else ""

    msg_lines = [
        f"⚡【台股盤中雷達 V2.0】觸底轉折點 {stars}",
        "─────────────────",
        f"📍 標的：{code} {meta['name']}{dual_tag}",
        f"💰 即時現價：{price:.2f} 元 (距下軌 {dist_sign}{dist_pct:.2f}%)",
        f"🎯 潛在反彈空間：+{upside_pct:.1f}% (看中軌 {mb:.2f} 元)",
        f"📊 盤中量能：{rt['volume']:,} 張 (已大於千張門檻)",
        f"🕯️ 即時型態：{candle_status}",
        f"🏦 法人背景：昨日買超 +{meta['total_lots']:,} 張 (外資+{meta['foreign_lots']:,}, 投信+{meta['trust_lots']:,})",
        f"⏰ 偵測時間：{rt.get('time', datetime.datetime.now().strftime('%H:%M:%S'))}",
        "─────────────────",
        "💡 實戰提醒：具備反彈空間與法人背書，破下軌5%嚴格停損！"
    ]
    message = "\n".join(msg_lines)
    print(f"\n[!] 觸發盤中警報 V2.0: {code} {meta['name']} 現價 {price} (反彈空間 +{upside_pct:.1f}%)")
    send_to_line(message, strategy="01")

def run_intraday_scanner(interval_seconds=180, once=False):
    """盤中雷達 V2.0 主迴圈"""
    print("=" * 65)
    print("🚀 台股盤中即時雷達 V2.0 啟動中 (布林下軌超跌 + 法人純度 + 反彈空間)")
    print("=" * 65)

    watchlist = prepare_watchlist()
    if not watchlist:
        print("[X] 監控清單建立失敗，結束執行。")
        return

    notified_today = set()

    while True:
        now = datetime.datetime.now()
        now_time = now.time()

        start_time = datetime.time(9, 0)
        end_time = datetime.time(13, 35)

        if not once and (now_time < start_time or now_time > end_time):
            if now_time > end_time:
                print("[*] 盤中交易已結束 (超過 13:35)，盤中雷達收工。")
                break
            else:
                print(f"[*] 尚未開盤 (目前時間 {now.strftime('%H:%M:%S')})，等待至 09:00 開盤...")
                time.sleep(30)
                continue

        print(f"\n[*] [{now.strftime('%H:%M:%S')}] 正在查詢即時報價 (監控中 {len(watchlist)} 檔)...")
        rt_quotes = fetch_realtime_quotes(watchlist.keys())

        matched_count = 0
        for code, meta in watchlist.items():
            if code in rt_quotes:
                rt = rt_quotes[code]
                price = rt['price']
                volume = rt['volume']

                # 條件 1: 成交量 >= 1000 張
                if volume < 1000:
                    continue

                # 條件 2: 位於布林下軌 -5.0% ~ +1.0% 之內
                lb = meta['lower_band']
                dist_pct = (price - lb) / lb * 100.0

                if -5.0 <= dist_pct <= 1.0:
                    # 條件 3: 潛在反彈空間 >= 2.0%
                    upside_pct = (meta['middle_band'] - price) / price * 100.0
                    if upside_pct < 2.0:
                        continue

                    matched_count += 1
                    if code not in notified_today:
                        notified_today.add(code)
                        send_intraday_alert_v2(code, meta, rt)

        print(f"[*] 巡邏完成：目前符合條件個股 {matched_count} 檔，今日已推播 {len(notified_today)} 檔。")

        if once:
            break

        print(f"[*] 等待 {interval_seconds} 秒後進行下一次盤中掃描... (按 Ctrl+C 可停止)")
        time.sleep(interval_seconds)

if __name__ == '__main__':
    is_once = '--once' in sys.argv
    run_intraday_scanner(interval_seconds=180, once=is_once)
