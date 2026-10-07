#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股盤中即時雷達監控器 (Intraday Real-time Scanner)
適用時段：09:00 ~ 13:30 (台股開盤時段)

監控邏輯：
1. 以「昨日三大法人逆勢買超且未大量拋售」的潛力股作為觀察母池。
2. 預先計算各股的 20 日布林通道下軌與中軌。
3. 盤中每隔 N 分鐘向證交所即時基本市況系統 (mis.twse.com.tw) 查詢即時成交價與累積成交量。
4. 只要盤中成交量突破 1000 張，且現價剛好殺入「布林下軌 -5% ~ +1%」的超跌承接區，立即發送 LINE 快訊！
5. 防洗版機制：同一檔個股當天只推播一次。
"""

import sys
import os
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

def prepare_watchlist(top_n=150):
    """
    盤前準備：從最新一日法人買賣超中挑選「三大法人買超 > 0 且投信未拋售」的股票，
    並計算其今日布林下軌值。
    """
    last_date = get_latest_trading_date()
    if not last_date:
        print("[X] 無法取得最新交易日數據")
        return {}

    print(f"[*] 盤前準備：讀取 {last_date} 三大法人買超名單...")
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

    # 依法人買超張數排序，取前 top_n 檔
    candidates = sorted(candidates, key=lambda x: x['total'], reverse=True)[:top_n]
    print(f"[*] 鎖定 {len(candidates)} 檔法人逆勢買超焦點股，正在計算布林通道...")

    # 批次下載歷史資料計算布林通道
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

            watchlist[code] = {
                'name': c['name'],
                'foreign_lots': c['foreign'],
                'trust_lots': c['trust'],
                'total_lots': c['total'],
                'lower_band': float(lb),
                'middle_band': float(ma20),
                'upper_band': float(ub)
            }
        except Exception:
            continue

    print(f"[OK] 盤中監控母池建置完成，共 {len(watchlist)} 檔股票納入即時雷達。")
    return watchlist

def fetch_realtime_quotes(stock_codes):
    """向證交所 mis.twse.com.tw 批次查詢即時現價與累積成交量"""
    if not stock_codes:
        return {}
    
    # 每次最多查詢 50 檔，避免 URL 過長
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
                    c = item.get('c', '') # 股票代碼
                    z = item.get('z', '-') # 當前成交價
                    y = item.get('y', '-') # 昨日收盤價
                    v = item.get('v', '0') # 累積成交量 (張)
                    
                    price = None
                    if z != '-' and z != '':
                        price = float(z)
                    elif y != '-' and y != '':
                        price = float(y)
                        
                    volume = int(v) if v and v.isdigit() else 0
                    if price is not None and c:
                        results[c] = {
                            'price': price,
                            'volume': volume,
                            'time': item.get('t', '')
                        }
        except Exception as e:
            pass
            
    return results

def send_intraday_alert(code, meta, rt):
    """格式化並發送盤中即時觸底快訊到 LINE"""
    dist_pct = (rt['price'] - meta['lower_band']) / meta['lower_band'] * 100.0
    dist_sign = "+" if dist_pct > 0 else ""
    
    # 操盤實戰四大價位試算
    entry_val = f"{rt['price']:.2f} 元 (下軌支撐區 {meta['lower_band']:.2f}~{rt['price']:.2f} 元分批)"
    addon_val = f"{round(rt['price'] * 1.03, 2)} 元 (反彈突破 5MA 續攻確認)"
    tp_val = f"TP1 {meta['middle_band']:.2f} 元 (中軌MA20) ｜ TP2 {meta['upper_band']:.2f} 元 (上軌)"
    sl_val = f"{round(meta['lower_band'] * 0.95, 2)} 元 (跌破下軌-5%破底無條件停損)"

    msg_lines = [
        "⚡【台股盤中即時雷達】布林下軌觸底轉折點",
        "─────────────────",
        f"📍 標的：{code} {meta['name']}",
        f"💰 即時現價：{rt['price']:.2f} 元 (距下軌 {dist_sign}{dist_pct:.2f}%)",
        f"📊 盤中成交量：{rt['volume']:,} 張 (已大於 1000 張門檻)",
        f"📐 布林軌道：下軌 {meta['lower_band']:.2f}｜中軌 {meta['middle_band']:.2f}｜上軌 {meta['upper_band']:.2f}",
        "─────────────────",
        "🎯【實戰操盤四價位建議】",
        f"  🟢 進場價位：{entry_val}",
        f"  🔵 加碼價位：{addon_val}",
        f"  🔴 停利價位：{tp_val}",
        f"  🛑 停損價位：{sl_val}",
        "─────────────────",
        f"🏦 法人背景：昨日買超 +{meta['total_lots']:,} 張 (外資+{meta['foreign_lots']:,}, 投信+{meta['trust_lots']:,})",
        f"⏰ 偵測時間：{rt.get('time', datetime.datetime.now().strftime('%H:%M:%S'))}",
        "─────────────────",
        "💡 策略提示：股價急跌至下軌支撐區但籌碼有主力撐腰，嚴格遵守破底停損紀律！"
    ]
    message = "\n".join(msg_lines)
    print(f"\n[!] 觸發盤中警報: {code} {meta['name']} 現價 {rt['price']} (距下軌 {dist_sign}{dist_pct:.2f}%)")
    send_to_line(message)

def run_intraday_scanner(interval_seconds=180, once=False):
    """
    盤中雷達主迴圈：
    - 每 interval_seconds (預設 3 分鐘) 掃描一次
    - 09:00 ~ 13:30 運作
    """
    print("=" * 65)
    print("🚀 台股盤中即時雷達啟動中 (監控成交量 > 1000張 & 布林下軌 [-5% ~ +1%])")
    print("=" * 65)

    watchlist = prepare_watchlist()
    if not watchlist:
        print("[X] 監控清單建立失敗，結束執行。")
        return

    notified_today = set()

    while True:
        now = datetime.datetime.now()
        now_time = now.time()

        # 檢查是否在台股盤中交易時段 (09:00 ~ 13:30)
        start_time = datetime.time(9, 0)
        end_time = datetime.time(13, 35)

        if not once and (now_time < start_time or now_time > end_time):
            if now_time > end_time:
                print("[*] 盤中交易已結束 (超過 13:35)，盤中雷達休息收工。")
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
                    matched_count += 1
                    # 避免當日重複轟炸推播
                    if code not in notified_today:
                        notified_today.add(code)
                        send_intraday_alert(code, meta, rt)

        print(f"[*] 巡邏完成：目前符合條件個股 {matched_count} 檔，今日已推播 {len(notified_today)} 檔。")

        if once:
            break

        print(f"[*] 等待 {interval_seconds} 秒後進行下一次盤中掃描... (按 Ctrl+C 可停止)")
        time.sleep(interval_seconds)

if __name__ == '__main__':
    is_once = '--once' in sys.argv
    run_intraday_scanner(interval_seconds=180, once=is_once)
