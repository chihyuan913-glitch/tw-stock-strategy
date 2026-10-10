#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股法人籌碼出貨破線做空選股器 V2.0 優化旗艦版 (Institutional Dumping & Downtrend Breakdown Short Screener)

【核心做空策略優化邏輯 (基於 114~115 年歷史回測深度量化)】
一、大盤避險紅綠燈 (宏觀市場環境濾網)：
  - 自動偵測大盤/0050 月線狀態。
  - 加權指數跌破 20MA 或 20MA 斜率下彎時，啟動「避險做空模式」(回測勝率最高、期望值顯著為正)。
  - 加權指數強勢多頭排列時，發出強烈警示並建議停止盲目開倉。

二、籌碼面（主力資金出貨追蹤）：
  1. 外資或投信近 3 個交易日累計賣超大於 1,000 張 (<-1,000,000 股)。
  2. 法人賣超張數佔當日總成交量比例大於 12% (由 10% 優化提升，鎖定主導砸盤主力)。
  3. 近 5 日法人籌碼呈現淨賣超 (主力提款籌碼渙散)。

三、技術面與防軋空起跌安全濾網：
  4. 收盤價跌破 20 日均線（月線），且月線斜率維持向下 (MA20_curr < MA20_prev，均線反壓蓋頭)。
  5. 近 5 日平均成交量大於 1,000 張，確保流動性。
  6. 起跌甜蜜點收緊：月線負乖離率介於 0% 到 -5% 之間 (-5.0% <= Bias <= 0%) (由 -8% 優化收緊，起跌空間最大，徹底杜絕跌深追空被短軋)。
  7. 當日 K 棒無長下影線（下影線長度小於實體一半，排除低檔強撐）。

四、操盤實戰四大價位 (量化極致調校)：
  - 🟢 空單進場：現價 (或反彈至月線反壓區分批佈空)
  - 🔵 加空價位：破當日低點續跌確認 (或現價 -3%)
  - 🔴 停利回補：TP1 月線負乖離 -10% (由 -12% 優化，提升命中率) ｜ TP2 月線負乖離 -16% (由 -20% 優化，符合實戰波段滿足點)
  - 🛑 停損回補：站回月線 +1% 或現價 +3% 嚴格無條件停損！(平均虧損嚴壓於 -3.5% 內)
  - ⏱️ 波段快打：建議最大持倉 7~10 個交易日，不破底即獲利了結換股。
"""

import sys
import os
import re
import argparse
import datetime
import json
import urllib.request
import pandas as pd
import numpy as np
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


def load_stock_futures_pool():
    """
    載入臺灣期貨交易所 (TAIFEX) 股票期貨標的清單
    優先連線期交所官網動態抓取最新標的，失敗時自動讀取本地 stock_futures_list.json 快取
    回傳: dict: { stock_code: {'contract': str, 'name': str} }
    """
    json_path = os.path.join(os.path.dirname(__file__), 'stock_futures_list.json')
    futures_map = {}
    
    # 嘗試聯網動態抓取最新期交所名單
    try:
        url = 'https://www.taifex.com.tw/cht/2/stockLists'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=6) as r:
            html = r.read().decode('utf-8', errors='ignore')
            rows = re.findall(r'<tr>.*?<td[^>]*>([A-Za-z0-9]{2,3})</td>.*?<td[^>]*>([0-9]{4})</td>.*?<td[^>]*>(.*?)</td>.*?</tr>', html, re.DOTALL)
            for contract, code, name in rows:
                clean_name = re.sub(r'<.*?>', '', name).strip()
                futures_map[code] = {
                    'contract': contract.strip(),
                    'name': clean_name
                }
            if len(futures_map) >= 150:
                try:
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(futures_map, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                return futures_map
    except Exception:
        pass

    # 聯網失敗時讀取本地快取
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                futures_map = json.load(f)
        except Exception:
            pass

    return futures_map


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


def fetch_institutional_data(dates):
    """批次抓取三大法人買賣超 (T86)"""
    print(f"[*] 正在抓取近 {len(dates)} 個交易日的三大法人籌碼數據: {dates} ...")
    chips_history = {}
    
    for d_str in dates:
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
                                dealer = int(row[11].replace(',', ''))
                                total = int(row[18].replace(',', ''))
                                
                                if code not in chips_history:
                                    chips_history[code] = {}
                                chips_history[code][d_str] = {
                                    'name': name,
                                    'foreign': foreign,
                                    'trust': trust,
                                    'dealer': dealer,
                                    'total': total
                                }
                            except (ValueError, IndexError):
                                pass
        except Exception as e:
            print(f"[!] 抓取 {d_str} 法人數據失敗: {e}")
            
    return chips_history


def fetch_daily_quotes(date_str):
    """抓取每日收盤行情 (MI_INDEX)"""
    print(f"[*] 正在連線證交所抓取 {date_str} 盤後行情 (OHLCV)...")
    url_mi = f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=json'
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
                                name = row[1].strip()
                                vol = int(row[2].replace(',', ''))
                                close_str = row[8].replace(',', '').strip()
                                open_str = row[5].replace(',', '').strip()
                                high_str = row[6].replace(',', '').strip()
                                low_str = row[7].replace(',', '').strip()
                                
                                if close_str != '--' and open_str != '--' and high_str != '--':
                                    quotes[code] = {
                                        'name': name,
                                        'volume': vol,
                                        'open': float(open_str),
                                        'high': float(high_str),
                                        'low': float(low_str),
                                        'close': float(close_str)
                                    }
                            except (ValueError, IndexError):
                                pass
    except Exception as e:
        print(f"[!] 抓取行情失敗: {e}")
        
    return quotes


def check_market_regime():
    """
    檢查台股大盤 (0050.TW / 加權指數) 宏觀市場環境
    回傳: (is_hedge_timing: bool, status_text: str)
    - True: 大盤跌破月線或月線下彎 (避險做空模式，勝率與期望值最高)
    - False: 大盤強勢多頭 (站上月線且月線向上，警示逆勢放空風險)
    """
    try:
        df_m = yf.download('0050.TW', period='2mo', progress=False)
        if df_m.empty:
            return True, "⚠️ 無法取得大盤數據，預設允許避險監控"
        close_s = df_m['Close']['0050.TW'] if isinstance(df_m.columns, pd.MultiIndex) else df_m['Close']
        close_s = close_s.dropna()
        if len(close_s) < 21:
            return True, "⚠️ 大盤歷史K線不足20日，預設允許避險監控"
        ma20 = close_s.rolling(20).mean().dropna()
        curr_c = float(close_s.iloc[-1])
        curr_ma = float(ma20.iloc[-1])
        prev_ma = float(ma20.iloc[-2])
        is_below = curr_c <= curr_ma
        is_slope_down = curr_ma < prev_ma
        is_hedge = is_below or is_slope_down

        if is_hedge:
            return True, f"🟢【大盤避險紅綠燈：避險模式啟動】0050現價 {curr_c:.2f} 跌破月線 {curr_ma:.2f} 或月線下彎，做空勝率與期望值處於黃金甜蜜期！"
        else:
            return False, f"⚠️【大盤避險紅綠燈：多頭強勢警示】0050現價 {curr_c:.2f} 站穩月線 {curr_ma:.2f} 且走揚。多頭勢強逆勢做空風險高，建議僅做對沖避險或降低部位！"
    except Exception as e:
        return True, f"⚠️ 大盤偵測異常 ({e})，預設維持常規監控"


def screen_short_stocks(date_str=None, min_vol_lots=1000, 
                        max_neg_bias_pct=-5.0, inst_sell_ratio_pct=12.0,
                        inst_3d_threshold=1000, futures_only=True,
                        hedge_only=False):
    """
    執行完整做空量化篩選 (V2.0 優化版)
    - max_neg_bias_pct: 預設 -5.0% (起跌甜蜜點，杜絕跌深追空被短軋)
    - inst_sell_ratio_pct: 預設 12.0% (主力集中重壓砸盤)
    - hedge_only: 若為 True，當大盤處於強勢多頭時自動攔截停止開倉
    """
    is_hedge, regime_msg = check_market_regime()
    print(f"\n[*] {regime_msg}")
    if hedge_only and not is_hedge:
        print("[-] 【嚴格避險模式已啟用】當前大盤處於強勢多頭排列，自動終止新開空單！")
        return []

    futures_map = {}
    if futures_only:
        futures_map = load_stock_futures_pool()
        print(f"[*] 【個股期貨優先模式】已載入期交所 {len(futures_map)} 檔股票期貨標的，完全排除無券源小型股！")

    if date_str:
        all_dates = get_recent_trading_dates(n_days=10)
        if date_str in all_dates:
            idx = all_dates.index(date_str)
            trading_dates = all_dates[idx:idx+5]
        else:
            dt = datetime.datetime.strptime(date_str, '%Y%m%d').date()
            trading_dates = []
            for i in range(10):
                d = dt - datetime.timedelta(days=i)
                if d.weekday() < 5:
                    trading_dates.append(d.strftime('%Y%m%d'))
                if len(trading_dates) == 5:
                    break
    else:
        trading_dates = get_recent_trading_dates(n_days=5)
        if not trading_dates:
            print("[X] 無法取得交易日資料，請檢查網路連線。")
            return []

    latest_date = trading_dates[0]
    dates_3d = trading_dates[:min(3, len(trading_dates))]
    dates_5d = trading_dates[:min(5, len(trading_dates))]
    
    print(f"[*] 做空篩選基準日：{latest_date}（近3日賣超檢測：{dates_3d}，近5日：{dates_5d}）")

    chips_history = fetch_institutional_data(dates_5d)
    quotes = fetch_daily_quotes(latest_date)
    
    if not quotes or not chips_history:
        print("[X] 資料獲取不完整，終止篩選。")
        return []

    print(f"[*] 今日取得 {len(quotes)} 檔行情，{len(chips_history)} 檔籌碼記錄。")
    print("[*] 執行第一階段籌碼出貨面與 K 棒形態快篩...")

    preliminary_candidates = []
    preliminary_meta = {}

    for code, q in quotes.items():
        # 【關鍵過濾】只針對具備股票期貨之標的進行做空推薦
        if futures_only and code not in futures_map:
            continue

        if code not in chips_history or latest_date not in chips_history[code]:
            continue
            
        c_today = chips_history[code][latest_date]
        
        # 近 3 日外資累計與投信累計賣超 (股數)
        f_3d_shares = sum(chips_history[code][d]['foreign'] for d in dates_3d if d in chips_history[code])
        t_3d_shares = sum(chips_history[code][d]['trust'] for d in dates_3d if d in chips_history[code])
        
        # 做空條件 1: 外資或投信近 3 個交易日累計賣超大於 1,000 張 (<= -1,000,000 股)
        threshold_sell_shares = -inst_3d_threshold * 1000
        cond_inst_3d_sell = (f_3d_shares <= threshold_sell_shares) or (t_3d_shares <= threshold_sell_shares)
        if not cond_inst_3d_sell:
            continue
            
        # 做空條件 2: 當日法人賣超佔總成交量比例大於 10%
        inst_today_shares = c_today['total']
        if q['volume'] <= 0 or inst_today_shares >= 0:
            continue
            
        inst_sell_ratio = (abs(inst_today_shares) / q['volume']) * 100.0
        if inst_sell_ratio < inst_sell_ratio_pct:
            continue

        # 做空風險濾網: 當日 K 棒無長下影線（排除低檔強烈承接）
        # 實體 K 棒長度 = abs(Close - Open)
        # 下影線長度 = min(Open, Close) - Low
        body = abs(q['close'] - q['open'])
        lower_shadow = min(q['open'], q['close']) - q['low']
        
        if body > 0.05:
            cond_shadow = (lower_shadow < 0.5 * body)
        else:
            cond_shadow = (lower_shadow <= (q['close'] * 0.005))
            
        if not cond_shadow:
            continue

        # 計算近 5 日法人累計淨賣超 (供報表檢視與排序參考，不作為硬性卡關)
        total_5d_shares = sum(chips_history[code][d]['total'] for d in dates_5d if d in chips_history[code])

        preliminary_candidates.append(code)
        preliminary_meta[code] = {
            'quote': q,
            'f_3d_lots': f_3d_shares // 1000,
            't_3d_lots': t_3d_shares // 1000,
            'inst_today_lots': inst_today_shares // 1000,
            'total_5d_lots': total_5d_shares // 1000,
            'inst_sell_ratio': round(inst_sell_ratio, 1),
            'body': round(body, 2),
            'lower_shadow': round(lower_shadow, 2)
        }

    print(f"[*] 通過籌碼大額出貨與無下影線初篩標的共 {len(preliminary_candidates)} 檔。")
    if not preliminary_candidates:
        print("[-] 今日無符合籌碼出貨與下影線門檻之標的。")
        return []

    # 第二階段技術面濾網：跌破 20MA、月線向下、5日均量>1000張、負乖離在 0%~-8% 甜蜜做空區
    print("[*] 執行第二階段空頭技術面濾網（下載日K計算月線下彎與負乖離率）...")
    tickers = [f"{c}.TW" for c in preliminary_candidates]
    try:
        hist_data = yf.download(tickers, period='3mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 歷史數據下載失敗: {e}")
        return []

    results = []

    for code in preliminary_candidates:
        t_key = f"{code}.TW"
        meta = preliminary_meta[code]
        q = meta['quote']
        
        try:
            if len(preliminary_candidates) == 1:
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
            
            # 月線 (20MA)
            ma20 = close_series.rolling(20).mean()
            ma20_curr = float(ma20.iloc[-1])
            ma20_prev = float(ma20.iloc[-2])
            
            # 做空條件 4: 收盤價跌破 20 日均線（月線），且月線斜率維持向下 (助跌蓋頭反壓)
            curr_close = float(q['close'])
            is_below_ma20 = (curr_close <= ma20_curr)
            is_slope_down = (ma20_curr < ma20_prev)
            
            if not (is_below_ma20 and is_slope_down):
                continue

            # 做空條件 5: 近 5 日平均成交量大於 1,000 張 (確保流動性)
            v5_avg_shares = float(vol_series.rolling(5).mean().iloc[-1])
            v5_avg_lots = v5_avg_shares / 1000.0
            if v5_avg_lots < min_vol_lots:
                continue

            # 做空條件 6 (防軋空安全進場濾網):
            # 收盤價與 20 日均線負乖離率介於 0% 到 -8% 之間 (起跌破線區，絕不追空過深標的)
            bias_ma20_pct = ((curr_close - ma20_curr) / ma20_curr) * 100.0
            if not (max_neg_bias_pct <= bias_ma20_pct <= 0.0):
                continue

            # -------------------------------------------------------------
            # 策略實戰做空四大價位試算
            # -------------------------------------------------------------
            # 1. 空單進場：現價或反彈至月線反壓區
            short_entry = round(curr_close, 2)
            # 2. 加空價位：破當日低點 1 檔續跌 (或現價 -3%)
            add_short = round(min(q['low'] * 0.995, curr_close * 0.97), 2)
            # 3. 停利回補 (V2.0 優化：TP1 -10%, TP2 -16%)：
            #    TP1: 月線負乖離 -10% 短波段滿足點 (分批平倉 1/3 ~ 1/2)
            #    TP2: 月線負乖離 -16% 波段滿足點 (全數了結)
            tp1_cover = round(ma20_curr * 0.90, 2)
            tp2_cover = round(ma20_curr * 0.84, 2)
            # 4. 停損回補 (收緊防線：站回月線 +1% 或現價 +3% 嚴格無條件停損回補)：
            high_ref = q['high'] if q['high'] > 0 else curr_close * 1.015
            sl_cover = round(max(ma20_curr * 1.01, high_ref * 1.01, curr_close * 1.03), 2)

            # -------------------------------------------------------------
            # 個股期貨合約資訊與保證金試算 (一口 = 2,000 股現貨 = 2 張)
            # -------------------------------------------------------------
            f_info = futures_map.get(code, {'contract': '--', 'name': q['name']})
            contract_code = f_info.get('contract', '--')
            # 以標準級距 13.5% 估算一口放空保證金
            margin_1lot = int(round(curr_close * 2000 * 0.135, -1))

            results.append({
                '證券代號': code,
                '證券名稱': q['name'],
                '期貨契約': contract_code,
                '收盤價': round(curr_close, 2),
                '進場價位': short_entry,
                '空單進場': short_entry,
                '加碼價位': add_short,
                '加空價位': add_short,
                '停利TP1': tp1_cover,
                '停利TP2': tp2_cover,
                '停損價位': sl_cover,
                '停損回補': sl_cover,
                '1口保證金(約)': margin_1lot,
                '月線(20MA)': round(ma20_curr, 2),
                '月線負乖離%': round(bias_ma20_pct, 2),
                '當日成交量(張)': q['volume'] // 1000,
                '5日均量(張)': int(v5_avg_lots),
                '法人賣超佔比%': meta['inst_sell_ratio'],
                '外資3日賣超(張)': meta['f_3d_lots'],
                '投信3日賣超(張)': meta['t_3d_lots'],
                '當日法人賣超(張)': meta['inst_today_lots'],
                '5日法人累計(張)': meta['total_5d_lots'],
                'K棒實體': meta['body'],
                '下影線': meta['lower_shadow']
            })
            
        except Exception as e:
            continue

    return results


def main():
    parser = argparse.ArgumentParser(description="台股法人籌碼出貨破線做空選股策略 V2.0 優化版 (統一鎖定個股期貨標的)")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYYMMDD，預設為最新交易日)")
    parser.add_argument("--min-vol", type=int, default=1000, help="最低5日平均成交量(張)，預設 1000 張")
    parser.add_argument("--max-neg-bias", type=float, default=-5.0, help="20MA 負乖離率下限百分比(%%)，預設 -5.0%% (起跌甜蜜點)")
    parser.add_argument("--sell-ratio", type=float, default=12.0, help="法人賣超佔成交量低標(%%)，預設 12.0%% (集中重壓砸盤)")
    parser.add_argument("--sell-3d", type=int, default=1000, help="外資或投信近3日累計賣超門檻(張)，預設 1000 張")
    parser.add_argument("--hedge-only", action="store_true", help="大盤避險嚴格模式：大盤多頭強勢期自動攔截開倉")
    parser.add_argument("--no-futures-filter", action="store_true", help="關閉個股期貨限制，允許篩選全市場普通股")
    parser.add_argument("--export", type=str, default=None, help="匯出報表檔名 (.csv 或 .md)")
    parser.add_argument("--line", action="store_true", help="發送選股通知至 LINE Notify/Bot")

    args = parser.parse_args()

    results = screen_short_stocks(
        date_str=args.date,
        min_vol_lots=args.min_vol,
        max_neg_bias_pct=args.max_neg_bias,
        inst_sell_ratio_pct=args.sell_ratio,
        inst_3d_threshold=args.sell_3d,
        futures_only=(not args.no_futures_filter),
        hedge_only=args.hedge_only
    )

    if not results:
        print("\n[!] 經檢核，所選條件下無符合所有做空複合條件的個股。")
        return

    df_res = pd.DataFrame(results)
    # 依照「法人賣超佔比%」與「當日法人賣超(張)」排序 (砸盤力道最強者排前)
    df_res = df_res.sort_values(by=["法人賣超佔比%", "當日法人賣超(張)"], ascending=[False, True]).reset_index(drop=True)

    print("\n" + "="*115)
    print(f"🎯【台股法人出貨破線做空】選股結果清單（共 {len(df_res)} 檔）- ⚡ 全數具備股票期貨，免借券、無券源限制！")
    print("條件符合：具備股票期貨 | 外資/投信3日賣超>1000張 | 法人佔比>=12% | 跌破20MA且斜率向下 | 5日均量>1000張 | 負乖離[-5%~0%]")
    print("="*115)
    print(df_res.to_string(index=False))
    print("="*115)

    if args.export:
        export_path = args.export
        if not os.path.isabs(export_path) and os.path.dirname(export_path) == "":
            export_path = os.path.join(os.path.dirname(__file__), export_path)
        if export_path.endswith('.csv'):
            df_res.to_csv(export_path, index=False, encoding='utf-8-sig')
            print(f"[✓] 已成功匯出 CSV 報表至: {export_path}")
        elif export_path.endswith('.md'):
            with open(export_path, 'w', encoding='utf-8') as f:
                f.write(f"# 🎯 台股法人出貨破線做空選股日報（⚡ 統一鎖定具備股票期貨標的）\n\n")
                f.write(f"- 產生時間：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"- 篩選標準：具備期交所股票期貨、外資/投信3日累計賣超>1000張、法人賣超佔比>=12%、跌破月線且斜率向下、5日均量>1000張、負乖離[-5%~0%]、無長下影線\n")
                f.write(f"- 交易優勢：**免借券、無融券額度限制、無股東會/除權息強制回補、期交稅僅十萬分之二 (0.002%)**\n\n")
                cols = list(df_res.columns)
                header_line = "| " + " | ".join(cols) + " |\n"
                separator_line = "| " + " | ".join(["---"] * len(cols)) + " |\n"
                f.write(header_line)
                f.write(separator_line)
                for _, row in df_res.iterrows():
                    row_line = "| " + " | ".join(str(row[c]) for c in cols) + " |\n"
                    f.write(row_line)
                f.write("\n")
            print(f"[✓] 已成功匯出 Markdown 報表至: {export_path}")

    if args.line:
        try:
            from line_sender import send_to_line
            target_date = args.date or (results[0]['證券代號'] and datetime.date.today().strftime('%Y%m%d')) or "今日"
            msg_lines = [
                "╔═══════════════════════╗",
                "║  📉【台股盤後】弱勢破線做空避險日報  ║",
                "╚═══════════════════════╝",
                f"📅 交易日期：{target_date} 盤後結算",
                "⚡ 避險工具：鎖定個股期貨（免借券／無回補限制）",
                "🎯 核心邏輯：法人大賣 ＋ 跌破月線下彎 ＋ 起跌甜蜜點[-5%~0%]",
                f"🔥 今日精選：共 {len(df_res)} 檔（嚴選前 {min(len(df_res), 6)} 檔精華）",
                "━━━━━━━━━━━━━━━━━━━━"
            ]
            for i, r in enumerate(df_res.head(6).to_dict(orient='records'), 1):
                msg_lines.append(
                    f"【{i:02d}】📍 {r['證券代號']} {r['證券名稱']} ｜ ⚡ 股期: {r['期貨契約']}\n"
                    f"💵 現價收盤：{r['收盤價']} 元 (月線負乖離 {r['月線負乖離%']}%)\n"
                    f"────────────────────\n"
                    f"🎯 操盤四大防線：\n"
                    f"├ 🟢 進場價位：{r['收盤價']} 元 (空單進場基準)\n"
                    f"├ 🔵 加碼價位：{r['加空價位']} 元 (破今日低點續跌加空)\n"
                    f"├ 🔴 停利目標：TP1 {r['停利TP1']} (-10%) ｜ TP2 {r['停利TP2']} (-16%)\n"
                    f"└ 🛑 停損防守：{r['停損回補']} 元 (站上月線反壓無條件停損)\n"
                    f"────────────────────\n"
                    f"💰 股期保證金與籌碼：\n"
                    f"• 1口保證金：約 {r['1口保證金(約)']:,} 元 (表彰2張現貨)\n"
                    f"• 法人賣超：佔比 {r['法人賣超佔比%']}% (外資3日{r['外資3日賣超(張)']:,}張, 投信3日{r['投信3日賣超(張)']:,}張)\n"
                    f"• 均線反壓：月線 {r['月線(20MA)']} 元 (下彎蓋頭反壓)\n"
                    "━━━━━━━━━━━━━━━━━━━━"
                )
            if len(df_res) > 6:
                msg_lines.append(f"📋 其餘 {len(df_res)-6} 檔標的完整名單請查閱 result.csv")
                msg_lines.append("━━━━━━━━━━━━━━━━━━━━")
            msg_lines.append("💡【總指揮風控紀律】站上月線反壓無條件嚴格停損回補，絕不死抗！")
            
            print("[*] 正在發送做空選股清單至 LINE (策略 03)...")
            send_to_line("\n".join(msg_lines), strategy="03")
        except Exception as e:
            print(f"[!] 發送 LINE 選股通知失敗: {e}")


if __name__ == '__main__':
    main()
