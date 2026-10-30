#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 03：法人出貨破線做空與股票期貨避險選股器 V3.0 全域旗艦版
(Institutional Dumping & Breakdown Short Screener V3.0)

【核心做空量化策略邏輯 (4+1 核心維度鐵律與實戰四大防線定價引擎 V4.0)】
一、母池與流動性防守 (Pool & Liquidity Defense)：
  - 限定臺灣期貨交易所 (TAIFEX) 252 檔個股期貨標的 (零券源限制、零借券費、資金效率高 7 倍、期交稅僅 0.002%)。
  - 嚴格排除 ETF (如 0050、0056) 與非個股標的，杜絕乘數與保證金計算失真。
  - 近 5 日平均成交量 >= 1,000 張，排除流動性匱乏標的。

二、趨勢位階線 (Trend Breakdown Alignment)：
  - 收盤價跌破 20 日均線 (月線)，且月線斜率向下蓋頭反壓 (MA20_curr < MA20_prev)。
  - 現價位於月線之下，若同時跌破 60 日季線則空方排列確立，下殺動能最強。

三、籌碼動能出貨面 (Institutional Heavy Dumping)：
  - 外資或投信近 3 個交易日累計賣超 >= 1,000 張 (<-1,000,000 股)。
  - 當日三大法人合計賣超佔當日成交量比例 >= 12% (主力集中重壓砸盤提款)。
  - 近 5 日法人累計呈現淨賣超 (籌碼渙散浮額流出)。

四、型態防守面 (Candlestick Pattern Defense)：
  - 當日 K 棒無長下影線 (下影線長度 < 實體長度一半，排除低檔主力強撐誘空)。
  - 實體收黑或破支撐平台。

五、空間防追空起跌甜蜜點 (Sweet-Spot Breakdown Window)：
  - 月線負乖離率介於 0% 到 -5.0% 之間 (-5.0% <= Bias <= 0.0%)。
  - 剛破線起跌區，起跌空間最大，徹底杜絕跌深追空被技術性反彈軋空！

六、實戰四大防線標準定價引擎 V4.0 (四防線合規取整與期貨價位優先)：
  - 調用根目錄 four_price_engine.py 之 calculate_four_prices_short()。
  - 臺灣證交所法定 6 階升降檔位方向性合規取整 (空單停損向上取整防毛刺、停利向上取整以利買進回補、進場加空向下取整)。
  - 幾何單調性嚴格單向守護：LimitDown <= TP2 <= TP1 < Addon < Entry <= Close < SL <= LimitUp。
  - 現貨合規點位無縫映射至期貨近月主力合約價位，展示一口保證金試算 (約現貨 2 張市價之 13.5%)。

七、期貨未平倉量 (OI) 深度流動性加分與 100 分制軟評分矩陣：
  - 納入期貨未平倉口數 (OI) 深度評估 (>=10,000口 +20分、>=5,000口 +16分、>=2,000口 +12分、>=1,000口 +8分)。
  - 隔日沖分點集中度評估 (algo_guards.py) 扣分與警示。

八、期貨結算倒數 5 日開立次月新倉標準 (Futures Rollover Standard)：
  - 依期交所第三個星期三法定結算日，在距到期日 <= 5 個交易日時自動啟動開立次月新倉提醒。
"""

import sys
import os
import re
import argparse
import datetime
import json
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np
import yfinance as yf

# 加入專案根目錄至 sys.path 以便共用底層模組
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from algo_guards import evaluate_overnight_broker_risk
from futures_helper import (
    load_stock_futures_pool,
    fetch_taifex_futures_quotes,
    calculate_futures_pricing,
    get_futures_settlement_info
)
from four_price_engine import (
    calculate_four_prices_short,
    normalize_to_tick,
    get_twse_tick_size,
    get_limit_prices
)
from adaptive_regime_manager import load_adaptive_config
from line_sender import (
    send_to_line,
    build_screener_carousel,
    format_clean_screener_text,
    format_screener_markdown_cards
)
from rolling_retention_manager import save_rolling_result_csv

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
                        # 排除 ETF (00開頭) 與非 4 碼純數字個股
                        if len(code) == 4 and code.isdigit() and not code.startswith('00'):
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
                        # 排除 ETF (00開頭) 與非 4 碼純數字個股
                        if len(code) == 4 and code.isdigit() and not code.startswith('00'):
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


def get_market_regime_status() -> Tuple[bool, str, Dict[str, Any]]:
    """
    連線全域自適應市場位階引擎，檢查大盤宏觀環境
    回傳: (is_bear_timing: bool, status_msg: str, regime_params: dict)
    """
    try:
        regime_config = load_adaptive_config()
        m_info = regime_config.get("market_regime", {})
        regime_key = m_info.get("key", "SIDEWAYS_CONSOLIDATION")
        label = m_info.get("label", "🟡【箱型震盪整理】")
        short_params = regime_config.get("short_strategies", {})

        is_bear = (regime_key in ["BEAR_DEFENSIVE", "SIDEWAYS_CONSOLIDATION"])
        
        if regime_key == "BEAR_DEFENSIVE":
            status_text = f"🟣【大盤避險紅綠燈：空頭修正模式啟動】{label}，做空勝率與期望值處於黃金甜蜜期！"
        elif regime_key == "SIDEWAYS_CONSOLIDATION":
            status_text = f"🟡【大盤避險紅綠燈：箱型震盪輪動】{label}，個股走勢分化，放空聚焦於弱勢破線股。"
        elif regime_key == "BULL_OVERHEAT":
            status_text = f"🔴【大盤避險紅綠燈：高檔過熱警戒】{label}，慎防波段獲利了結與長黑甩轎，避險空單可小量試單。"
        else:
            status_text = f"⚠️【大盤避險紅綠燈：多頭強勢攻堅】{label}，多頭均線向上助漲，做空風險極高，僅供持股避險對沖！"

        return is_bear, status_text, short_params
    except Exception as e:
        # Fallback 到 YF 0050 均線偵測
        try:
            df_m = yf.download('0050.TW', period='2mo', progress=False)
            close_s = df_m['Close']['0050.TW'] if isinstance(df_m.columns, pd.MultiIndex) else df_m['Close']
            close_s = close_s.dropna()
            ma20 = close_s.rolling(20).mean().dropna()
            curr_c = float(close_s.iloc[-1])
            curr_ma = float(ma20.iloc[-1])
            prev_ma = float(ma20.iloc[-2])
            is_below = curr_c <= curr_ma
            is_slope_down = curr_ma < prev_ma
            is_bear = is_below or is_slope_down
            status_text = f"[*] 大盤 (0050) 現價 {curr_c:.2f} | 月線 {curr_ma:.2f} ({'破月線/下彎' if is_bear else '站上月線'})"
            return is_bear, status_text, {}
        except Exception:
            return True, "⚠️ 無法取得大盤數據，預設允許常規避險監控", {}


def calculate_short_score(meta: Dict[str, Any], curr_close: float, ma20: float, 
                          ma60: float, bias_pct: float, fut_oi: int, 
                          og_risk: Dict[str, Any]) -> Tuple[int, str]:
    """
    計算第二階段 100 分制做空軟評分矩陣 (Short Scoring Matrix)
    綜合考量：法人砸盤力道 (30分) + 空頭均線蓋頭力道 (20分) + 期貨未平倉量OI (20分) +
             起跌甜蜜負乖離 (15分) + 量能結構 (15分) - 隔日沖出貨反制扣分
    """
    # 1. 法人賣超佔比 (佔比 12% 起跳，每增加 3% 加 3 分，最高 20 分)
    inst_ratio = meta['inst_sell_ratio']
    score_ratio = min(20, int(10 + max(0, inst_ratio - 12.0) * 1.0))

    # 2. 3日外資/投信賣超總量 (1,000張起跳，每增 2,000 張加 2 分，最高 10 分)
    max_3d_sell = max(abs(meta['f_3d_lots']), abs(meta['t_3d_lots']))
    score_vol_sell = min(10, int(5 + max(0, max_3d_sell - 1000) / 2000 * 2))
    score_chips = score_ratio + score_vol_sell

    # 3. 均線破線下彎蓋頭力道 (現價 < 20MA < 60MA 完整空頭加 20 分，否則 14 分)
    if curr_close < ma20 and ma20 < ma60:
        score_trend = 20
    else:
        score_trend = 14

    # 4. 起跌甜蜜負乖離度 (-1.0% ~ -3.5% 最優給 15 分，-3.5% ~ -5.0% 給 11 分，更深給 7 分)
    if -3.5 <= bias_pct <= -1.0:
        score_bias = 15
    elif -5.0 <= bias_pct < -3.5:
        score_bias = 11
    else:
        score_bias = 7

    # 5. 期貨未平倉量 (OI) 深度加分 (期貨流動性防守，最高 20 分)
    #    未平倉量大代表法人長期重倉參與，買賣價差緊密，不易被劇烈滑價！
    if fut_oi >= 10000:
        score_oi = 20  # 萬口以上超旗艦主流 (如鴻海期、聯發科期、台積電期)
    elif fut_oi >= 5000:
        score_oi = 16  # 5,000口以上主力強勢標的
    elif fut_oi >= 2000:
        score_oi = 12  # 2,000口以上活躍波段股期
    elif fut_oi >= 1000:
        score_oi = 8   # 1,000口合格流動性
    else:
        score_oi = 4   # 冷門小口數股期，扣減優先級

    # 6. 量能出貨結構 (當日成交量 / 5日均量，放量跌破給最高 15 分)
    v_ratio = meta['vol_ratio']
    score_volume = min(15, int(v_ratio * 10))

    # 7. 隔日沖出貨反制扣分
    penalty = 0
    if og_risk.get('risk_level') == 'HIGH':
        penalty = 12
    elif og_risk.get('risk_level') == 'MEDIUM':
        penalty = 6

    raw_score = score_chips + score_trend + score_bias + score_oi + score_volume - penalty
    total_score = max(50, min(100, raw_score))

    if total_score >= 90:
        stars = '★★★★★'
    elif total_score >= 80:
        stars = '★★★★☆'
    elif total_score >= 70:
        stars = '★★★☆☆'
    else:
        stars = '★★☆☆☆'

    return total_score, stars


def screen_short_stocks(date_str=None, min_vol_lots=1000, 
                        max_neg_bias_pct=-5.0, inst_sell_ratio_pct=12.0,
                        inst_3d_threshold=1000, futures_only=True,
                        hedge_only=False):
    """
    執行完整台股法人破線做空與個股期貨避險量化篩選 (V3.0 全域旗艦版)
    支援雙層降級容錯架構 (Tier-1 嚴格 4+1 維度 / Tier-2 破線候補)
    """
    is_bear, regime_msg, short_params = get_market_regime_status()
    print(f"\n[*] {regime_msg}")
    if hedge_only and not is_bear:
        print("[-] 【嚴格避險模式已啟用】當前大盤處於多頭主升波段，自動終止新開空單！")
        return []

    # 動態繼承大盤位階之做空參數
    if short_params:
        if "min_volume" in short_params:
            min_vol_lots = max(1000, int(short_params["min_volume"]))
        if "bias_min_pct" in short_params:
            max_neg_bias_pct = float(short_params["bias_min_pct"])

    futures_map = {}
    taifex_quotes = {}
    if futures_only:
        futures_map = load_stock_futures_pool()
        print(f"[*] 【個股期貨優先模式】已載入期交所 {len(futures_map)} 檔股票期貨標的，完全排除無券源小型股！")
        taifex_quotes = fetch_taifex_futures_quotes()
        print(f"[*] 【期交所即時/盤後行情】已同步 {len(taifex_quotes)} 檔近月主力合約報價！")

    settle_info = get_futures_settlement_info()
    if settle_info['is_near_settlement']:
        print(f"\n{settle_info['banner_text']}\n")

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

    # 初篩候選人
    tier1_candidates = []
    tier2_candidates = []
    preliminary_meta = {}

    for code, q in quotes.items():
        # 嚴格排除 ETF 與非 4 碼個股
        if code.startswith('00') or len(code) != 4 or not code.isdigit():
            continue

        # 【關鍵過濾】只針對具備股票期貨之標的進行做空推薦
        if futures_only and code not in futures_map:
            continue

        if code not in chips_history or latest_date not in chips_history[code]:
            continue
            
        c_today = chips_history[code][latest_date]
        
        # 近 3 日外資累計與投信累計賣超 (股數)
        f_3d_shares = sum(chips_history[code][d]['foreign'] for d in dates_3d if d in chips_history[code])
        t_3d_shares = sum(chips_history[code][d]['trust'] for d in dates_3d if d in chips_history[code])
        
        inst_today_shares = c_today['total']
        if q['volume'] <= 0 or inst_today_shares >= 0:
            continue
            
        inst_sell_ratio = (abs(inst_today_shares) / q['volume']) * 100.0

        # K 棒形態防守：當日無長下影線 (排除低檔主力強撐)
        body = abs(q['close'] - q['open'])
        lower_shadow = min(q['open'], q['close']) - q['low']
        
        if body > 0.05:
            cond_shadow = (lower_shadow < 0.5 * body)
        else:
            cond_shadow = (lower_shadow <= (q['close'] * 0.005))
            
        if not cond_shadow:
            continue

        total_5d_shares = sum(chips_history[code][d]['total'] for d in dates_5d if d in chips_history[code])

        meta_item = {
            'quote': q,
            'f_3d_lots': f_3d_shares // 1000,
            't_3d_lots': t_3d_shares // 1000,
            'inst_today_lots': inst_today_shares // 1000,
            'total_5d_lots': total_5d_shares // 1000,
            'inst_sell_ratio': round(inst_sell_ratio, 1),
            'body': round(body, 2),
            'lower_shadow': round(lower_shadow, 2)
        }
        preliminary_meta[code] = meta_item

        # Tier-1 嚴格門檻：3日賣超 >= 1000張 且 當日法人賣超佔比 >= 12%
        cond_tier1 = ((f_3d_shares <= -inst_3d_threshold * 1000) or (t_3d_shares <= -inst_3d_threshold * 1000)) and (inst_sell_ratio >= inst_sell_ratio_pct)
        if cond_tier1:
            tier1_candidates.append(code)
        else:
            # Tier-2 降級容錯門檻：3日賣超 >= 600張 且 當日法人賣超佔比 >= 8%
            cond_tier2 = ((f_3d_shares <= -600 * 1000) or (t_3d_shares <= -600 * 1000)) and (inst_sell_ratio >= 8.0)
            if cond_tier2:
                tier2_candidates.append(code)

    print(f"[*] 通過籌碼初篩：Tier-1 嚴格主力砸盤共 {len(tier1_candidates)} 檔，Tier-2 備選共 {len(tier2_candidates)} 檔。")
    all_candidates = tier1_candidates.copy()
    # 若 Tier-1 小於 4 檔，啟用 Tier-2 候補
    if len(all_candidates) < 4:
        all_candidates.extend(tier2_candidates)

    if not all_candidates:
        print("[-] 今日無符合籌碼出貨與形態門檻之標的。")
        return []

    print("[*] 執行第二階段空頭技術面濾網（下載日K計算月線下彎、季線位階與負乖離率）...")
    tickers = [f"{c}.TW" for c in all_candidates]
    try:
        hist_data = yf.download(tickers, period='4mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 歷史數據下載失敗: {e}")
        return []

    results = []

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
            
            ma20 = close_series.rolling(20).mean()
            ma20_curr = float(ma20.iloc[-1])
            ma20_prev = float(ma20.iloc[-2])
            
            ma60_curr = float(close_series.rolling(60).mean().iloc[-1]) if len(close_series) >= 60 else ma20_curr * 1.02
            
            curr_close = float(q['close'])
            is_below_ma20 = (curr_close <= ma20_curr)
            is_slope_down = (ma20_curr <= ma20_prev)
            
            # 做空趨勢條件: 收盤價跌破月線且月線下彎助跌
            if not (is_below_ma20 and is_slope_down):
                continue

            # 流動性條件: 5日均量 >= 1,000 張
            v5_avg_shares = float(vol_series.rolling(5).mean().iloc[-1])
            v5_avg_lots = v5_avg_shares / 1000.0
            if v5_avg_lots < min_vol_lots:
                continue

            vol_ratio = q['volume'] / v5_avg_shares if v5_avg_shares > 0 else 1.0
            meta['vol_ratio'] = vol_ratio

            # 空間防追空條件 (起跌甜蜜點):
            bias_ma20_pct = ((curr_close - ma20_curr) / ma20_curr) * 100.0
            
            is_tier1 = (code in tier1_candidates)
            if is_tier1:
                if not (max_neg_bias_pct <= bias_ma20_pct <= 0.0):
                    continue
                tier_badge = "🟢【Tier-1 核心起跌】"
            else:
                # Tier-2 負乖離容許放寬至 -8.0%
                if not (-8.0 <= bias_ma20_pct <= 0.0):
                    continue
                tier_badge = "🟡【Tier-2 破線候補】"

            # -------------------------------------------------------------
            # 實戰四大防線定價引擎 V4.0 全域強制規範 (Four-Price Engine Short)
            # -------------------------------------------------------------
            fp_calc = calculate_four_prices_short(
                curr_price=curr_close,
                prev_close=curr_close,
                high=q['high'],
                low=q['low'],
                ma20=ma20_curr,
                regime_params=short_params
            )

            # -------------------------------------------------------------
            # 股票期貨價位優先計算與四大防線換算 (Futures Pricing Priority)
            # -------------------------------------------------------------
            f_info = futures_map.get(code, {'contract': '--', 'name': q['name']})
            contract_base = f_info.get('contract', '--')
            fut_q = taifex_quotes.get(contract_base)

            # 調用共用期貨定價引擎，計算以期貨為優先的報價與四防線
            fut_pricing = calculate_futures_pricing(
                spot_close=curr_close,
                futures_quote=fut_q,
                entry_spot=fp_calc['entry_price'],
                addon_spot=fp_calc['addon_price'],
                tp1_spot=fp_calc['tp1_price'],
                tp2_spot=fp_calc['tp2_price'],
                sl_spot=fp_calc['sl_price'],
                contract_base=contract_base,
                is_long=False
            )

            # 期貨合約價位方向性檔位合規取整 (Tick Normalization)
            fut_entry = normalize_to_tick(fut_pricing['futures_entry'], direction='down')
            fut_addon = normalize_to_tick(fut_pricing['futures_addon'], direction='down')
            fut_tp1 = normalize_to_tick(fut_pricing['futures_tp1'], direction='up')
            fut_tp2 = normalize_to_tick(fut_pricing['futures_tp2'], direction='up')
            fut_sl = normalize_to_tick(fut_pricing['futures_sl'], direction='up')

            # 次月期貨四大防線檔位合規取整
            next_entry = normalize_to_tick(fut_pricing['next_entry'], direction='down') if fut_pricing['next_entry'] else None
            next_addon = normalize_to_tick(fut_pricing['next_addon'], direction='down') if fut_pricing['next_addon'] else None
            next_tp1 = normalize_to_tick(fut_pricing['next_tp1'], direction='up') if fut_pricing['next_tp1'] else None
            next_tp2 = normalize_to_tick(fut_pricing['next_tp2'], direction='up') if fut_pricing['next_tp2'] else None
            next_sl = normalize_to_tick(fut_pricing['next_sl'], direction='up') if fut_pricing['next_sl'] else None

            # 隔日沖券商出貨風控評估
            og_risk = evaluate_overnight_broker_risk(
                code,
                meta={
                    'volume': q['volume'],
                    'dealer_hedge': meta['inst_today_lots'] * 1000,
                    'foreign': meta['f_3d_lots'] * 1000,
                    'inst_ratio': meta['inst_sell_ratio']
                }
            )

            # 計算 100 分制軟評分與星級
            score, stars = calculate_short_score(
                meta=meta,
                curr_close=curr_close,
                ma20=ma20_curr,
                ma60=ma60_curr,
                bias_pct=bias_ma20_pct,
                fut_oi=fut_pricing['futures_oi'],
                og_risk=og_risk
            )

            results.append({
                '證券代號': code,
                '證券名稱': q['name'],
                '期貨契約': fut_pricing['contract_sym'],
                '期貨月份': fut_pricing['month'],
                '收盤價': round(curr_close, 2),
                '期貨收盤價': fut_pricing['futures_close'],
                '價差(Basis)': fut_pricing['basis_desc'],
                '進場價位': fut_entry,
                '空單進場': fut_entry,
                '加碼價位': fut_addon,
                '加空價位': fut_addon,
                '停利TP1': fut_tp1,
                '停利TP2': fut_tp2,
                '停損價位': fut_sl,
                '停損回補': fut_sl,
                '現貨進場': fp_calc['entry_price'],
                '現貨加空': fp_calc['addon_price'],
                '現貨TP1': fp_calc['tp1_price'],
                '現貨TP2': fp_calc['tp2_price'],
                '現貨停損': fp_calc['sl_price'],
                '漲停價': fp_calc['limit_up'],
                '跌停價': fp_calc['limit_down'],
                '盈虧比': fp_calc['risk_reward_ratio'],
                '隔日沖風控': og_risk['tag'],
                '1口保證金(約)': fut_pricing['margin_1lot'],
                '期貨成交量': fut_pricing['futures_vol'],
                '期貨未平倉(OI)': fut_pricing['futures_oi'],
                '綜合評分': score,
                '星級推薦': stars,
                'Tier分流': tier_badge,
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
                '下影線': meta['lower_shadow'],
                '結算倒數': fut_pricing['settlement_tag'],
                '次月合約': fut_pricing['next_contract_sym'],
                '次月月份': fut_pricing['next_month'],
                '次月收盤': fut_pricing['next_close'],
                '次月進場參考': next_entry,
                '次月加空參考': next_addon,
                '次月TP1參考': next_tp1,
                '次月TP2參考': next_tp2,
                '次月停損參考': next_sl,
                '次月保證金': fut_pricing['next_margin_1lot'],
                '結算操盤建議': fut_pricing['settlement_reminder'],
                '操盤錦囊': str({
                    'entry_desc': f"建議放空上限 {fut_entry} 元 (現價 {curr_close} 元，反彈不過此上限為黃金空點)",
                    'addon_desc': f"摜破 {fut_addon} 元確認弱勢續殺，啟動第 2 階段順勢加空",
                    'tp1_desc': f"回補目標一 {fut_tp1} 元分批回補 50% 鎖定利潤",
                    'tp2_desc': f"回補目標二 {fut_tp2} 元主跌段等幅滿足全數回補",
                    'sl_desc': f"突破 {fut_sl} 元無條件空單停損回補，嚴控風險"
                })
            })
            
        except Exception as e:
            continue

    return results


def main():
    parser = argparse.ArgumentParser(description="台股策略 03：法人出貨破線做空與個股期貨避險選股器 V3.0 全域旗艦版")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYYMMDD，預設為最新交易日)")
    parser.add_argument("--min-vol", type=int, default=1000, help="最低5日平均成交量(張)，預設 1000 張")
    parser.add_argument("--max-neg-bias", type=float, default=-5.0, help="20MA 負乖離率下限百分比(%%)，預設 -5.0%% (起跌甜蜜點)")
    parser.add_argument("--sell-ratio", type=float, default=12.0, help="法人賣超佔成交量低標(%%)，預設 12.0%% (集中重壓砸盤)")
    parser.add_argument("--sell-3d", type=int, default=1000, help="外資或投信近3日累計賣超門檻(張)，預設 1000 張")
    parser.add_argument("--hedge-only", action="store_true", help="大盤避險嚴格模式：大盤多頭強勢期自動攔截開倉")
    parser.add_argument("--no-futures-filter", action="store_true", help="關閉個股期貨限制，允許篩選全市場普通股")
    parser.add_argument("--export", type=str, default=None, help="匯出報表檔名 (.csv 或 .md)")
    parser.add_argument("--line", action="store_true", help="發送選股通知至 LINE 策略 03 獨立視窗")

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
    # 優先依「Tier分流」、「綜合評分」與「期貨未平倉(OI)」降序排列
    df_res = df_res.sort_values(by=["Tier分流", "綜合評分", "期貨未平倉(OI)"], ascending=[True, False, False]).reset_index(drop=True)

    settle_info = get_futures_settlement_info()

    print("\n" + "="*120)
    print(f"🎯【台股法人出貨破線做空】選股結果清單（共 {len(df_res)} 檔）- ⚡ 全面升級【實戰四大防線 V4.0 × 股票期貨價位優先】！")
    print(f"期貨結算週期戰情：{settle_info['badge_text']} (法定結算日: {settle_info['front_settle_short']})")
    print("四大防線價位已依法定升降檔位方向性合規取整；現貨點位同步於報表中對照備查")
    print("="*120)
    
    # 終端印出重點摘要
    cols_show = ['證券代號', '證券名稱', '期貨契約', '期貨月份', '收盤價', '期貨收盤價', '價差(Basis)', 
                 '空單進場', '加空價位', '停利TP1', '停利TP2', '停損回補', '1口保證金(約)', '期貨未平倉(OI)', '綜合評分', '星級推薦']
    print(df_res[cols_show].to_string(index=False))
    print("="*120)

    if args.export:
        export_path = args.export
        if not os.path.isabs(export_path) and os.path.dirname(export_path) == "":
            export_path = os.path.join(os.path.dirname(__file__), export_path)
        if export_path.endswith('.csv'):
            target_date = args.date or (results[0].get('日期') if results else None) or datetime.date.today().strftime('%Y-%m-%d')
            df_res = save_rolling_result_csv(export_path, df_res, today_date_str=target_date, max_days=5)
            print(f"[✓] 已成功匯出滾動 CSV 報表至: {export_path}")
            
            # 同步產出視覺化字卡 Markdown 戰報
            md_path = export_path[:-4] + ".md"
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(format_screener_markdown_cards(results, target_date, strategy_id="03", max_stocks=10))
                f.write("\n\n## 📋 完整量化數據指標總表\n\n")
                f.write(df_res.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已同步匯出視覺化字卡 Markdown 戰報至: {md_path}")
        elif export_path.endswith('.md'):
            target_date = args.date or (results[0].get('日期') if results else None) or datetime.date.today().strftime('%Y-%m-%d')
            with open(export_path, 'w', encoding='utf-8') as f:
                f.write(format_screener_markdown_cards(results, target_date, strategy_id="03", max_stocks=10))
                f.write("\n\n## 📋 完整量化數據指標總表\n\n")
                f.write(df_res.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已成功匯出視覺化字卡 Markdown 報表至: {export_path}")

    if args.line:
        try:
            target_date = args.date or (results[0]['證券代號'] and datetime.date.today().strftime('%Y/%m/%d')) or "今日"
            card = build_screener_carousel(results, target_date, strategy_id="03", max_stocks=6)
            print("[*] 正在發送做空選股清單至 LINE 策略 03 獨立視窗...")
            success = send_to_line(card, strategy="03")
            if not success:
                clean_text = format_clean_screener_text(results, target_date, strategy_id="03", max_stocks=6)
                send_to_line(clean_text, strategy="03")
        except Exception as e:
            print(f"[!] 發送 LINE 選股通知失敗: {e}")


if __name__ == '__main__':
    main()
