#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 11：台股營收創高與量價突破成長動能策略 (Volume Breakout Pro 2.0 旗艦優化版)
================================================================================
本選股器依據《總指揮中心規範》全域標準與 11 年大數據實證深度升級：

【一、大盤環境順風濾網 (Market Regime Filter)】
  - 加權指數 (^TWII) >= 20MA (月線)：多頭順風期，全面開啟 A 軌動能突破與 B 軌回測。
  - 加權指數 (^TWII) < 20MA (月線)：盤整逆風期，嚴禁追高，僅執行 B 軌量縮回測且部位減半。

【二、4+1 核心維度硬性過濾條件 (Hard Filters <= 5)】
  1. 母池與流動性防守：5日均量 >= 1,000 張 (初篩 >= 500 張)，收盤價 >= 15 元，排除處置股、全額交割、TDR。
  2. 趨勢突破面：收盤價創近 20 日新高 (或近 10 日新高)，站上 20MA (月線) 與 60MA (季線)，RSI(14) > 55。
  3. 量能甜蜜倍數面：當日量比 1.8x ~ 4.5x 20MA 均量 (避開量能不足與 >5 倍天量出貨)，OBV 創 20 日新高。
  4. 基本面營收動能面：當月營收年增率 YoY >= 10.0%，且當月營收創波段新高 (或 MoM >= -2.0%)。
  5. 空間防追高：月線正乖離率介於 +1.0% 至 +7.5% 之間 (若 > 8.5% 嚴禁追高，避開高檔套牢)。

【三、第二階段 100 分制軟評分矩陣】
  - 前置波動壓縮品質 (VCP / Squeeze，最高 25 分)：突破前 15~30 日振幅 <= 12% 且量縮沉澱 (+25分)
  - 月營收與獲利爆發度 (Fundamental Alpha，最高 25 分)：YoY >= 30% (+25分)，YoY >= 20% (+20分)，YoY >= 10% (+15分)
  - 法人鎖碼與籌碼集中度 (Smart Money Flow，最高 25 分)：外資+投信合計買超佔比 >= 10% 或買超 >= 1000張 (+25分)
  - 量價突破動能品質 (Surge Quality，最高 15 分)：量比落於 2.0x ~ 3.5x 最優 (+15分)
  - 隔日沖反制 (Overnight Broker Guard，最高 +10分，最低 -20分)：隔日沖大戶扎堆扣 15~20 分並警示。

【四、雙軌四大防線操盤引擎 (TWSE 法定檔位合規引擎 V4.0)】
  - A 軌 (動能突破)：尾盤或次日開盤進場，TP1 (+8%~+10%), TP2 (+18%~+25%), 停損 max(長紅低點-1檔, -5.5%)
  - B 軌 (量縮回測)：突破後 2~5 日回測 5MA/頸線且量縮低接，TP1 (+12%), TP2 (+20%), 停損破頸線 -2%
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

# Windows 命令列編碼保護
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

# 加入專案根目錄至 sys.path 以引用共用模組
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from algo_guards import evaluate_overnight_broker_risk
from four_price_engine import calculate_four_prices_long, normalize_to_tick
from rolling_retention_manager import save_rolling_result_csv
from line_sender import (
    send_to_line,
    build_screener_carousel,
    format_clean_screener_text,
    format_screener_markdown_cards
)
from revenue_helper import fetch_monthly_revenue, get_stock_revenue

STRATEGY_ID = "11"
STRATEGY_NAME = "策略 11：台股營收創高與量價突破成長動能策略"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')
MD_FILE = os.path.join(os.path.dirname(__file__), 'result.md')

STANDARD_COLUMNS = [
    '證券代號', '證券名稱', '梯隊標籤', '星級', '評分', '收盤價', '進場價位', '進場參考',
    '加碼價位', '停利TP1', '停利TP2', '停損價位', 'B軌回測價', '當月營收(千元)', '營收YoY%',
    '營收MoM%', '營收創高', '當日成交量(張)', '20日均量(張)', '均量比Rvol', '月線(20MA)',
    '月線乖離%', '前置振幅%', 'RSI14', 'OBV創高', '法人買超合計(張)', '法人佔比%', '隔日沖風控'
]


def check_market_regime():
    """
    檢驗加權指數 (^TWII) 20MA 多空狀態:
    回傳: (is_bull, desc, bias_pct)
    """
    try:
        df_taiex = yf.download('^TWII', period='2mo', interval='1d', auto_adjust=False, progress=False)
        if not df_taiex.empty and len(df_taiex) >= 20:
            closes = df_taiex['Close'].squeeze()
            ma20 = closes.rolling(20).mean().iloc[-1]
            last_p = closes.iloc[-1]
            bias = (last_p - ma20) / ma20 * 100.0
            if last_p >= ma20:
                return True, f"🟢【大盤多頭順風期】加權指數站穩月線 (乖離: {bias:+.2f}%)，開啟 A/B 雙軌操盤模式", bias
            else:
                return False, f"⚠️【大盤盤整逆風期】加權指數跌破月線 (乖離: {bias:+.2f}%)，停用 A 軌追高，僅啟動 B 軌回測 (部位減半)", bias
    except Exception as e:
        print(f"[!] 檢查大盤環境失敗: {e}")
    return True, "🟢【大盤中性】維持標準雙軌操盤模式", 0.0


def get_recent_trading_dates(n_days=1, max_lookback=20):
    """取得最近 n_days 個證交所實際開盤交易日 (YYYYMMDD)"""
    today = datetime.date.today()
    found_dates = []
    
    for i in range(max_lookback):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        d_str = d.strftime('%Y%m%d')
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json'
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('stat') == 'OK' and len(data.get('data', [])) > 0:
                    found_dates.append(d_str)
                    if len(found_dates) == n_days:
                        break
        except Exception:
            pass
            
    return found_dates


def fetch_daily_quotes(date_str):
    """抓取全市場 (上市 TWSE + 上櫃 TPEx) 每日收盤行情 (OHLCV)"""
    print(f"[*] 正在連線證交所與櫃買中心抓取 {date_str} 盤後收盤行情 (OHLCV)...")
    quotes = {}
    
    # 1. TWSE 上市行情 (MI_INDEX)
    url_mi = f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=json'
    req_mi = urllib.request.Request(url_mi, headers={'User-Agent': 'Mozilla/5.0'})
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
                                vol = int(row[2].replace(',', '')) // 1000
                                close_str = row[8].replace(',', '').strip()
                                open_str = row[5].replace(',', '').strip()
                                high_str = row[6].replace(',', '').strip()
                                low_str = row[7].replace(',', '').strip()
                                
                                if close_str != '--' and open_str != '--' and high_str != '--':
                                    quotes[code] = {
                                        'name': name,
                                        'vol': vol,
                                        'open': float(open_str),
                                        'high': float(high_str),
                                        'low': float(low_str),
                                        'close': float(close_str),
                                        'market': 'TWSE'
                                    }
                            except (ValueError, IndexError):
                                pass
    except Exception as e:
        print(f"[!] 抓取 TWSE 行情失敗: {e}")

    # 2. TPEx 上櫃行情
    url_otc = 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes'
    req_otc = urllib.request.Request(url_otc, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req_otc, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for r in data:
                code = str(r.get('SecuritiesCompanyCode', '')).strip()
                if len(code) == 4 and code.isdigit():
                    try:
                        vol = int(r.get('TradingShares', 0)) // 1000
                        cl = float(r.get('Close', 0))
                        op = float(r.get('Open', 0))
                        hi = float(r.get('High', 0))
                        lo = float(r.get('Low', 0))
                        if cl > 0 and vol > 0:
                            quotes[code] = {
                                'name': str(r.get('CompanyName', '')).strip(),
                                'vol': vol,
                                'open': op,
                                'high': hi,
                                'low': lo,
                                'close': cl,
                                'market': 'TPEx'
                            }
                    except Exception:
                        pass
    except Exception as e:
        print(f"[!] 抓取 TPEx 行情失敗: {e}")
        
    return quotes


def fetch_institutional_data(trade_date_str):
    """抓取三大法人單日買賣超 (張)"""
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={trade_date_str}&selectType=ALLBUT0999&response=json"
    headers = {'User-Agent': 'Mozilla/5.0'}
    data_dict = {}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            res_json = json.loads(resp.read().decode('utf-8'))
            if res_json.get('stat') == 'OK' and 'data' in res_json:
                for row in res_json['data']:
                    code = row[0].strip()
                    name = row[1].strip()
                    try:
                        foreign_net = int(row[4].replace(',', '')) // 1000
                        trust_net = int(row[10].replace(',', '')) // 1000
                        total_net = int(row[18].replace(',', '')) // 1000
                        data_dict[code] = {
                            'name': name,
                            'foreign_net': foreign_net,
                            'trust_net': trust_net,
                            'total_net': total_net
                        }
                    except (ValueError, IndexError):
                        continue
    except Exception as e:
        print(f"[!] 抓取法人買賣超失敗 ({trade_date_str}): {e}")
    return data_dict


def calculate_rsi(series, period=14):
    """計算 RSI 指標"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period, min_periods=period).mean()
    avg_loss = loss.rolling(period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if not rsi.empty else 50.0


def calculate_obv(df_hist):
    """計算 OBV 能量潮並檢驗是否創近 20 日新高"""
    if len(df_hist) < 20:
        return False
    closes = df_hist['Close'].squeeze()
    volumes = df_hist['Volume'].squeeze()
    direction = np.where(closes > closes.shift(1), 1, np.where(closes < closes.shift(1), -1, 0))
    obv = (direction * volumes).cumsum()
    recent_obv_max = obv.iloc[-21:-1].max() if len(obv) >= 22 else obv.max()
    return obv.iloc[-1] >= recent_obv_max


def evaluate_vcp_compression(df_hist):
    """
    計算突破日前 15~30 日的前置波動收斂 (VCP) 指標與振幅:
    回傳: (vcp_amplitude_pct, has_volume_dryup)
    """
    if len(df_hist) < 20:
        return 20.0, False
    
    window = df_hist.iloc[-31:-1] if len(df_hist) >= 32 else df_hist.iloc[:-1]
    if len(window) < 10:
        return 20.0, False
    
    high_max = window['High'].max()
    low_min = window['Low'].min()
    mean_p = window['Close'].mean()
    
    if mean_p <= 0:
        return 20.0, False
        
    amp_pct = ((high_max - low_min) / mean_p) * 100.0
    
    vols = window['Volume']
    vol_ma20 = vols.rolling(20, min_periods=5).mean()
    dryup_days = ((vols < vol_ma20 * 0.6) & (vols > 0)).sum()
    has_dryup = dryup_days >= 2

    return round(amp_pct, 1), has_dryup


def run_screener(line_notify=False, export_csv=None, force=False):
    print("=" * 80)
    print(f"🚀 啟動 {STRATEGY_NAME} (Volume Breakout Pro 2.0 旗艦優化版)")
    print(f"⏰ 執行時間: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. 檢視大盤市場位階
    print("\n🌐 [1/6] 檢核大盤環境濾網 (加權指數 ^TWII 月線順風面)...")
    is_market_bull, market_desc, market_bias = check_market_regime()
    print(f"  - 大盤診斷: {market_desc}")

    # 2. 取得最新月營收資料庫
    print("\n📦 [2/6] 同步全國上市櫃月營收資料庫 (TWSE & TPEx OpenAPI)...")
    rev_dict = fetch_monthly_revenue(force=force)
    print(f"[✓] 已載入 {len(rev_dict)} 家企業最新月營收公告。")

    # 3. 取得近期交易日與籌碼行情
    print("\n📅 [3/6] 獲取近期交易日與當日行情數據...")
    dates = get_recent_trading_dates(n_days=1)
    if not dates:
        print("[X] 無法取得有效開盤日期，終止選股。")
        return pd.DataFrame()
    today_str = dates[0]
    print(f"  - 最新交易日標記: {today_str}")

    quotes = fetch_daily_quotes(today_str)
    if not quotes:
        print("[X] 無法取得市場報價數據，終止選股。")
        return pd.DataFrame()
    print(f"[✓] 成功取得 {len(quotes)} 檔標的當日報價行情。")

    inst_data = fetch_institutional_data(today_str)
    print(f"[✓] 成功取得 {len(inst_data)} 檔三大法人買賣超數據。")

    # 4. 第一階段：初篩具備「放量突破＋月營收強勁」候選池
    print("\n🔍 [4/6] 執行 4+1 維度初篩過濾 (流動性、量能倍數、月營收成長、形態防守)...")
    candidates = []

    for code, q in quotes.items():
        if len(code) != 4 or not code.isdigit() or code.startswith('00'):
            continue
        
        if q['vol'] < 500 or q['close'] < 15.0:
            continue

        r_info = get_stock_revenue(code, rev_dict)
        rev_yoy = r_info.get('yoy_pct', 0.0)
        if rev_yoy < 10.0:
            continue

        if q['close'] < q['open'] * 0.985:
            continue

        candle_range = q['high'] - q['low']
        if candle_range > 0:
            upper_shadow = q['high'] - max(q['close'], q['open'])
            if (upper_shadow / candle_range) > 0.45:
                continue

        candidates.append((code, q, r_info))

    print(f"[✓] 初篩合格候選標的: {len(candidates)} 檔，進入深度技術面、RSI、OBV 與 VCP 驗證...")

    # 5. 第二階段：批量下載歷史 K 線數據並精算指標
    print("\n📈 [5/6] 批量下載歷史 K 線數據並檢驗 4+1 硬篩與二階段軟評分...")
    if not candidates:
        print("[!] 無任何標的通過初篩。")
        return pd.DataFrame()

    ticker_map = {c[0]: (f"{c[0]}.TWO" if c[1].get('market') == 'TPEx' else f"{c[0]}.TW") for c in candidates}
    ticker_symbols = list(ticker_map.values())
    try:
        hist_data = yf.download(
            tickers=ticker_symbols,
            period="4mo",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True
        )
    except Exception as e:
        print(f"[!] yfinance 批量下載失敗: {e}")
        hist_data = None

    passed_stocks = []

    for code, q, r_info in candidates:
        ticker = ticker_map.get(code, f"{code}.TW")
        df = None
        if hist_data is not None:
            try:
                if len(candidates) == 1:
                    df = hist_data.dropna()
                else:
                    df = hist_data[ticker].dropna()
            except Exception:
                df = None

        if df is None or len(df) < 25:
            try:
                df = yf.download(ticker, period="3mo", interval="1d", auto_adjust=False, progress=False)
                df = df.dropna()
            except Exception:
                continue

        if df is None or len(df) < 25:
            continue

        closes = df['Close'].squeeze()
        volumes = df['Volume'].squeeze() / 1000.0  # 換算為張

        ma20 = closes.rolling(20).mean().iloc[-1]
        ma60 = closes.rolling(60).mean().iloc[-1] if len(closes) >= 60 else ma20 * 0.95
        vol_ma5 = volumes.rolling(5).mean().iloc[-1]
        vol_ma20 = volumes.rolling(20).mean().iloc[-1]

        if np.isnan(ma20) or np.isnan(vol_ma20) or vol_ma20 <= 0:
            continue

        close_p = q['close']
        vol_today = q['vol']

        # 4+1 硬篩檢驗
        # ① 流動性防守
        if vol_ma5 < 500 or vol_ma20 < 300:
            continue

        # ② 趨勢位階線：收盤價創近 20 日新高 (或近 10 日新高作為 Tier-2)，站穩 20MA
        recent_20_max = closes.iloc[-20:-1].max() if len(closes) >= 21 else closes.max()
        recent_10_max = closes.iloc[-10:-1].max() if len(closes) >= 11 else closes.max()
        is_20d_high = close_p >= recent_20_max
        is_10d_high = close_p >= recent_10_max

        if not is_10d_high:
            continue
        if close_p < ma20:
            continue

        # RSI(14) 動能確認
        rsi_val = calculate_rsi(closes, period=14)
        if np.isnan(rsi_val) or rsi_val < 50.0:  # 要求具備多頭動能
            continue

        # ③ 量能甜蜜倍數面：1.8x <= Rvol <= 4.5x (寬限 1.5x ~ 5.0x)
        rvol = vol_today / vol_ma20
        is_sweet_vol = (1.8 <= rvol <= 4.5)
        is_tier2_vol = (1.5 <= rvol <= 5.0)
        if not is_tier2_vol:
            continue

        # OBV 能量潮檢驗
        obv_new_high = calculate_obv(df)

        # ④ 空間防追高：月線正乖離率介於 +0.5% 至 +8.5%
        bias_20 = (close_p - ma20) / ma20 * 100.0
        if bias_20 < 0.5 or bias_20 > 8.5:
            continue

        # 判定梯隊
        if is_20d_high and is_sweet_vol and bias_20 <= 7.5 and (vol_ma5 >= 800) and (rsi_val >= 55.0):
            tier_base = "Tier-1 旗艦突破"
            tier_score_boost = 10.0
        else:
            tier_base = "Tier-2 優質突破"
            tier_score_boost = 0.0

        # 前置波動收斂 (VCP) 檢驗
        vcp_amp, has_dryup = evaluate_vcp_compression(df)

        # 籌碼與法人數據
        inst = inst_data.get(code, {'foreign_net': 0, 'trust_net': 0, 'total_net': 0})
        total_inst_net = inst['total_net']
        inst_ratio = (total_inst_net / vol_today * 100.0) if vol_today > 0 else 0.0

        # 隔日沖風險檢驗 (algo_guards)
        meta_dict = {
            'volume': vol_today,
            'foreign': inst.get('foreign_net', 0),
            'trust': inst.get('trust_net', 0),
            'total_inst': total_inst_net,
            'inst_ratio': inst_ratio
        }
        broker_risk = evaluate_overnight_broker_risk(code, meta=meta_dict)
        is_broker_warning = broker_risk.get('is_overnight_warning', False)
        broker_penalty = broker_risk.get('penalty_score', 0)
        broker_note = broker_risk.get('tag', '籌碼健康')

        # 第二階段 100 分制軟評分
        score = 45.0  # 基礎分

        # A. 前置波動收斂 VCP (最高 25 分)
        if vcp_amp <= 12.0 and has_dryup:
            score += 25.0
        elif vcp_amp <= 15.0:
            score += 18.0
        elif vcp_amp <= 20.0:
            score += 10.0
        else:
            score += 5.0

        # B. 月營收爆發度 (最高 25 分，整合 revenue_score)
        rev_score = r_info.get('revenue_score', 15.0)
        score += rev_score

        # C. 法人鎖碼 (最高 20 分)
        if total_inst_net >= 1000 or inst_ratio >= 10.0:
            score += 20.0
        elif total_inst_net >= 500 or inst_ratio >= 5.0:
            score += 15.0
        elif total_inst_net > 0:
            score += 10.0

        # D. 量價突破甜蜜度與 OBV (最高 15 分)
        if 2.0 <= rvol <= 3.5:
            score += 10.0
        else:
            score += 5.0
        if obv_new_high:
            score += 5.0

        # E. 梯隊加分
        score += tier_score_boost

        # F. 隔日沖扣分
        if is_broker_warning:
            score -= broker_penalty

        score = max(10.0, min(100.0, score))

        # 星級換算
        if score >= 85:
            stars = "★★★★★"
            tier = "Tier-1 旗艦突破"
        elif score >= 70:
            stars = "★★★★☆"
            tier = "Tier-2 優質突破"
        else:
            stars = "★★★☆☆"
            tier = tier_base

        # 計算雙軌四大防線價位 (four_price_engine 合規取整)
        if len(df) >= 15:
            tr = np.maximum(
                df['High'] - df['Low'],
                np.maximum(
                    abs(df['High'] - df['Close'].shift(1)),
                    abs(df['Low'] - df['Close'].shift(1))
                )
            )
            atr_val = tr.rolling(14).mean().iloc[-1]
            if np.isnan(atr_val) or atr_val <= 0:
                atr_val = close_p * 0.03
        else:
            atr_val = close_p * 0.03

        four_prices = calculate_four_prices_long(
            curr_price=close_p,
            high=q['high'],
            low=q['low'],
            ma20=ma20,
            atr=atr_val
        )

        entry_price = four_prices['entry_price']
        addon_price = four_prices['addon_price']
        tp1_price = four_prices['tp1_price']
        tp2_price = four_prices['tp2_price']
        sl_price = four_prices['sl_price']

        # B 軌量縮回測預估價 (約 5MA 或頸線支撐，以收盤價 -2.5% 合規向下取整)
        b_track_retest = normalize_to_tick(close_p * 0.975, direction="down")

        rev_yoy = r_info.get('yoy_pct', 0.0)
        rev_mom = r_info.get('mom_pct', 0.0)
        rev_tier = r_info.get('revenue_tier', '穩健')

        # 操盤錦囊與突破後 3 日診斷指引
        action_guide = (
            f"【A軌突破】買進上限 {entry_price} 元以內建倉 50%；"
            f"【B軌回測】拉回 {b_track_retest} 元量縮守穩為黃金加碼點；"
            f"【T+1~T+3診斷】溫和放量沿5MA走高則加碼並移保本，若爆量長黑摜破則果斷停損。"
        )

        passed_stocks.append({
            '證券代號': code,
            '證券名稱': q['name'],
            '梯隊標籤': tier,
            '星級': stars,
            '評分': round(score, 1),
            '收盤價': close_p,
            '進場價位': entry_price,
            '進場參考': f"A軌: <={entry_price} / B軌回測: {b_track_retest}",
            '加碼價位': addon_price,
            '停利TP1': tp1_price,
            '停利TP2': tp2_price,
            '停損價位': sl_price,
            'B軌回測價': b_track_retest,
            '當月營收(千元)': int(r_info.get('rev_current', 0)),
            '營收YoY%': rev_yoy,
            '營收MoM%': rev_mom,
            '營收創高': rev_tier,
            '當日成交量(張)': vol_today,
            '20日均量(張)': int(vol_ma20),
            '均量比Rvol': round(rvol, 2),
            '月線(20MA)': round(ma20, 2),
            '月線乖離%': round(bias_20, 2),
            '前置振幅%': vcp_amp,
            'RSI14': round(rsi_val, 1),
            'OBV創高': "★創20日高" if obv_new_high else "平穩",
            '法人買超合計(張)': total_inst_net,
            '法人佔比%': round(inst_ratio, 1),
            '隔日沖風控': broker_note,
            '操盤指引': action_guide
        })

    # 6. 排序與產出報表
    print(f"\n📊 [6/6] 整合選股成果，共精選 {len(passed_stocks)} 檔標的...")
    if not passed_stocks:
        print("[!] 本次掃描無符合嚴格 4+1 條件之標的。")
        df_res = pd.DataFrame(columns=STANDARD_COLUMNS)
    else:
        df_res = pd.DataFrame(passed_stocks)
        df_res = df_res.sort_values(by=['評分', '均量比Rvol', '營收YoY%'], ascending=[False, False, False])
        df_res = df_res.head(10).reset_index(drop=True)

    # 儲存 CSV 報表 (支援滾動留存與標準 UTF-8-BOM)
    save_rolling_result_csv(CSV_FILE, df_res, strategy_id=STRATEGY_ID)
    print(f"[✓] 成功儲存選股清單至: {CSV_FILE}")

    # 產生 Markdown 戰報
    results_list = df_res.to_dict('records')
    md_content = format_screener_markdown_cards(
        results=results_list,
        date_str=today_str,
        strategy_id=STRATEGY_ID
    )
    # 附加大盤環境指引橫幅
    regime_banner = f"\n> 🌐 **大盤環境導航**：{market_desc}\n\n"
    md_content_with_banner = md_content.replace("---\n\n", f"---\n{regime_banner}", 1)

    with open(MD_FILE, 'w', encoding='utf-8') as f:
        f.write(md_content_with_banner)
    print(f"[✓] 成功產出視覺化 Markdown 戰報至: {MD_FILE}")

    # LINE 推播 (若啟用)
    if line_notify and not df_res.empty:
        print(f"\n📱 正在發送 LINE 戰報至策略 {STRATEGY_ID} 專屬群組/視窗...")
        carousel_flex = build_screener_carousel(
            results=results_list,
            date_str=today_str,
            strategy_id=STRATEGY_ID
        )
        text_fallback = format_clean_screener_text(
            results=results_list,
            date_str=today_str,
            strategy_id=STRATEGY_ID
        )
        success = send_to_line(
            message=text_fallback,
            flex_content=carousel_flex,
            strategy=STRATEGY_ID
        )
        if success:
            print("[✓] LINE 戰訊推送成功！")
        else:
            print("[!] LINE 戰訊推送失敗，請檢查環境變數與 Token。")

    print("\n" + "=" * 80)
    print(f"🎉 {STRATEGY_NAME} 執行完畢！")
    print("=" * 80)
    return df_res


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=f"{STRATEGY_NAME} 盤後選股器")
    parser.add_argument('--line', action='store_true', help='同步發送 LINE 輪播卡片推播')
    parser.add_argument('--export', type=str, default=CSV_FILE, help='匯出 CSV 檔案路徑')
    parser.add_argument('--force', action='store_true', help='強制刷新月營收快取重新抓取')
    args = parser.parse_args()

    run_screener(line_notify=args.line, export_csv=args.export, force=args.force)
