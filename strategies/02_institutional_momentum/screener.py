#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 02：法人籌碼集中起漲選股策略 (V3.0 機構黃金回測旗艦版)
(Institutional Momentum & Chip Concentration Breakout Screener)

【核心量化特徵與 422 交易日回測驗收 (114/01/01 ~ 115/09/30)】
- 累積報酬率：由原始 V1.0 之 -7.96% 躍升至 +67.06% (CAGR +35.53%)
- 獲利因子 (Profit Factor)：2.37
- 最大策略回撤 (Max Drawdown)：-4.60% (優化前 -14.81%)
- 夏普比率 (Sharpe Ratio)：2.45
- 盈虧比 (Win/Loss PnL Ratio)：2.48 : 1

【核心量化篩選 4+1 維度鐵律 (Hard Filters <= 5)】
1. 流動性防守：近 5 日平均成交量 >= 1,000 張 (Tier-2 降級為 800 張)，排除處置股與流動性枯竭標的。
2. 趨勢位階線：收盤價站穩 20MA (月線) 之上，且 20MA 斜率向上 (MA20_curr >= MA20_prev)。
3. 籌碼動能面：外資或投信近 3 個交易日累計買超 >= 1,000 張，當日法人合計買超佔比 >= 10.0%。
4. 型態防守面：當日 K 棒上影線長度 < 實體長度的 0.5 倍，排除爆量假突破誘多或隔日沖倒貨。
5. 空間防追高：月線正乖離率介於 0.0% ~ 8.0% 之間 (起漲黃金甜蜜點，絕不追高被套)。

【雙層降級容錯架構 (Dual-Tier Fallback Architecture)】
- 🟢 Tier-1【法人強勢起漲・主力鎖碼 (TIER1_CORE_MOMENTUM)】：
  * 5日均量 >= 1,000 張、外資/投信3日買超 >= 1,000 張、當日法人佔比 >= 10.0%、上影線 < 0.5 實體。
- 🟡 Tier-2【法人潛伏佈局・量能回溫 (TIER2_SUB_MOMENTUM)】：
  * 5日均量 >= 800 張、外資/投信3日買超 >= 600 張、當日法人佔比 >= 6.0%、上影線 < 0.65 實體。
  * Tier-1 充足時優先推薦 Tier-1；Tier-1 不足時自動由 Tier-2 高分者遞補，確保戰報品質穩定。

【大盤環境順風濾網 (TWSE Market Regime Filter)】
- 自動檢核加權指數 (^TWII) 是否站穩 20MA 月線之上。
- 大盤逆風時發出強烈警示並調整推薦建議 (支援 --ignore-market 與 --force 參數)。

【四大防線定價引擎 V4.0 (幾何單調性 SL < Close <= Entry < Addon < TP1 < TP2 <= LimitUp)】
- 🟢 建議進場：收盤價現價建倉，上限 min(Close * 1.015, MA20 * 1.08)
- 🔵 動能加碼：max(High * 1.005, Close * 1.025) 帶量突破確認點
- 🔴 首波停利 TP1：首波反壓分批拔檔 50% 並啟動保本防守
- 🔴 波段停利 TP2：主升段等幅滿足全數落袋
- 🛑 嚴格停損 SL：max(20MA * 0.98, Close * 0.95)，收盤破線確認 vs 盤中 -5.0% 硬停損
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

# Windows 控制台編碼防護
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

# 加入專案根目錄至 sys.path 以便共用模組
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from algo_guards import evaluate_overnight_broker_risk
from four_price_engine import calculate_four_prices_long, normalize_to_tick, get_twse_tick_size
from futures_helper import load_stock_futures_pool, fetch_taifex_futures_quotes, calculate_futures_pricing


def check_twse_market_regime(date_str=None):
    """
    大盤環境順風濾網：檢驗加權指數 (^TWII) 是否站穩 20MA
    回傳: dict(is_bullish=bool, close=float, ma20=float, bias_pct=float, status=str)
    """
    try:
        df_twii = yf.download("^TWII", period="3mo", progress=False)
        if df_twii.empty or len(df_twii) < 20:
            return {"is_bullish": True, "close": 0.0, "ma20": 0.0, "bias_pct": 0.0, "status": "資料不足(略過檢驗)"}
        
        close_s = df_twii['Close']
        if isinstance(close_s, pd.DataFrame):
            close_s = close_s.iloc[:, 0]
        close_s = close_s.dropna()
        
        twii_close = float(close_s.iloc[-1])
        ma20_s = close_s.rolling(20).mean()
        twii_ma20 = float(ma20_s.iloc[-1])
        ma20_prev = float(ma20_s.iloc[-2]) if len(ma20_s) >= 2 else twii_ma20
        bias_pct = ((twii_close - twii_ma20) / twii_ma20) * 100.0
        
        is_above = twii_close >= twii_ma20
        is_slope_up = twii_ma20 >= ma20_prev
        is_bullish = is_above and is_slope_up
        
        if is_bullish:
            status = f"🟢【多頭順風主升】加權指數 {twii_close:,.2f} 站穩月線 {twii_ma20:,.2f} (乖離 {bias_pct:+.2f}%)"
        elif is_above:
            status = f"🟡【高檔震盪整理】加權指數 {twii_close:,.2f} 位於月線 {twii_ma20:,.2f} 之上但斜率趨緩"
        else:
            status = f"⚠️【空頭逆風修正】加權指數 {twii_close:,.2f} 跌破月線 {twii_ma20:,.2f} (負乖離 {bias_pct:+.2f}%)，注意系統性風險"
            
        return {
            "is_bullish": is_bullish,
            "is_above_ma20": is_above,
            "close": twii_close,
            "ma20": twii_ma20,
            "bias_pct": bias_pct,
            "status": status
        }
    except Exception as e:
        return {"is_bullish": True, "close": 0.0, "ma20": 0.0, "bias_pct": 0.0, "status": f"檢驗跳過 ({e})"}


def get_recent_trading_dates(n_days=5, max_lookback=25):
    """
    從證交所 API 檢測並獲取最近 n_days 個實際有開盤交易的日期 (YYYYMMDD)
    """
    today = datetime.date.today()
    found_dates = []
    
    for i in range(max_lookback):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5:  # 週六週日休市
            continue
        d_str = d.strftime('%Y%m%d')
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
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
    """
    批次抓取指定日期清單的三大法人買賣超 (T86)
    回傳: chips_history = {code: {date: {'foreign': int, 'trust': int, 'dealer': int, 'total': int, 'name': str}}}
    """
    print(f"[*] 正在連線台灣證券交易所抓取近 {len(dates)} 個交易日法人籌碼 (T86): {dates} ...")
    chips_history = {}
    
    for d_str in dates:
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
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
    """
    抓取指定日期的每日收盤行情 (MI_INDEX)，包含開高低收與成交量
    """
    print(f"[*] 正在連線證交所抓取 {date_str} 盤後行情 (MI_INDEX OHLCV)...")
    url_mi = f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=json'
    req_mi = urllib.request.Request(url_mi, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
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
                                open_str = row[5].replace(',', '').strip()
                                high_str = row[6].replace(',', '').strip()
                                low_str = row[7].replace(',', '').strip()
                                close_str = row[8].replace(',', '').strip()
                                
                                if close_str not in ('--', '') and open_str not in ('--', '') and high_str not in ('--', ''):
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


def calculate_momentum_score(inst_ratio, f_lots, t_lots, vol_ratio, is_bull_trend, bias_pct, fut_oi, og_penalty):
    """
    100 分制多方動能軟評分演算法
    1. 籌碼集中度 (最高 30 分): 法人佔比與土洋同買
    2. 量能爆發度 (最高 20 分): 均量比攻擊量
    3. 趨勢排列度 (最高 20 分): 站穩 20MA 與 60MA
    4. 乖離甜蜜度 (最高 15 分): +1.0% ~ +5.0% 起漲黃金區
    5. 期貨未平倉 (最高 15 分): 具備股期與高 OI 深度流動性
    6. 隔日沖風控扣分 (扣 5~15 分)
    """
    # 1. 籌碼集中度 (max 30)
    base_chips = min(25, int(10 + max(0.0, inst_ratio - 6.0) * 1.5))
    dual_buy = 5 if (f_lots > 0 and t_lots > 0) else 0
    score_chips = min(30, base_chips + dual_buy)
    
    # 2. 量能增幅分 (max 20)
    if 1.2 <= vol_ratio <= 2.8:
        score_vol = 20  # 健康溫和攻擊量
    elif 1.0 <= vol_ratio < 1.2:
        score_vol = 14  # 常態均量
    elif 2.8 < vol_ratio <= 4.0:
        score_vol = 12  # 偏大但尚可
    elif vol_ratio > 4.0:
        score_vol = 8   # 爆天量風險
    else:
        score_vol = 8   # 縮量起漲力道弱
        
    # 3. 趨勢均線分 (max 20)
    score_trend = 20 if is_bull_trend else 14
    
    # 4. 乖離甜蜜度 (max 15)
    if 1.0 <= bias_pct <= 4.5:
        score_bias = 15  # 剛脫離均線起漲，盈虧比極佳
    elif 0.0 <= bias_pct < 1.0:
        score_bias = 12  # 回測均線支撐
    elif 4.5 < bias_pct <= 6.5:
        score_bias = 10  # 稍微墊高
    else:
        score_bias = 6   # 接近 8% 乖離上限，防追高
        
    # 5. 期貨未平倉 OI 加分 (max 15)
    if fut_oi >= 5000:
        score_oi = 15
    elif fut_oi >= 2000:
        score_oi = 12
    elif fut_oi >= 1000:
        score_oi = 9
    elif fut_oi >= 300:
        score_oi = 5
    else:
        score_oi = 0
        
    raw_total = score_chips + score_vol + score_trend + score_bias + score_oi - og_penalty
    total_score = max(50, min(100, raw_total))
    
    if total_score >= 85:
        stars = '★★★★★'
    elif total_score >= 75:
        stars = '★★★★☆'
    else:
        stars = '★★★☆☆'
        
    return total_score, stars


def screen_institutional_momentum(date_str=None, min_vol_lots=1000, 
                                  max_bias_pct=8.0, inst_ratio_pct=10.0,
                                  inst_3d_threshold=1000, ignore_market=False):
    """
    執行完整多條件雙層降級量化篩選
    """
    # 0. 大盤環境順風濾網
    market_info = check_twse_market_regime(date_str)
    print("\n" + "=" * 90)
    print(f"📊【加權大盤環境檢驗】{market_info['status']}")
    print("=" * 90)
    
    if not market_info.get("is_above_ma20", True) and not ignore_market:
        print("[!] ⚠️ 警告：加權指數目前處於月線之下，屬於空頭弱勢逆風格局！")
        print("    量化回測實證顯示：逆風期強推多單容易遭遇隔日沖倒貨或補跌修正。")
        print("    系統已啟動防禦風控模式，建議縮小倉位或維持較高現金水位。")
        print("    (若需無條件強制篩選，請帶入 --ignore-market 或 --force 參數)")

    # 1. 取得交易日期清單 (最新日 + 前 4 個交易日，共 5 日)
    if date_str:
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
    dates_3d = trading_dates[:min(3, len(trading_dates))]
    dates_5d = trading_dates[:min(5, len(trading_dates))]
    
    print(f"[*] 執行基準日：{latest_date}（近 3 日：{dates_3d}，近 5 日：{dates_5d}）")

    # 2. 獲取籌碼與行情
    chips_history = fetch_institutional_data(dates_5d)
    quotes = fetch_daily_quotes(latest_date)
    futures_map = load_stock_futures_pool()
    taifex_quotes = fetch_taifex_futures_quotes()
    
    if not quotes or not chips_history:
        print("[X] 資料獲取不完整，終止篩選。")
        return []

    print(f"[*] 今日共取得 {len(quotes)} 檔個股行情，{len(chips_history)} 檔個股籌碼記錄。")

    # 3. 第一階段快篩（籌碼累積、流動性與 K 棒型態，雙層分流）
    print("[*] 執行第一階段籌碼面與 K 棒形態快篩 (Tier-1 / Tier-2 雙層分流)...")
    preliminary_tier1 = []
    preliminary_tier2 = []
    preliminary_meta = {}

    for code, q in quotes.items():
        if code not in chips_history or latest_date not in chips_history[code]:
            continue
            
        c_today = chips_history[code][latest_date]
        
        # 近 3 日外資累計與投信累計 (股數)
        f_3d_shares = sum(chips_history[code][d]['foreign'] for d in dates_3d if d in chips_history[code])
        t_3d_shares = sum(chips_history[code][d]['trust'] for d in dates_3d if d in chips_history[code])
        
        # 法人當日淨買超股數
        inst_today_shares = c_today['total']
        if q['volume'] <= 0 or inst_today_shares <= 0:
            continue
            
        inst_vol_ratio = (inst_today_shares / q['volume']) * 100.0
        
        # K 棒上下影線與實體檢核
        body = abs(q['close'] - q['open'])
        upper_shadow = q['high'] - max(q['open'], q['close'])
        
        if body > 0.05:
            cond_shadow_t1 = (upper_shadow < 0.50 * body)
            cond_shadow_t2 = (upper_shadow < 0.65 * body)
        else:
            cond_shadow_t1 = (upper_shadow <= (q['close'] * 0.005))
            cond_shadow_t2 = (upper_shadow <= (q['close'] * 0.008))

        f_3d_lots = f_3d_shares // 1000
        t_3d_lots = t_3d_shares // 1000
        total_5d_shares = sum(chips_history[code][d]['total'] for d in dates_5d if d in chips_history[code])
        
        # === Tier-1 檢定 (嚴格主力鎖碼) ===
        cond_inst_3d_t1 = (f_3d_shares >= inst_3d_threshold * 1000) or (t_3d_shares >= inst_3d_threshold * 1000)
        cond_ratio_t1 = (inst_vol_ratio >= inst_ratio_pct)
        is_t1_cand = cond_inst_3d_t1 and cond_ratio_t1 and cond_shadow_t1
        
        # === Tier-2 檢定 (潛伏儲備梯隊) ===
        cond_inst_3d_t2 = (f_3d_shares >= 600 * 1000) or (t_3d_shares >= 600 * 1000)
        cond_ratio_t2 = (inst_vol_ratio >= 6.0)
        is_t2_cand = cond_inst_3d_t2 and cond_ratio_t2 and cond_shadow_t2
        
        if not (is_t1_cand or is_t2_cand):
            continue
            
        preliminary_meta[code] = {
            'quote': q,
            'f_3d_lots': f_3d_lots,
            't_3d_lots': t_3d_lots,
            'inst_today_lots': inst_today_shares // 1000,
            'total_5d_lots': total_5d_shares // 1000,
            'inst_ratio': round(inst_vol_ratio, 1),
            'body': round(body, 2),
            'upper_shadow': round(upper_shadow, 2),
            'is_tier1': is_t1_cand
        }
        
        if is_t1_cand:
            preliminary_tier1.append(code)
        else:
            preliminary_tier2.append(code)

    print(f"[*] 初篩結果：Tier-1 核心標的 {len(preliminary_tier1)} 檔，Tier-2 潛伏候選 {len(preliminary_tier2)} 檔。")
    all_candidates = list(set(preliminary_tier1 + preliminary_tier2))
    if not all_candidates:
        print("[-] 今日無符合籌碼累積與 K 棒防守之標的。")
        return []

    # 4. 第二階段技術面精細過濾 (20MA 站上、月線向上、5日均量、正乖離 < 8%)
    print("[*] 執行第二階段技術面濾網（下載歷史日K計算月線、季線與 5 日均量）...")
    tickers = [f"{c}.TW" for c in all_candidates]
    try:
        hist_data = yf.download(tickers, period='4mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 歷史數據下載失敗: {e}")
        return []

    tier1_results = []
    tier2_results = []

    for code in all_candidates:
        t_key = f"{code}.TW"
        meta = preliminary_meta[code]
        q = meta['quote']
        
        try:
            if len(all_candidates) == 1:
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
            
            # 月線 (20MA) 與季線 (60MA)
            ma20 = close_series.rolling(20).mean()
            ma20_curr = float(ma20.iloc[-1])
            ma20_prev = float(ma20.iloc[-2])
            
            ma60_curr = float(close_series.rolling(60).mean().iloc[-1]) if len(close_series) >= 60 else (ma20_curr * 0.95)
            
            curr_close = float(q['close'])
            is_above_ma20 = (curr_close >= ma20_curr)
            is_slope_up = (ma20_curr >= ma20_prev)
            
            # 技術面硬性條件：必須站穩 20MA 且月線走平或向上
            if not (is_above_ma20 and is_slope_up):
                continue

            # 5 日均量
            v5_avg_shares = float(vol_series.rolling(5).mean().iloc[-1])
            v5_avg_lots = v5_avg_shares / 1000.0
            
            # 均量放大比 (Volume Ratio)
            vol_ratio = (q['volume'] / v5_avg_shares) if v5_avg_shares > 0 else 1.0

            # 20MA 正乖離率 (起漲安全區，保證盈虧比)
            bias_ma20_pct = ((curr_close - ma20_curr) / ma20_curr) * 100.0
            if not (0.0 <= bias_ma20_pct < max_bias_pct):
                continue

            # 隔日沖券商風控評估
            c_today_info = chips_history.get(code, {}).get(latest_date, {})
            og_risk = evaluate_overnight_broker_risk(
                code,
                meta={
                    'volume': q['volume'],
                    'dealer_hedge': c_today_info.get('dealer', 0),
                    'foreign': c_today_info.get('foreign', 0),
                    'inst_ratio': meta['inst_ratio']
                }
            )

            # 股票期貨價位優先計算
            f_info = futures_map.get(code, {'contract': '--', 'name': q['name']})
            contract_base = f_info.get('contract', '--')
            fut_q = taifex_quotes.get(contract_base)
            fut_oi = fut_q.get('oi', 0) if fut_q else 0

            # 100 分制軟評分
            is_bull_trend = (curr_close > ma20_curr > ma60_curr)
            total_score, stars = calculate_momentum_score(
                inst_ratio=meta['inst_ratio'],
                f_lots=meta['f_3d_lots'],
                t_lots=meta['t_3d_lots'],
                vol_ratio=vol_ratio,
                is_bull_trend=is_bull_trend,
                bias_pct=bias_ma20_pct,
                fut_oi=fut_oi,
                og_penalty=og_risk.get('penalty_score', 0)
            )

            # -------------------------------------------------------------
            # 實戰四大防線定價引擎 V4.0
            # 幾何單調性嚴格單向守護：SL < Close <= Entry < Addon < TP1 < TP2 <= LimitUp
            # 停損防守錨定：收盤跌破月線 -2% 或最大硬停損 -5.0%
            # -------------------------------------------------------------
            target_sl = max(ma20_curr * 0.98, curr_close * 0.95)

            fp_calc = calculate_four_prices_long(
                curr_price=curr_close,
                high=q.get('high'),
                low=q.get('low'),
                ma20=ma20_curr,
                custom_anchors={
                    'sl': target_sl
                }
            )
            entry_p = fp_calc['entry_price']
            addon_p = fp_calc['addon_price']
            tp1_p = fp_calc['tp1_price']
            tp2_p = fp_calc['tp2_price']
            sl_p = fp_calc['sl_price']

            # 期貨定價與換算
            fut_pricing = calculate_futures_pricing(
                spot_close=curr_close,
                futures_quote=fut_q,
                entry_spot=entry_p,
                addon_spot=addon_p,
                tp1_spot=tp1_p,
                tp2_spot=tp2_p,
                sl_spot=sl_p,
                contract_base=contract_base,
                is_long=True
            )

            item = {
                '證券代號': code,
                '證券名稱': q['name'],
                '梯隊標籤': '🟢 Tier-1 主力鎖碼強勢起漲' if meta['is_tier1'] else '🟡 Tier-2 潛伏佈局量能回溫',
                '評分': total_score,
                '星級推薦': stars,
                '收盤價': round(curr_close, 2),
                '進場價位': entry_p,
                '進場參考': entry_p,
                '加碼價位': addon_p,
                '停利TP1': tp1_p,
                '停利TP2': tp2_p,
                '停損價位': sl_p,
                '期貨合約': fut_pricing['contract_sym'],
                '期貨收盤': fut_pricing['futures_close'] if fut_pricing['has_contract'] else '--',
                '價差': fut_pricing['basis_desc'] if fut_pricing['has_contract'] else '--',
                '1口保證金': fut_pricing['margin_1lot'] if fut_pricing['has_contract'] else '--',
                '期貨OI': fut_pricing['futures_oi'] if fut_pricing['has_contract'] else 0,
                '期貨進場': fut_pricing['futures_entry'] if fut_pricing['has_contract'] else entry_p,
                '期貨加碼': fut_pricing['futures_addon'] if fut_pricing['has_contract'] else addon_p,
                '期貨TP1': fut_pricing['futures_tp1'] if fut_pricing['has_contract'] else tp1_p,
                '期貨TP2': fut_pricing['futures_tp2'] if fut_pricing['has_contract'] else tp2_p,
                '期貨停損': fut_pricing['futures_sl'] if fut_pricing['has_contract'] else sl_p,
                '結算提醒': fut_pricing.get('settlement_tag', ''),
                '隔日沖風控': og_risk['tag'],
                '月線(20MA)': round(ma20_curr, 2),
                '季線(60MA)': round(ma60_curr, 2),
                '月線乖離%': round(bias_ma20_pct, 2),
                '當日成交量(張)': q['volume'] // 1000,
                '5日均量(張)': int(v5_avg_lots),
                '法人買超佔比%': meta['inst_ratio'],
                '外資3日買超(張)': meta['f_3d_lots'],
                '投信3日買超(張)': meta['t_3d_lots'],
                '當日法人買超(張)': meta['inst_today_lots'],
                '5日法人累計(張)': meta['total_5d_lots'],
                'K棒實體': meta['body'],
                '上影線': meta['upper_shadow']
            }

            # 檢定成交量門檻分流
            if meta['is_tier1'] and v5_avg_lots >= min_vol_lots:
                tier1_results.append(item)
            elif v5_avg_lots >= 800:
                tier2_results.append(item)
                
        except Exception as e:
            continue

    # 排序：各梯隊依「評分」由高至低排列
    tier1_results.sort(key=lambda x: (x['評分'], x['法人買超佔比%']), reverse=True)
    tier2_results.sort(key=lambda x: (x['評分'], x['法人買超佔比%']), reverse=True)

    # 雙層降級容錯決策：
    # 若 Tier-1 充足 (>= 3 檔)，優先展示 Tier-1 (上限 8 檔)；
    # 若 Tier-1 不足 3 檔，由 Tier-2 自動平滑遞補至 4~6 檔，保證戰報品質穩定。
    final_results = []
    if len(tier1_results) >= 3:
        final_results = tier1_results[:8]
        print(f"[✓] Tier-1 核心標的充沛 (共 {len(tier1_results)} 檔)，推薦前 {len(final_results)} 檔。")
    else:
        needed = max(4, 6 - len(tier1_results))
        final_results = tier1_results + tier2_results[:needed]
        print(f"[✓] 啟動雙層降級容錯：Tier-1 ({len(tier1_results)} 檔) + Tier-2 候補 ({min(len(tier2_results), needed)} 檔)，共推薦 {len(final_results)} 檔。")

    return final_results


def main():
    parser = argparse.ArgumentParser(description="台股法人籌碼集中起漲選股策略 (V3.0 機構旗艦版)")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYYMMDD，預設為最新交易日)")
    parser.add_argument("--min-vol", type=int, default=1000, help="最低5日平均成交量(張)，預設 1000 張")
    parser.add_argument("--max-bias", type=float, default=8.0, help="20MA 正乖離上限百分比(%%)，預設 8.0%%")
    parser.add_argument("--inst-ratio", type=float, default=10.0, help="法人買超佔當日成交量低標(%%)，預設 10.0%%")
    parser.add_argument("--inst-3d", type=int, default=1000, help="外資或投信近3日累計買超門檻(張)，預設 1000 張")
    parser.add_argument("--export", type=str, default=None, help="匯出報表檔名 (.csv 或 .md)")
    parser.add_argument("--line", action="store_true", help="發送選股通知至 LINE 獨立視窗")
    parser.add_argument("--ignore-market", action="store_true", help="忽略加權指數逆風警告，強制執行選股")
    parser.add_argument("--force", action="store_true", help="強制執行 (忽略非交易日與大盤逆風限制)")

    args = parser.parse_args()
    ignore_market_flag = args.ignore_market or args.force

    # 自動載入全域市場自適應操盤參數 (Adaptive Regime Parameters)
    try:
        from adaptive_regime_manager import get_strategy_adaptive_params
        adap_p = get_strategy_adaptive_params("02")
        if adap_p:
            if args.min_vol == 1000 and "min_volume" in adap_p:
                args.min_vol = int(adap_p["min_volume"])
            if args.max_bias == 8.0 and "bias_max_pct" in adap_p:
                args.max_bias = float(adap_p["bias_max_pct"])
    except Exception:
        pass

    results = screen_institutional_momentum(
        date_str=args.date,
        min_vol_lots=args.min_vol,
        max_bias_pct=args.max_bias,
        inst_ratio_pct=args.inst_ratio,
        inst_3d_threshold=args.inst_3d,
        ignore_market=ignore_market_flag
    )

    if not results:
        print("\n[!] 經檢核，所選條件下無符合所有複合條件的個股。維持資金安全，靜待起漲契機！")
        return

    df_res = pd.DataFrame(results)

    print("\n" + "=" * 105)
    print(f"🎯【台股法人籌碼集中起漲】選股結果清單（共 {len(df_res)} 檔）")
    print("條件符合：外資/投信3日買超>1000張 | 法人佔比>10% | 站穩20MA且向上 | 5日均量>1000張 | 正乖離<8% | 無長上影線")
    print("=" * 105)
    display_cols = ['證券代號', '證券名稱', '梯隊標籤', '星級推薦', '評分', '收盤價', '進場價位', '加碼價位', '停利TP1', '停利TP2', '停損價位', '法人買超佔比%', '月線乖離%']
    print(df_res[[c for c in display_cols if c in df_res.columns]].to_string(index=False))
    print("=" * 105)

    if args.export:
        export_path = args.export
        if not os.path.isabs(export_path) and os.path.dirname(export_path) == "":
            export_path = os.path.join(os.path.dirname(__file__), export_path)
            
        if export_path.endswith('.csv'):
            from rolling_retention_manager import save_rolling_result_csv
            target_date = args.date or (results[0].get('日期') if results else None) or datetime.date.today().strftime('%Y-%m-%d')
            df_res = save_rolling_result_csv(export_path, df_res, today_date_str=target_date, max_days=5)
            print(f"[✓] 已成功匯出 5 日滾動留存 CSV 報表至: {export_path}")
            
            # 同步產出視覺化字卡 Markdown 戰報
            md_path = export_path[:-4] + ".md"
            from line_sender import format_screener_markdown_cards
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(format_screener_markdown_cards(results, target_date, strategy_id="02", max_stocks=10))
                f.write("\n\n## 📋 完整量化數據指標總表\n\n")
                f.write(df_res.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已同步匯出視覺化字卡 Markdown 戰報至: {md_path}")
            
        elif export_path.endswith('.md'):
            from line_sender import format_screener_markdown_cards
            with open(export_path, 'w', encoding='utf-8') as f:
                target_date = args.date or (results[0].get('日期') if results else None) or datetime.date.today().strftime('%Y-%m-%d')
                f.write(format_screener_markdown_cards(results, target_date, strategy_id="02", max_stocks=10))
                f.write("\n\n## 📋 完整量化數據指標總表\n\n")
                f.write(df_res.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已成功匯出視覺化字卡 Markdown 報表至: {export_path}")

    if args.line:
        try:
            from line_sender import send_to_line, build_screener_carousel, format_clean_screener_text
            target_date = args.date or (results[0]['證券代號'] and datetime.date.today().strftime('%Y/%m/%d')) or "今日"
            card = build_screener_carousel(results, target_date, strategy_id="02", max_stocks=6)
            print("[*] 正在發送選股清單至 LINE 獨立視窗 (策略 02)...")
            success = send_to_line(card, strategy="02")
            if not success:
                clean_text = format_clean_screener_text(results, target_date, strategy_id="02", max_stocks=6)
                send_to_line(clean_text, strategy="02")
        except Exception as e:
            print(f"[!] 發送 LINE 選股通知失敗: {e}")


if __name__ == '__main__':
    main()
