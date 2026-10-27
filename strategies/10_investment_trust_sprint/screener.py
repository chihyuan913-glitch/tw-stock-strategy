#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 10：投信認養集中度爆發飆股策略 (Investment Trust Sprint Momentum Screener)
================================================================================
本選股器嚴格遵循《總指揮中心規範》之 4+1 維度硬篩與二階段軟評分機制：

【一、4+1 核心維度硬性過濾條件 (Hard Filters <= 5)】
  1. 母池與流動性防守：5日均量 >= 1,000 張，排除處置股、全額交割股、DR股。
  2. 趨勢位階線：收盤價 >= 20MA（月線），且月線走揚或走平 (MA20 >= MA20_5日前 * 0.995)。
  3. 籌碼核心面：投信密集認養連續吃貨：
     - Tier-1 精選：投信近 3 個交易日連續淨買超，5日累計買超 >= 800 張 (或今日佔比 >= 5%)。
     - Tier-2 候補：若 Tier-1 為 0 檔，自動啟用投信連買 >= 2 天且 5日累計 >= 400 張。
  4. 型態防守面：當日收紅 K 棒 (Close > Open) 且上影線長度 < 實體長度之一半 (排除拉高出貨假突破)。
  5. 空間防追高：月線正乖離率 <= 6.5% (剛自整理平台帶量起漲，拒絕過度追高)。

【二、第二階段軟評分矩陣 (100 分制)】
  - 投信連續認養天數 (最高 30 分)：>= 5 天 (+30分), 4 天 (+22分), 3 天 (+15分)
  - 投信買超佔成交量比 (最高 25 分)：>= 12% (+25分), >= 8% (+18分), >= 5% (+12分)
  - 5日投信累計吃貨張數 (最高 20 分)：>= 3,000 張 (+20分), >= 1,500 張 (+14分), >= 800 張 (+8分)
  - 均量放大倍數 Rvol (最高 15 分)：當日成交量 / 5日均量 >= 1.5 (+15分), >= 1.2 (+10分)
  - 起漲甜蜜正乖離 (最高 10 分)：正乖離在 +1.5% ~ +4.5% 最優 (+10分)
  - 風控扣分 (algo_guards)：隔日沖分點高度扎堆扣 5~15 分，標註警示。

【三、實戰四大防線價位 (TWSE 法定檔位合規引擎 V4.0)】
  - 🟢 建議進場價：方向性合規取整之建倉上限 (現價或頸線突破位)
  - 🔵 動能加碼價：突破今日高點 1 檔續強確認點
  - 🔴 停利目標價：TP1 (+6%~+8%), TP2 (+12%~+16%)
  - 🛑 嚴格停損價：跌破 20MA -1.5% 或進場價 -4.5% 嚴守風控
================================================================================
"""

import sys
import os
import argparse
import datetime
import json
import urllib.request
import pandas as pd
import numpy as np
import yfinance as yf

# 加入專案根目錄至 sys.path 以引用共用模組
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from algo_guards import evaluate_overnight_broker_risk
from four_price_engine import calculate_four_prices_long
from rolling_retention_manager import save_rolling_result_csv
from line_sender import (
    send_to_line,
    build_screener_carousel,
    format_clean_screener_text,
    format_screener_markdown_cards
)

try:
    from adaptive_regime_manager import get_strategy_adaptive_params, load_adaptive_config
except ImportError:
    def get_strategy_adaptive_params(strat_id):
        return {}
    def load_adaptive_config():
        return {}

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
STRATEGY_NAME = "策略 10：投信認養集中度爆發飆股策略"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')
MD_FILE = os.path.join(os.path.dirname(__file__), 'result.md')

STANDARD_COLUMNS = [
    '證券代號', '證券名稱', '梯隊標籤', '星級', '評分', '收盤價', '進場價位', '進場參考', 
    '加碼價位', '停利TP1', '停利TP2', '停損價位', '投信連買天數', '今日投信買超(張)', 
    '投信佔成交量%', '5日投信累計(張)', '外資買超(張)', '法人買超合計(張)', 
    '當日成交量(張)', '5日均量(張)', '均量比Rvol', '月線(20MA)', '月線乖離%', '隔日沖風控'
]


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
    """批次抓取近 N 日的三大法人買賣超 (T86)"""
    print(f"[*] 正在抓取近 {len(dates)} 個交易日的三大法人與投信籌碼數據: {dates} ...")
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
    """抓取指定日期的每日收盤行情 (MI_INDEX)"""
    print(f"[*] 正在連線證交所抓取 {date_str} 盤後收盤行情 (OHLCV)...")
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


def calculate_trust_sprint_score(trust_consec_days: int, trust_today_ratio: float, 
                                 trust_5d_lots: int, rvol: float, bias_pct: float, 
                                 tier_label: str = "Tier-1", broker_penalty: int = 0) -> tuple:
    """第二階段投信認養軟評分矩陣 (滿分 100 分)"""
    if trust_consec_days >= 5:
        s_consec = 30
    elif trust_consec_days >= 4:
        s_consec = 22
    elif trust_consec_days >= 3:
        s_consec = 15
    else:
        s_consec = 8

    if trust_today_ratio >= 12.0:
        s_ratio = 25
    elif trust_today_ratio >= 8.0:
        s_ratio = 18
    elif trust_today_ratio >= 5.0:
        s_ratio = 12
    else:
        s_ratio = 6

    if trust_5d_lots >= 3000:
        s_accum = 20
    elif trust_5d_lots >= 1500:
        s_accum = 14
    elif trust_5d_lots >= 800:
        s_accum = 8
    else:
        s_accum = 4

    if rvol >= 1.8:
        s_vol = 15
    elif rvol >= 1.3:
        s_vol = 10
    else:
        s_vol = 5

    if 1.5 <= bias_pct <= 4.5:
        s_bias = 10
    elif 0.0 <= bias_pct < 1.5:
        s_bias = 7
    else:
        s_bias = 4

    raw_score = s_consec + s_ratio + s_accum + s_vol + s_bias
    if tier_label == "Tier-2":
        raw_score = int(raw_score * 0.88)

    final_score = max(0, min(100, raw_score - broker_penalty))

    if final_score >= 80:
        stars = "★★★★★"
    elif final_score >= 65:
        stars = "★★★★☆"
    elif final_score >= 50:
        stars = "★★★☆☆"
    else:
        stars = "★★☆☆☆"

    return final_score, stars


def screen_investment_trust_sprint(date_str: str = None, min_vol_lots: int = 1000,
                                   max_bias_pct: float = 6.5, min_trust_consec_days: int = 3,
                                   min_trust_5d_lots: int = 800):
    """
    執行策略 10 雙層動態降級容錯選股流程
    - Tier-1：投信連續認養爆發精選 (連買>=3天, 5日累計>=800張, 5日均量>=1000張, 乖離<=6.5%)
    - Tier-2：投信新點火候補 (若 Tier-1 為 0 檔，自動啟用連買>=2天, 5日累計>=400張, 5日均量>=800張)
    """
    if date_str:
        trading_dates = [date_str]
        all_dates = get_recent_trading_dates(n_days=10)
        if date_str in all_dates:
            idx = all_dates.index(date_str)
            trading_dates = all_dates[idx:idx+5]
        else:
            dt = datetime.datetime.strptime(date_str, '%Y%m%d').date()
            trading_dates = []
            for i in range(12):
                d = dt - datetime.timedelta(days=i)
                if d.weekday() < 5:
                    trading_dates.append(d.strftime('%Y%m%d'))
                if len(trading_dates) == 5:
                    break
    else:
        trading_dates = get_recent_trading_dates(n_days=5)
        if not trading_dates:
            print("[X] 無法取得最新交易日期，請檢查網路連線或手動指定 --date YYYYMMDD")
            return []

    latest_date = trading_dates[0]
    dates_5d = trading_dates[:min(5, len(trading_dates))]

    print(f"[*] 執行基準日：{latest_date}（投信檢核交易日清單：{dates_5d}）")

    chips_history = fetch_institutional_data(dates_5d)
    quotes = fetch_daily_quotes(latest_date)

    if not quotes or not chips_history:
        print("[X] 資料獲取不完整，終止篩選。")
        return []

    print(f"[*] 今日共取得 {len(quotes)} 檔個股行情，{len(chips_history)} 檔個股籌碼記錄。")

    print("[*] 執行第一階段投信認養與型態快篩...")
    preliminary_candidates = []
    preliminary_meta = {}

    for code, q in quotes.items():
        if code not in chips_history or latest_date not in chips_history[code]:
            continue

        c_today = chips_history[code][latest_date]
        trust_today_shares = c_today['trust']

        # 投信今日必須買超且有基本成交量 (放寬至 800 張以涵蓋 Tier-2 候補)
        if trust_today_shares <= 0 or q['volume'] < (800 * 1000):
            continue

        consec_trust_days = 0
        for d in dates_5d:
            if d in chips_history[code] and chips_history[code][d]['trust'] > 0:
                consec_trust_days += 1
            else:
                break

        # 投信連續買超至少 2 天以上方可列入候選
        if consec_trust_days < 2:
            continue

        trust_5d_shares = sum(chips_history[code][d]['trust'] for d in dates_5d if d in chips_history[code])
        trust_5d_lots = trust_5d_shares // 1000
        trust_today_ratio = round((trust_today_shares / q['volume']) * 100.0, 2) if q['volume'] > 0 else 0.0

        # 型態防守：收紅或平盤帶下影線，且上影線不可超過實體 60%
        body = q['close'] - q['open']
        upper_shadow = q['high'] - max(q['open'], q['close'])
        if body < 0:
            continue
        if body > 0.05 and upper_shadow >= (0.6 * body):
            continue

        preliminary_candidates.append(code)
        preliminary_meta[code] = {
            'quote': q,
            'trust_today_lots': trust_today_shares // 1000,
            'trust_today_ratio': trust_today_ratio,
            'trust_consec_days': consec_trust_days,
            'trust_5d_lots': trust_5d_lots,
            'foreign_today_lots': c_today['foreign'] // 1000,
            'total_today_lots': c_today['total'] // 1000,
            'body': round(body, 2),
            'upper_shadow': round(upper_shadow, 2)
        }

    print(f"[*] 通過投信認養與型態初篩個股共 {len(preliminary_candidates)} 檔。")
    if not preliminary_candidates:
        print("[-] 今日全市場無符合投信連續買超 >= 2 天之標的。")
        return []

    print("[*] 執行第二階段技術面精準過濾（計算 20MA 走揚與正乖離率）...")
    tickers = [f"{c}.TW" for c in preliminary_candidates]
    try:
        hist_data = yf.download(tickers, period='3mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 下載歷史 K 線失敗: {e}")
        return []

    tier1_results = []
    tier2_results = []

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

            ma20 = close_series.rolling(20).mean()
            ma20_curr = float(ma20.iloc[-1])
            ma20_prev = float(ma20.iloc[-5]) if len(ma20) >= 5 else float(ma20.iloc[-2])

            curr_close = float(q['close'])

            # 收盤價站上月線且月線走揚或持平
            if curr_close < ma20_curr or ma20_curr < (ma20_prev * 0.995):
                continue

            v5_avg_shares = float(vol_series.rolling(5).mean().iloc[-1])
            v5_avg_lots = v5_avg_shares / 1000.0

            bias_ma20_pct = round(((curr_close - ma20_curr) / ma20_curr) * 100.0, 2)
            if bias_ma20_pct < 0.0:
                continue

            curr_vol_lots = q['volume'] // 1000
            rvol = round(curr_vol_lots / v5_avg_lots, 2) if v5_avg_lots > 0 else 1.0

            og_risk = evaluate_overnight_broker_risk(
                code,
                meta={
                    'volume': q['volume'],
                    'dealer_hedge': chips_history.get(code, {}).get(latest_date, {}).get('dealer', 0),
                    'foreign': chips_history.get(code, {}).get(latest_date, {}).get('foreign', 0),
                    'inst_ratio': meta['trust_today_ratio']
                }
            )

            # 四大實戰操盤防線 (TWSE 合規取整引擎 V4.0)
            fp_calc = calculate_four_prices_long(
                curr_price=curr_close,
                high=q.get('high'),
                low=q.get('low'),
                ma20=ma20_curr
            )
            entry_p = fp_calc['entry_price']
            addon_p = fp_calc['addon_price']
            tp1_p = fp_calc['tp1_price']
            tp2_p = fp_calc['tp2_price']
            sl_p = fp_calc['sl_price']

            # Tier-1 檢驗：連買>=3天, 5日累計>=800張(或今日佔比>=5%), 5日均量>=1000張, 乖離<=6.5%
            is_tier1 = (
                meta['trust_consec_days'] >= min_trust_consec_days and
                (meta['trust_5d_lots'] >= min_trust_5d_lots or meta['trust_today_ratio'] >= 5.0) and
                v5_avg_lots >= min_vol_lots and
                bias_ma20_pct <= max_bias_pct and
                meta['body'] > 0
            )

            # Tier-2 候補檢驗：連買>=2天, 5日累計>=400張, 5日均量>=800張, 乖離<=7.5%
            is_tier2 = (
                meta['trust_consec_days'] >= 2 and
                (meta['trust_5d_lots'] >= 400 or meta['trust_today_ratio'] >= 3.5) and
                v5_avg_lots >= 800 and
                bias_ma20_pct <= (max_bias_pct + 1.0)
            )

            if is_tier1:
                score, stars = calculate_trust_sprint_score(
                    trust_consec_days=meta['trust_consec_days'],
                    trust_today_ratio=meta['trust_today_ratio'],
                    trust_5d_lots=meta['trust_5d_lots'],
                    rvol=rvol,
                    bias_pct=bias_ma20_pct,
                    tier_label="Tier-1",
                    broker_penalty=og_risk['penalty_score']
                )
                tier1_results.append({
                    '證券代號': code,
                    '證券名稱': q['name'],
                    '梯隊標籤': '🟢 Tier-1 認養精選',
                    '星級': stars,
                    '評分': score,
                    '收盤價': round(curr_close, 2),
                    '進場價位': entry_p,
                    '進場參考': entry_p,
                    '加碼價位': addon_p,
                    '停利TP1': tp1_p,
                    '停利TP2': tp2_p,
                    '停損價位': sl_p,
                    '投信連買天數': meta['trust_consec_days'],
                    '今日投信買超(張)': meta['trust_today_lots'],
                    '投信佔成交量%': meta['trust_today_ratio'],
                    '5日投信累計(張)': meta['trust_5d_lots'],
                    '外資買超(張)': meta['foreign_today_lots'],
                    '法人買超合計(張)': meta['total_today_lots'],
                    '當日成交量(張)': curr_vol_lots,
                    '5日均量(張)': int(v5_avg_lots),
                    '均量比Rvol': rvol,
                    '月線(20MA)': round(ma20_curr, 2),
                    '月線乖離%': bias_ma20_pct,
                    '隔日沖風控': og_risk['tag']
                })
            elif is_tier2:
                score, stars = calculate_trust_sprint_score(
                    trust_consec_days=meta['trust_consec_days'],
                    trust_today_ratio=meta['trust_today_ratio'],
                    trust_5d_lots=meta['trust_5d_lots'],
                    rvol=rvol,
                    bias_pct=bias_ma20_pct,
                    tier_label="Tier-2",
                    broker_penalty=og_risk['penalty_score']
                )
                tier2_results.append({
                    '證券代號': code,
                    '證券名稱': q['name'],
                    '梯隊標籤': '🟡 Tier-2 點火候補',
                    '星級': stars,
                    '評分': score,
                    '收盤價': round(curr_close, 2),
                    '進場價位': entry_p,
                    '進場參考': entry_p,
                    '加碼價位': addon_p,
                    '停利TP1': tp1_p,
                    '停利TP2': tp2_p,
                    '停損價位': sl_p,
                    '投信連買天數': meta['trust_consec_days'],
                    '今日投信買超(張)': meta['trust_today_lots'],
                    '投信佔成交量%': meta['trust_today_ratio'],
                    '5日投信累計(張)': meta['trust_5d_lots'],
                    '外資買超(張)': meta['foreign_today_lots'],
                    '法人買超合計(張)': meta['total_today_lots'],
                    '當日成交量(張)': curr_vol_lots,
                    '5日均量(張)': int(v5_avg_lots),
                    '均量比Rvol': rvol,
                    '月線(20MA)': round(ma20_curr, 2),
                    '月線乖離%': bias_ma20_pct,
                    '隔日沖風控': og_risk['tag']
                })

        except Exception as e:
            continue

    if tier1_results:
        print(f"[✓] 成功篩選出 Tier-1 投信密集認養精選標的共 {len(tier1_results)} 檔。")
        return sorted(tier1_results, key=lambda x: x['評分'], reverse=True)
    elif tier2_results:
        print(f"[*] Tier-1 精選為 0 檔，自動啟用【Tier-2 投信新點火候補機制】（共 {len(tier2_results)} 檔候補）...")
        sorted_tier2 = sorted(tier2_results, key=lambda x: x['評分'], reverse=True)
        return sorted_tier2[:6]
    else:
        print("[-] 今日全市場無符合 Tier-1 或 Tier-2 門檻之標的。")
        return []


def main():
    parser = argparse.ArgumentParser(description="台股策略 10：投信認養集中度爆發飆股策略")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYYMMDD，預設為最新交易日)")
    parser.add_argument("--min-vol", type=int, default=1000, help="最低5日平均成交量(張)，預設 1000 張")
    parser.add_argument("--max-bias", type=float, default=6.5, help="20MA 正乖離上限百分比(%%)，預設 6.5%%")
    parser.add_argument("--consec-days", type=int, default=3, help="投信連續淨買超天數門檻，預設 3 天")
    parser.add_argument("--trust-5d", type=int, default=800, help="投信近5日累計淨買超張數低標，預設 800 張")
    parser.add_argument("--export", type=str, default=None, help="匯出報表檔名 (.csv 或 .md)")
    parser.add_argument("--line", action="store_true", help="發送選股通知至 LINE 視窗")
    parser.add_argument("--force", action="store_true", help="強制執行選股運算")

    args = parser.parse_args()

    try:
        adap_p = get_strategy_adaptive_params("10")
        if adap_p:
            if args.min_vol == 1000 and "min_volume" in adap_p:
                args.min_vol = int(adap_p["min_volume"])
            if args.max_bias == 6.5 and "bias_max_pct" in adap_p:
                args.max_bias = float(adap_p["bias_max_pct"])
    except Exception:
        pass

    results = screen_investment_trust_sprint(
        date_str=args.date,
        min_vol_lots=args.min_vol,
        max_bias_pct=args.max_bias,
        min_trust_consec_days=args.consec_days,
        min_trust_5d_lots=args.trust_5d
    )

    if not results:
        print("\n[!] 經檢核，所選條件下今日無符合投信認養爆發之標的。")
        if args.export and args.export.endswith('.csv'):
            export_path = args.export
            if not os.path.isabs(export_path) and os.path.dirname(export_path) == "":
                export_path = os.path.join(os.path.dirname(__file__), export_path)
            if not os.path.exists(export_path):
                empty_df = pd.DataFrame(columns=STANDARD_COLUMNS)
                empty_df.to_csv(export_path, index=False, encoding='utf-8-sig')
                print(f"[✓] 已建立標準空白 CSV 結構檔: {export_path}")
        return

    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values(by=["評分", "投信佔成交量%"], ascending=[False, False]).reset_index(drop=True)

    print("\n" + "="*95)
    print(f"🎯【{STRATEGY_NAME}】選股結果清單（共 {len(df_res)} 檔）")
    print("條件符合：5日均量>=1000張 | 投信連買>=3天 | 5日累計>=800張 | 站穩20MA且月線走揚 | 乖離<=6.5% | 實體紅K")
    print("="*95)
    print(df_res.to_string(index=False))
    print("="*95)

    if args.export:
        export_path = args.export
        if not os.path.isabs(export_path) and os.path.dirname(export_path) == "":
            export_path = os.path.join(os.path.dirname(__file__), export_path)
        if export_path.endswith('.csv'):
            target_date = args.date or (results[0].get('日期') if results else None) or datetime.date.today().strftime('%Y-%m-%d')
            df_res = save_rolling_result_csv(export_path, df_res, today_date_str=target_date, max_days=5)
            print(f"[✓] 已成功匯出 CSV 報表至: {export_path}")

            md_path = export_path[:-4] + ".md"
            with open(md_path, 'w', encoding='utf-8') as f:
                target_date = args.date or datetime.date.today().strftime('%Y-%m-%d')
                f.write(format_screener_markdown_cards(results, target_date, strategy_id="10", max_stocks=10))
                f.write("\n\n## 📋 完整量化數據指標總表\n\n")
                f.write(df_res.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已同步匯出視覺化字卡 Markdown 戰報至: {md_path}")
        elif export_path.endswith('.md'):
            with open(export_path, 'w', encoding='utf-8') as f:
                target_date = args.date or datetime.date.today().strftime('%Y-%m-%d')
                f.write(format_screener_markdown_cards(results, target_date, strategy_id="10", max_stocks=10))
                f.write("\n\n## 📋 完整量化數據指標總表\n\n")
                f.write(df_res.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已成功匯出視覺化字卡 Markdown 報表至: {export_path}")

    if args.line:
        try:
            target_date = args.date or datetime.date.today().strftime('%Y/%m/%d')
            card = build_screener_carousel(results, target_date, strategy_id="10", max_stocks=6)
            print("[*] 正在發送選股清單至 LINE (策略 10)...")
            success = send_to_line(card, strategy="10")
            if not success:
                clean_text = format_clean_screener_text(results, target_date, strategy_id="10", max_stocks=6)
                send_to_line(clean_text, strategy="10")
        except Exception as e:
            print(f"[!] 發送 LINE 選股通知失敗: {e}")


if __name__ == '__main__':
    main()
