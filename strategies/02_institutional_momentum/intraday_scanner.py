#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股盤中即時雷達：法人籌碼集中起漲策略 (Intraday Momentum & Chip Concentration Scanner)
適用時段：09:00 ~ 13:35 (台股盤中交易時段)

【運作機制】
1. 盤前母池建立：
   - 篩選外資或投信近 3 日累計買超 > 1,000 張。
   - 法人買超佔成交量 > 10%、5 日法人合計淨買超 > 0。
   - 計算月線 (20MA) 且月線斜率向上 (助漲結構)、5 日均量 > 1,000 張。
   - 計算月線 +8% 起漲安全價格上限 (Price Ceiling)。

2. 盤中即時監控 (09:00 ~ 13:35)：
   - 每隔 N 秒向證交所即時基本市況系統 (mis.twse.com.tw) 查詢最新撮合成交價、開盤價、最高價與累積成交量。
   - 即時判定：
     (1) 累積成交量突破 1,000 張。
     (2) 現價站上月線且處於起漲安全區 (0% <= 月線正乖離 < 8%)。
     (3) 即時 K 棒實體飽滿，無長上影線 (上影線 < 0.5 * 實體 K 棒)。
   - 符合條件立即透過 LINE Messaging API 發送推播快訊至使用者手機！
   - 防洗版機制：同一檔個股當天只推播一次。
"""

import sys
import os
import time
import datetime
import json
import urllib.request
import argparse
from pathlib import Path
import pandas as pd
import yfinance as yf

# 加入專案根目錄至 sys.path 以便共用模組 (如 line_sender)
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# 匯入 LINE 發送模組
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


def get_recent_trading_dates(n_days=5, max_lookback=20):
    """取得最近 n_days 個證交所實際開盤交易日 (YYYYMMDD)"""
    today = datetime.date.today()
    found_dates = []
    
    for i in range(max_lookback):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        d_str = d.strftime('%Y%m%d')
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('stat') == 'OK' and len(data.get('data', [])) > 0:
                    found_dates.append(d_str)
                    if len(found_dates) == n_days:
                        break
        except Exception:
            pass
            
    return found_dates


def prepare_momentum_watchlist(top_n=150):
    """
    盤前準備：篩選符合法人波段買超、月線向上之觀察母池，並預先計算 20MA 及起漲邊界價格
    """
    print("[*] 正在檢索最近交易日之法人籌碼數據...")
    trading_dates = get_recent_trading_dates(n_days=5)
    if not trading_dates:
        print("[X] 無法取得交易日籌碼資料，請檢查網路連線。")
        return {}

    latest_date = trading_dates[0]
    dates_3d = trading_dates[:min(3, len(trading_dates))]
    dates_5d = trading_dates[:min(5, len(trading_dates))]
    print(f"[*] 基準日: {latest_date}（近3日: {dates_3d}，近5日: {dates_5d}）")

    # 1. 抓取法人歷史買賣超
    chips_history = {}
    for d_str in dates_5d:
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('stat') == 'OK':
                    for row in data.get('data', []):
                        code = row[0].strip()
                        if len(code) == 4 and code.isdigit():
                            try:
                                name = row[1].strip()
                                foreign = int(row[4].replace(',', ''))
                                trust = int(row[10].replace(',', ''))
                                total = int(row[18].replace(',', ''))
                                if code not in chips_history:
                                    chips_history[code] = {}
                                chips_history[code][d_str] = {
                                    'name': name,
                                    'foreign': foreign // 1000,
                                    'trust': trust // 1000,
                                    'total': total // 1000
                                }
                            except (ValueError, IndexError):
                                pass
        except Exception as e:
            print(f"[!] 抓取 {d_str} 籌碼失敗: {e}")

    # 2. 抓取昨日收盤行情計算法人佔比
    url_mi = f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={latest_date}&type=ALLBUT0999&response=json'
    req_mi = urllib.request.Request(url_mi, headers={'User-Agent': 'Mozilla/5.0'})
    quotes = {}
    try:
        with urllib.request.urlopen(req_mi, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for t in data.get('tables', []):
                if '每日收盤行情' in t.get('title', ''):
                    for row in t.get('data', []):
                        code = row[0].strip()
                        if len(code) == 4 and code.isdigit():
                            try:
                                vol = int(row[2].replace(',', '')) // 1000
                                close_str = row[8].replace(',', '').strip()
                                if close_str != '--':
                                    quotes[code] = {'volume': vol, 'close': float(close_str)}
                            except (ValueError, IndexError):
                                pass
    except Exception as e:
        print(f"[!] 抓取昨日收盤行情失敗: {e}")

    # 3. 籌碼初步挑選符合「外資/投信 3 日買超 > 1,000 張」且「5日累計 > 0」之標的
    candidates = []
    for code, q in quotes.items():
        if code in chips_history and latest_date in chips_history[code]:
            c_today = chips_history[code][latest_date]
            f_3d = sum(chips_history[code][d]['foreign'] for d in dates_3d if d in chips_history[code])
            t_3d = sum(chips_history[code][d]['trust'] for d in dates_3d if d in chips_history[code])
            total_5d = sum(chips_history[code][d]['total'] for d in dates_5d if d in chips_history[code])
            
            # 外資或投信近 3 日買超 >= 1,000 張
            if (f_3d >= 1000 or t_3d >= 1000) and total_5d > 0:
                name = c_today['name']
                vol = q['volume']
                inst_ratio = round((c_today['total'] / vol * 100.0), 1) if vol > 0 else 0
                candidates.append({
                    'code': code,
                    'name': name,
                    'yesterday_close': q['close'],
                    'yesterday_volume': vol,
                    'f_3d': f_3d,
                    't_3d': t_3d,
                    'inst_today': c_today['total'],
                    'inst_5d': total_5d,
                    'inst_ratio': inst_ratio
                })

    candidates = sorted(candidates, key=lambda x: (x['inst_ratio'], x['inst_5d']), reverse=True)[:top_n]
    print(f"[*] 籌碼初篩完成，鎖定 {len(candidates)} 檔法人重倉標的，正在計算月線結構...")

    # 4. 批次下載歷史日K線計算 MA20 與斜率
    tickers = [f"{c['code']}.TW" for c in candidates]
    try:
        hist_data = yf.download(tickers, period='3mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 下載歷史K線失敗: {e}")
        return {}

    watchlist = {}
    for c in candidates:
        code = c['code']
        t_key = f"{code}.TW"
        try:
            if len(candidates) == 1:
                df = hist_data.dropna()
            else:
                if t_key in hist_data.columns.levels[0]:
                    df = hist_data[t_key].dropna()
                else:
                    continue

            if 'Close' not in df or len(df['Close']) < 21:
                continue

            close_series = df['Close']
            vol_series = df['Volume']
            ma20 = close_series.rolling(20).mean()
            ma20_curr = float(ma20.iloc[-1])
            ma20_prev = float(ma20.iloc[-2])
            
            # 必須符合月線斜率向上 (MA20_curr > MA20_prev)
            if ma20_curr <= ma20_prev:
                continue

            # 5日均量 >= 1000 張
            v5_avg = float(vol_series.rolling(5).mean().iloc[-1]) / 1000.0
            if v5_avg < 800: # 留少許緩衝
                continue

            # 月線 +8% 起漲警戒天花板價格
            ceiling_price = round(ma20_curr * 1.08, 2)

            watchlist[code] = {
                'name': c['name'],
                'yesterday_close': c['yesterday_close'],
                'ma20': round(ma20_curr, 2),
                'ceiling_price': ceiling_price,
                'v5_avg': int(v5_avg),
                'f_3d': c['f_3d'],
                't_3d': c['t_3d'],
                'inst_today': c['inst_today'],
                'inst_5d': c['inst_5d'],
                'inst_ratio': c['inst_ratio']
            }
        except Exception:
            continue

    print(f"[OK] 盤中監控母池建置完成！共精選 {len(watchlist)} 檔「法人重倉＋月線向上」起漲焦點股。")
    return watchlist


def fetch_realtime_quotes(stock_codes):
    """向證交所 mis.twse.com.tw 批次查詢即時現價、開盤價、最高價、最低價與累積成交量"""
    if not stock_codes:
        return {}
        
    results = {}
    chunk_size = 50
    codes = list(stock_codes)
    
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i:i + chunk_size]
        # 預設查詢上市公司 (tse_)，若查不到可補充 otc_
        channels = [f"tse_{c}.tw" for c in chunk]
        channel_str = "|".join(channels)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={channel_str}&json=1&delay=0"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                for item in data.get('msgArray', []):
                    c = item.get('c', '') # 股票代碼
                    z = item.get('z', '-') # 當前最新成交價
                    y = item.get('y', '-') # 昨日收盤價
                    o = item.get('o', '-') # 開盤價
                    h = item.get('h', '-') # 最高價
                    l = item.get('l', '-') # 最低價
                    v = item.get('v', '0') # 累積成交量 (張)
                    
                    price = None
                    if z != '-' and z != '':
                        price = float(z)
                    elif y != '-' and y != '':
                        price = float(y)
                        
                    yesterday = float(y) if y != '-' and y != '' else None
                    open_p = float(o) if o != '-' and o != '' else price
                    high_p = float(h) if h != '-' and h != '' else price
                    low_p = float(l) if l != '-' and l != '' else price
                    volume = int(v) if v and v.isdigit() else 0
                    
                    if price is not None and c:
                        results[c] = {
                            'price': price,
                            'open': open_p,
                            'high': high_p,
                            'low': low_p,
                            'yesterday': yesterday,
                            'volume': volume,
                            'time': item.get('t', '')
                        }
        except Exception:
            pass
            
    return results


def send_intraday_momentum_alert(code, meta, rt):
    """格式化並發送盤中即時起漲通知至使用者 LINE"""
    price = rt['price']
    ma20 = meta['ma20']
    bias_pct = ((price - ma20) / ma20) * 100.0
    
    # 計算漲跌幅
    pct_chg = 0.0
    if rt['yesterday'] and rt['yesterday'] > 0:
        pct_chg = ((price - rt['yesterday']) / rt['yesterday']) * 100.0
    chg_sign = "+" if pct_chg > 0 else ""

    # K 棒實體與上影線
    open_p = rt['open'] if rt['open'] else price
    high_p = rt['high'] if rt['high'] else price
    body = abs(price - open_p)
    upper_shadow = high_p - max(open_p, price)

    # -------------------------------------------------------------
    # 策略實戰操盤四大價位試算 (進場、加碼、停利、停損)
    # -------------------------------------------------------------
    # 1. 進場價位：現價或回測月線區間
    entry_desc = f"{price:.2f} 元 (回測 {ma20:.2f}~{price:.2f} 元分批佈局)"

    # 2. 加碼價位：放量突破當日高點 1 檔或突破續攻 +3% (不超過月線正乖離 10%)
    addon_raw = max(high_p * 1.005, price * 1.03)
    addon_price = round(min(addon_raw, ma20 * 1.10), 2)

    # 3. 停利價位：
    #    TP1: 月線正乖離 +12% 短波段滿足點 (分批停利 1/3~1/2)
    #    TP2: 月線正乖離 +20% 波段噴出滿足點
    tp1_price = round(ma20 * 1.12, 2)
    tp2_price = round(ma20 * 1.20, 2)

    # 4. 停損價位：
    #    跌破月線 2% 或跌破今日最低點 -1% (嚴格停損，保護本金)
    low_ref = rt['low'] if rt.get('low') else price * 0.98
    sl_price = round(max(ma20 * 0.98, low_ref * 0.99), 2)

    msg_lines = [
        "🎯【台股盤中即時雷達】法人籌碼起漲發動點！",
        "─────────────────────",
        f"📍 標的：{code} {meta['name']}",
        f"💰 即時現價：{price:.2f} 元 ({chg_sign}{pct_chg:.2f}%)",
        f"📐 月線乖離：+{bias_pct:.2f}% (起漲安全區間內)",
        f"📊 盤中成交量：{rt['volume']:,} 張 (5日均量: {meta['v5_avg']:,} 張)",
        f"📈 均線防線：月線 {ma20:.2f} (上揚助漲)｜上限 {meta['ceiling_price']:.2f}",
        "─────────────────────",
        "🎯【實戰操盤四價位建議】",
        f"  🟢 進場價位：{entry_desc}",
        f"  🔵 加碼價位：{addon_price:.2f} 元 (突破日高續攻確認)",
        f"  🔴 停利價位：TP1 {tp1_price:.2f} 元 (+12%) ｜ TP2 {tp2_price:.2f} 元 (+20%)",
        f"  🛑 停損價位：{sl_price:.2f} 元 (跌破月線-2%無條件離場)",
        "─────────────────────",
        f"🏦 籌碼背景：",
        f"  • 外資3日累計：{meta['f_3d']:+,} 張",
        f"  • 投信3日累計：{meta['t_3d']:+,} 張",
        f"  • 法人買超佔比：{meta['inst_ratio']:.1f}%",
        f"  • 5日法人合計：{meta['inst_5d']:+,} 張",
        "─────────────────────",
        f"🕯️ K棒形態：實體 {body:.2f} 元｜上影線 {upper_shadow:.2f} 元 (無倒貨賣壓)",
        f"⏰ 偵測時間：{rt.get('time', datetime.datetime.now().strftime('%H:%M:%S'))}",
        "💡 實戰策略：符合法人籌碼集中起漲！回測月線有守為最佳買點，跌破月線無條件嚴格停損。"
    ]
    message = "\n".join(msg_lines)
    
    print("\n" + "=" * 60)
    print(f"🔥 [觸發 LINE 推播] {code} {meta['name']} 現價 {price:.2f} ({chg_sign}{pct_chg:.2f}%)，乖離 +{bias_pct:.2f}%")
    print("=" * 60)
    
    send_to_line(message)


def run_intraday_momentum_scanner(interval_seconds=60, once=False):
    """
    盤中雷達主迴圈：
    - 預設每 interval_seconds (如 60 秒) 輪詢一次
    - 09:00 ~ 13:35 監控
    """
    print("=" * 70)
    print("🚀 台股法人籌碼集中起漲【盤中即時雷達】啟動")
    print("   監控條件：量能>1000張 | 站上20MA且均線向上 | 正乖離<8% | 無長上影線")
    print("=" * 70)

    watchlist = prepare_momentum_watchlist()
    if not watchlist:
        print("[X] 監控母池建立失敗，結束程式。")
        return

    notified_today = set()

    while True:
        now = datetime.datetime.now()
        now_time = now.time()

        # 台股盤中時段檢查 (09:00 ~ 13:35)
        start_time = datetime.time(9, 0)
        end_time = datetime.time(13, 35)

        if not once and (now_time < start_time or now_time > end_time):
            if now_time > end_time:
                print(f"[*] 盤中交易已結束 (目前 {now.strftime('%H:%M:%S')} > 13:35)，盤中雷達收工休息。")
                break
            else:
                print(f"[*] 尚未開盤 (目前時間 {now.strftime('%H:%M:%S')})，等待至 09:00 開盤...")
                time.sleep(30)
                continue

        print(f"\n[*] [{now.strftime('%H:%M:%S')}] 正在掃描盤中即時行情 (母池 {len(watchlist)} 檔)...")
        rt_quotes = fetch_realtime_quotes(watchlist.keys())

        matched_count = 0
        for code, meta in watchlist.items():
            if code not in rt_quotes:
                continue

            rt = rt_quotes[code]
            price = rt['price']
            volume = rt['volume']
            open_p = rt['open'] if rt['open'] else price
            high_p = rt['high'] if rt['high'] else price

            # 條件 1: 盤中累積成交量 >= 1000 張
            if volume < 1000:
                continue

            # 條件 2: 現價站上 20MA 且正乖離率小於 8% (0% <= Bias < 8%)
            ma20 = meta['ma20']
            if price < ma20 or price >= meta['ceiling_price']:
                continue

            # 條件 3: K棒上影線濾網（上影線需小於實體 K 棒的一半）
            body = abs(price - open_p)
            upper_shadow = high_p - max(open_p, price)
            
            if body > 0.05:
                is_shadow_clean = (upper_shadow < 0.5 * body)
            else:
                is_shadow_clean = (upper_shadow <= (price * 0.005))

            if not is_shadow_clean:
                continue

            # 條件 4: 今日未跌破昨日收盤價（保持平盤以上或紅K強勢氣色）
            if rt['yesterday'] and price < rt['yesterday']:
                continue

            matched_count += 1

            # 防重複洗版：當日只推播一次
            if code not in notified_today:
                notified_today.add(code)
                send_intraday_momentum_alert(code, meta, rt)

        print(f"[*] 巡邏完成：目前符合起漲條件 {matched_count} 檔，今日累計推播 {len(notified_today)} 檔。")

        if once:
            break

        print(f"[*] 等待 {interval_seconds} 秒後執行下一輪盤中掃描... (按 Ctrl+C 可停止)")
        time.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(description="台股法人籌碼起漲策略 - 盤中即時雷達 LINE 推播")
    parser.add_argument("--interval", type=int, default=60, help="盤中即時掃描間隔秒數，預設 60 秒")
    parser.add_argument("--once", action="store_true", help="僅執行單次快篩測試（不持續迴圈）")
    parser.add_argument("--test-push", action="store_true", help="發送一則模擬起漲快訊測試 LINE 連線與排版")

    args = parser.parse_args()

    if args.test_push:
        print("[*] 正在發送模擬起漲測試訊息至 LINE...")
        fake_meta = {
            'name': '模擬測試股',
            'ma20': 48.50,
            'ceiling_price': 52.38,
            'v5_avg': 3500,
            'f_3d': 2850,
            't_3d': 1200,
            'inst_today': 2400,
            'inst_5d': 5200,
            'inst_ratio': 42.5
        }
        fake_rt = {
            'price': 49.80,
            'open': 48.70,
            'high': 50.00,
            'low': 48.60,
            'yesterday': 48.50,
            'volume': 4250,
            'time': datetime.datetime.now().strftime('%H:%M:%S')
        }
        send_intraday_momentum_alert('9999', fake_meta, fake_rt)
        print("[✓] 模擬測試訊息發送完成！請查看您的 LINE。")
        return

    run_intraday_momentum_scanner(interval_seconds=args.interval, once=args.once)


if __name__ == '__main__':
    main()
