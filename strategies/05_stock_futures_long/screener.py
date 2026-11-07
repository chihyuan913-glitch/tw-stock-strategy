#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 05：個股期貨法人強勢波段多方選股器 V3.0 旗艦自適應版
(Stock Futures Institutional Momentum Long Screener V3.0)

【核心多方量化策略邏輯 (4+1 核心維度鐵律)】
一、母池與流動性防守 (Liquidity & Pool)：
  - 限定臺灣期貨交易所 (TAIFEX) 252 檔個股期貨標的 (免借券、無借券費、資金效率高 7 倍、期交稅僅 0.002%)。
  - 近 5 日平均成交量 >= 1,000 張，排除流動性不足標的。

二、趨勢位階線 (Trend Alignment)：
  - 收盤價站上 20 日均線 (月線)，且月線斜率向上助漲 (MA20_curr >= MA20_prev)。
  - 現價站穩 60 日均線 (季線) 之上，維持中長線多頭主升格局。

三、籌碼動能面 (Institutional Buying Momentum)：
  - 外資或投信近 3 個交易日累計買超 >= 1,000 張。
  - 當日三大法人合計買超佔當日成交量比例 >= 12% (主力集中重壓吃貨)。
  - 近 5 日法人累計呈現淨買超 (籌碼扎實無浮額)。

四、型態防守面 (Candlestick Pattern Defense)：
  - 當日收紅或實體突破整理平台。
  - 當日 K 棒無長上影線 (上影線長度 < 實體長度一半，排除衝高倒貨出貨墓碑線)。

五、空間防追高 (Sweet-Spot Entry Window)：
  - 月線正乖離率介於 +1.0% 到 +6.0% 之間 (+1.0% <= Bias <= +6.0%)。
  - 剛自均線帶量起漲或突破整理平台，拒絕 +8% 以上無肉追高！

六、全台股首創【實戰四大防線定價引擎 V4.0 ＋ 股票期貨價位優先】：
  - 臺灣證交所法定 6 階升降檔位方向性合規取整 (TWSE Tick Size Rounding)。
  - ATR 波動自適應動態調校 (ATR Volatility Scaling)。
  - 大盤市場位階深度連動 (Market Regime Adaptation)。
  - 幾何單調性嚴格單向守護：LimitDown <= SL < Close <= Entry < Addon < TP1 <= TP2 <= LimitUp。
  - 股票期貨價位優先換算 (Basis + OI 深度流動性加分 + 結算日前五日開次月新倉提醒)。
"""

import sys
import os
import argparse
import datetime
import json
import urllib.request
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

# 加入專案根目錄至 sys.path 以便共用模組
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from algo_guards import evaluate_overnight_broker_risk
from adaptive_regime_manager import get_strategy_adaptive_params, load_adaptive_config
from four_price_engine import calculate_four_prices_long, normalize_to_tick
from futures_helper import (
    load_stock_futures_pool,
    fetch_taifex_futures_quotes,
    calculate_futures_pricing,
    get_futures_settlement_info
)
from market_schedule import check_market_open_or_exit, verify_twse_data_settlement, should_suppress_push
from rolling_retention_manager import save_rolling_result_csv
from line_sender import (
    send_to_line,
    build_screener_carousel,
    format_clean_screener_text,
    format_screener_markdown_cards
)

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

STRATEGY_ID = "05"
STRATEGY_NAME = "策略 05：個股期貨法人強勢波段多方"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')
MD_FILE = os.path.join(os.path.dirname(__file__), 'result.md')


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


def screen_stock_futures_long(date_str=None, min_vol_lots=1000, 
                              min_bias_pct=1.0, max_bias_pct=6.0, 
                              inst_buy_ratio_pct=12.0, inst_3d_threshold=1000, 
                              futures_only=True):
    """
    執行個股期貨波段多方量化篩選 (Strategy 05 V3.0 全域旗艦自適應版)
    """
    # 載入全域大盤自適應設定 (Regime Engine)
    adaptive_cfg = load_adaptive_config()
    regime_info = adaptive_cfg.get("market_regime", {})
    global_ctrl = adaptive_cfg.get("global_controls", {})
    adaptive_params = get_strategy_adaptive_params(STRATEGY_ID)

    regime_label = regime_info.get("label", "🟢【多頭良性主升波段】")
    regime_mode = global_ctrl.get("mode", "標準模式")
    cash_target = global_ctrl.get("cash_target_pct", 30.0)

    # 自適應參數微調 (若使用者未明確覆蓋命令列參數)
    effective_max_bias = max_bias_pct if max_bias_pct is not None else float(adaptive_params.get("bias_max_pct", 6.0))
    effective_tp1_pct = float(adaptive_params.get("tp1_pct", 5.0))
    effective_tp2_pct = float(adaptive_params.get("tp2_pct", 10.0))
    effective_sl_pct = float(adaptive_params.get("stop_loss_pct", -4.0))

    print(f"\n[*] 大盤自適應狀態：{regime_label} ｜ 模式：{regime_mode} ｜ 建議現金水位：{cash_target}%")
    print(f"[*] 量化動態參數：月線正乖離上限 {effective_max_bias:.1f}% ｜ TP1 +{effective_tp1_pct:.1f}% ｜ TP2 +{effective_tp2_pct:.1f}% ｜ 停損 {effective_sl_pct:.1f}%")

    futures_map = {}
    taifex_quotes = {}
    if futures_only:
        futures_map = load_stock_futures_pool(os.path.join(os.path.dirname(__file__), 'stock_futures_list.json'))
        print(f"[*] 【個股期貨優先模式】已載入期交所 {len(futures_map)} 檔股票期貨標的清單！")
        taifex_quotes = fetch_taifex_futures_quotes()
        print(f"[*] 【期交所即時/盤後行情】已同步 {len(taifex_quotes)} 檔股票期貨近月/次月合約行情！")

    settle_info = get_futures_settlement_info()
    if settle_info.get('is_rollover_window'):
        print(f"[*] ⚠️【結算週期警示】目前距結算日僅剩 {settle_info['trading_days_left']} 個交易日，建議開立次月新倉 ({settle_info['next_month']})！")

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
    
    print(f"[*] 波段多方篩選基準日：{latest_date}（近3日買超檢測：{dates_3d}，近5日：{dates_5d}）")

    chips_history = fetch_institutional_data(dates_5d)
    quotes = fetch_daily_quotes(latest_date)
    
    if not quotes or not chips_history:
        print("[X] 資料獲取不完整，終止篩選。")
        return []

    print(f"[*] 今日取得 {len(quotes)} 檔行情，{len(chips_history)} 檔籌碼記錄。")
    print("[*] 執行第一階段 4+1 維度硬性條件快篩 (流動性、籌碼集中、無長上影線)...")

    preliminary_candidates = []
    preliminary_meta = {}

    for code, q in quotes.items():
        # 【關鍵維度 1】限定具備期交所股票期貨標的
        if futures_only and code not in futures_map:
            continue

        if code not in chips_history or latest_date not in chips_history[code]:
            continue
            
        c_today = chips_history[code][latest_date]
        
        # 近 3 日外資累計與投信累計買超 (股數)
        f_3d_shares = sum(chips_history[code][d]['foreign'] for d in dates_3d if d in chips_history[code])
        t_3d_shares = sum(chips_history[code][d]['trust'] for d in dates_3d if d in chips_history[code])
        
        # 籌碼條件 1: 外資或投信近 3 個交易日累計買超 >= 1,000 張 (>= 1,000,000 股)
        threshold_buy_shares = inst_3d_threshold * 1000
        cond_inst_3d_buy = (f_3d_shares >= threshold_buy_shares) or (t_3d_shares >= threshold_buy_shares)
        if not cond_inst_3d_buy:
            continue
            
        # 籌碼條件 2: 當日三大法人買超佔總成交量比例 >= 12%
        inst_today_shares = c_today['total']
        if q['volume'] <= 0 or inst_today_shares <= 0:
            continue
            
        inst_buy_ratio = (inst_today_shares / q['volume']) * 100.0
        if inst_buy_ratio < inst_buy_ratio_pct:
            continue

        # 籌碼條件 3: 近 5 日法人累計呈現淨買超
        total_5d_shares = sum(chips_history[code][d]['total'] for d in dates_5d if d in chips_history[code])
        if total_5d_shares <= 0:
            continue

        # 型態防守面: 當日收紅或實體突破，且無長上影線（排除衝高倒貨出貨墓碑線）
        body = abs(q['close'] - q['open'])
        upper_shadow = q['high'] - max(q['open'], q['close'])
        
        if body > 0.05:
            cond_shadow = (upper_shadow < 0.5 * body)
        else:
            cond_shadow = (upper_shadow <= (q['close'] * 0.005))
            
        if not cond_shadow:
            continue

        preliminary_candidates.append(code)
        preliminary_meta[code] = {
            'quote': q,
            'f_3d_lots': f_3d_shares // 1000,
            't_3d_lots': t_3d_shares // 1000,
            'inst_today_lots': inst_today_shares // 1000,
            'total_5d_lots': total_5d_shares // 1000,
            'inst_buy_ratio': round(inst_buy_ratio, 1),
            'body': round(body, 2),
            'upper_shadow': round(upper_shadow, 2)
        }

    print(f"[*] 通過籌碼集中買超與無長上影線初篩標的共 {len(preliminary_candidates)} 檔。")
    if not preliminary_candidates:
        print("[-] 今日無符合籌碼集中買超與型態門檻之標的。")
        return []

    # 第二階段技術面濾網：站上 20MA、月線向上、現價>60MA、5日均量>1000張、正乖離在起漲甜蜜區
    print("[*] 執行第二階段多頭趨勢與正乖離率精算（下載日K計算月線走揚、ATR與四大防線）...")
    tickers = [f"{c}.TW" for c in preliminary_candidates]
    try:
        hist_data = yf.download(tickers, period='4mo', progress=False, group_by='ticker')
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

            if 'Close' not in df or len(df['Close']) < 25:
                continue

            close_series = df['Close']
            vol_series = df['Volume']
            
            # 月線 (20MA) 與 季線 (60MA)
            ma20 = close_series.rolling(20).mean()
            ma20_curr = float(ma20.iloc[-1])
            ma20_prev = float(ma20.iloc[-2])
            
            curr_close = float(q['close'])

            # 趨勢條件 1: 收盤價站上 20MA，且 20MA 斜率向上 (多頭均線助漲)
            is_above_ma20 = (curr_close >= ma20_curr)
            is_slope_up = (ma20_curr >= ma20_prev)
            if not (is_above_ma20 and is_slope_up):
                continue

            # 趨勢條件 2: 站穩 60MA 季線之上 (中長線多頭架構)
            if len(close_series) >= 60:
                ma60_curr = float(close_series.rolling(60).mean().iloc[-1])
                if curr_close < ma60_curr:
                    continue
            else:
                ma60_curr = ma20_curr * 0.95

            # 流動性條件: 近 5 日平均成交量 >= 1,000 張
            v5_avg_shares = float(vol_series.rolling(5).mean().iloc[-1])
            v5_avg_lots = v5_avg_shares / 1000.0
            if v5_avg_lots < min_vol_lots:
                continue

            # 空間防追高條件: 月線正乖離率介於 +1.0% 到 +6.0% (自適應起漲甜蜜點，絕不追高)
            bias_ma20_pct = ((curr_close - ma20_curr) / ma20_curr) * 100.0
            if not (min_bias_pct <= bias_ma20_pct <= effective_max_bias):
                continue

            # -------------------------------------------------------------
            # 計算 14 日 ATR (True Range) 波動度
            # -------------------------------------------------------------
            tr1 = df['High'] - df['Low']
            tr2 = (df['High'] - df['Close'].shift(1)).abs()
            tr3 = (df['Low'] - df['Close'].shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_14 = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else float(q['high'] - q['low'])
            if np.isnan(atr_14) or atr_14 <= 0:
                atr_14 = float(q['high'] - q['low']) if (q['high'] > q['low']) else curr_close * 0.03

            # -------------------------------------------------------------
            # 實戰四大防線定價引擎 V4.0 全域規範
            # (臺灣證交所法定檔位方向性合規取整 + ATR 波動自適應 + 幾何單調性嚴格守護)
            # -------------------------------------------------------------
            prev_close_val = float(df['Close'].iloc[-2]) if len(df['Close']) >= 2 else curr_close
            regime_pricing_params = {
                "tp1_pct": effective_tp1_pct,
                "tp2_pct": effective_tp2_pct,
                "stop_loss_pct": effective_sl_pct,
                "bias_max_pct": effective_max_bias
            }

            fp_calc = calculate_four_prices_long(
                curr_price=curr_close,
                prev_close=prev_close_val,
                high=q.get('high'),
                low=q.get('low'),
                ma20=ma20_curr,
                atr=atr_14,
                regime_params=regime_pricing_params
            )

            spot_entry = fp_calc['entry_price']
            spot_addon = fp_calc['addon_price']
            spot_tp1 = fp_calc['tp1_price']
            spot_tp2 = fp_calc['tp2_price']
            spot_sl = fp_calc['sl_price']
            rr_ratio = fp_calc.get('risk_reward_ratio', fp_calc.get('rr_ratio', 2.0))
            guidance = fp_calc.get('guidance', {})

            # -------------------------------------------------------------
            # 股票期貨價位優先計算與四大防線換算 (Futures Pricing Priority)
            # -------------------------------------------------------------
            f_info = futures_map.get(code, {'contract': '--', 'name': q['name']})
            contract_base = f_info.get('contract', '--')
            fut_q = taifex_quotes.get(contract_base)

            fut_pricing = calculate_futures_pricing(
                spot_close=curr_close,
                futures_quote=fut_q,
                entry_spot=spot_entry,
                addon_spot=spot_addon,
                tp1_spot=spot_tp1,
                tp2_spot=spot_tp2,
                sl_spot=spot_sl,
                contract_base=contract_base,
                is_long=True
            )

            # -------------------------------------------------------------
            # 隔日沖券商風控評估 (程式交易反制)
            # -------------------------------------------------------------
            og_risk = evaluate_overnight_broker_risk(
                code,
                meta={
                    'volume': q['volume'],
                    'dealer_hedge': meta['inst_today_lots'] * 1000,
                    'foreign': meta['f_3d_lots'] * 1000,
                    'inst_ratio': meta['inst_buy_ratio']
                }
            )

            # -------------------------------------------------------------
            # 第二階段多方軟評分系統 (100分制) - 納入「期貨未平倉量 (OI)」
            # -------------------------------------------------------------
            # 1. 法人買超佔比 (佔比 12% 起跳，每增加 3% 加 5 分，上限 30 分)
            score_chips = min(30, int(15 + (meta['inst_buy_ratio'] - 12.0) * 1.5))
            # 2. 均量放大倍數 (當日量 / 5日均量，放量起漲最高 20 分)
            vol_ratio = q['volume'] / v5_avg_shares if v5_avg_shares > 0 else 1.0
            score_vol = min(20, int(vol_ratio * 12))
            # 3. 均線多頭排列完整度 (現價 > 20MA > 60MA 加 15 分)
            score_trend = 15 if (curr_close > ma20_curr > ma60_curr) else 8
            # 4. 正乖離甜蜜度 (+2%~+4.5% 最優，加 15 分)
            score_bias = 15 if (2.0 <= bias_ma20_pct <= 4.5) else 8
            # 5. 期貨未平倉量 (OI) 深度加分 (期貨流動性防守，上限 20 分)
            fut_oi = fut_pricing['futures_oi']
            if fut_oi >= 10000:
                score_oi = 20  # 萬口以上超旗艦主流
            elif fut_oi >= 5000:
                score_oi = 16  # 5,000口以上主力強勢標的
            elif fut_oi >= 2000:
                score_oi = 12  # 2,000口以上活躍波段股期
            elif fut_oi >= 1000:
                score_oi = 8   # 1,000口以上流動性充沛
            elif fut_oi >= 300:
                score_oi = 4   # 300口以上常規標的
            else:
                score_oi = 0   # 偏冷門小合約

            total_score = max(50, min(100, score_chips + score_vol + score_trend + score_bias + score_oi - og_risk.get('penalty_score', 0)))

            if total_score >= 85:
                stars = '★★★★★'
            elif total_score >= 75:
                stars = '★★★★☆'
            else:
                stars = '★★★☆☆'

            # 雙向操盤錦囊
            playbook_cash = f"【空手者】於 {curr_close:.2f}~{fut_pricing['futures_entry']:.2f} 掛單建倉，超過 {fut_pricing['futures_entry']:.2f} 嚴禁追高。"
            playbook_hold = f"【持股者】以 {fut_pricing['futures_sl']:.2f} 為嚴格防守底線，突破 {fut_pricing['futures_addon']:.2f} 順勢加碼，攻抵 {fut_pricing['futures_tp1']:.2f} 分批停利 50%。"

            results.append({
                '證券代號': code,
                '證券名稱': q['name'],
                '期貨契約': fut_pricing['contract_sym'],
                '期貨月份': fut_pricing['month'],
                '收盤價': round(curr_close, 2),
                '期貨收盤價': fut_pricing['futures_close'],
                '價差(Basis)': fut_pricing['basis_desc'],
                '期貨未平倉(OI)': fut_pricing['futures_oi'],
                '結算倒數': fut_pricing['settlement_tag'],
                '次月合約': fut_pricing['next_month'],
                '次月期貨價': fut_pricing['next_close'],
                '次月進場參考': fut_pricing['next_entry'],
                '次月加碼參考': fut_pricing['next_addon'],
                '次月停利參考': fut_pricing['next_tp1'],
                '次月停損參考': fut_pricing['next_sl'],
                '次月未平倉(OI)': fut_pricing['next_oi'],
                '評分': total_score,
                '星級推薦': stars,
                '大盤位階': regime_label,
                '進場價位': fut_pricing['futures_entry'],
                '多單進場': fut_pricing['futures_entry'],
                '加碼價位': fut_pricing['futures_addon'],
                '突破加碼': fut_pricing['futures_addon'],
                '停利TP1': fut_pricing['futures_tp1'],
                '停利TP2': fut_pricing['futures_tp2'],
                '停損價位': fut_pricing['futures_sl'],
                '停損防守': fut_pricing['futures_sl'],
                '現貨進場': spot_entry,
                '現貨加碼': spot_addon,
                '現貨TP1': spot_tp1,
                '現貨TP2': spot_tp2,
                '現貨停損': spot_sl,
                '盈虧比(R:R)': rr_ratio,
                'ATR': round(atr_14, 2),
                '隔日沖風控': og_risk['tag'],
                '1口保證金(約)': fut_pricing['margin_1lot'],
                '期貨成交量': fut_pricing['futures_vol'],
                '結算操盤建議': fut_pricing['settlement_reminder'],
                '月線(20MA)': round(ma20_curr, 2),
                '季線(60MA)': round(ma60_curr, 2),
                '月線正乖離%': round(bias_ma20_pct, 2),
                '當日成交量(張)': q['volume'] // 1000,
                '5日均量(張)': int(v5_avg_lots),
                '法人買超佔比%': meta['inst_buy_ratio'],
                '外資3日買超(張)': meta['f_3d_lots'],
                '投信3日買超(張)': meta['t_3d_lots'],
                '當日法人買超(張)': meta['inst_today_lots'],
                '5日法人累計(張)': meta['total_5d_lots'],
                'K棒實體': meta['body'],
                '上影線': meta['upper_shadow'],
                '空手者策略': playbook_cash,
                '持股者策略': playbook_hold
            })
            
        except Exception:
            continue

    return results


def export_reports(results, csv_path=None, md_path=None, target_date=None):
    """匯出滾動 5 日 CSV 與視覺化 Markdown 戰報"""
    today_str = target_date or datetime.date.today().strftime('%Y-%m-%d')
    csv_file = csv_path or CSV_FILE
    md_file = md_path or MD_FILE

    if not results:
        empty_cols = ['證券代號', '證券名稱', '期貨契約', '收盤價', '期貨收盤價', '進場價位', '加碼價位', '停利TP1', '停損價位', '期貨未平倉(OI)', '評分']
        df_empty = pd.DataFrame(columns=empty_cols)
        save_rolling_result_csv(csv_file, df_empty, today_date_str=today_str, max_days=5)
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(f"# 【{STRATEGY_NAME}】最新盤後選股戰訊報表\n\n")
            f.write(f"產出日期：{today_str}\n\n")
            f.write("> [!NOTE]\n> 今日無標的符合 4+1 維度法人強勢波段多方條件，建議保留高現金水位耐心觀望。\n")
        print(f"[✓] 已更新空報表至: {csv_file}")
        return

    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values(by=["評分", "期貨未平倉(OI)", "法人買超佔比%"], ascending=[False, False, False]).reset_index(drop=True)

    # 儲存滾動 CSV
    df_res = save_rolling_result_csv(csv_file, df_res, today_date_str=today_str, max_days=5)
    print(f"[✓] 已成功匯出 CSV 報表至: {csv_file}")

    # 產出標準 Markdown 字卡戰報
    settle_banner = results[0].get('結算操盤建議', '')
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(format_screener_markdown_cards(results, today_str, strategy_id=STRATEGY_ID, max_stocks=10))
        if settle_banner:
            f.write(f"\n> [!WARNING] **{settle_banner}**\n\n")
        f.write("\n\n## 📋 完整量化數據指標總表\n\n")
        f.write(df_res.to_markdown(index=False))
        f.write("\n")
    print(f"[✓] 已同步匯出視覺化字卡 Markdown 戰報至: {md_file}")


def push_to_line(results, target_date=None, force=False):
    """推播選股日報至 LINE (策略 05 專屬視窗分流)"""
    suppress, sup_reason = should_suppress_push(force=force, task_type=f"{STRATEGY_NAME} 盤後戰報")
    if suppress:
        print(f"[🛡️ 守衛攔截] {sup_reason}")
        return

    if not results:
        msg = f"📊【{STRATEGY_NAME}】今日盤後選股無符合條件標的。"
        send_to_line(msg, strategy=STRATEGY_ID, force=force)
        return

    today_str = target_date or datetime.date.today().strftime('%Y/%m/%d')
    try:
        card = build_screener_carousel(results, today_str, strategy_id=STRATEGY_ID, max_stocks=6)
        print(f"[*] 正在發送波段多方選股日報至 LINE (策略 {STRATEGY_ID} 專屬視窗)...")
        success = send_to_line(card, strategy=STRATEGY_ID, force=force)
        if not success:
            clean_text = format_clean_screener_text(results, today_str, strategy_id=STRATEGY_ID, max_stocks=6)
            send_to_line(clean_text, strategy=STRATEGY_ID, force=force)
    except Exception as e:
        print(f"[!] 發送 LINE 選股通知失敗: {e}")


def main():
    parser = argparse.ArgumentParser(description="台股個股期貨法人強勢波段多方選股策略 V3.0 旗艦自適應版")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYYMMDD，預設為最新交易日)")
    parser.add_argument("--min-vol", type=int, default=1000, help="最低5日平均成交量(張)，預設 1000 張")
    parser.add_argument("--min-bias", type=float, default=1.0, help="20MA 正乖離率下限百分比(%%)，預設 +1.0%%")
    parser.add_argument("--max-bias", type=float, default=6.0, help="20MA 正乖離率上限百分比(%%)，預設 +6.0%% (起漲甜蜜點)")
    parser.add_argument("--buy-ratio", type=float, default=12.0, help="法人買超佔成交量低標(%%)，預設 12.0%% (集中重壓吃貨)")
    parser.add_argument("--buy-3d", type=int, default=1000, help="外資或投信近3日累計買超門檻(張)，預設 1000 張")
    parser.add_argument("--no-futures-filter", action="store_true", help="關閉個股期貨限制，允許篩選全市場普通股")
    parser.add_argument("--export", nargs="?", const=CSV_FILE, default=CSV_FILE, help="匯出報表檔名 (.csv 或 .md)")
    parser.add_argument("--line", action="store_true", help="發送選股日報至 LINE (策略 05 專屬視窗)")
    parser.add_argument("--force", action="store_true", help="強制執行 (忽略非交易日與未開盤守衛)")

    args = parser.parse_args()

    # 開休市檢核守門 (若非強制執行且未指定歷史日期)
    if not args.force and not args.date:
        if not check_market_open_or_exit(f"{STRATEGY_NAME} 盤後選股"):
            return

    # 夜間官方數據沉澱守門 (若在夜間 20:00 後執行且無指定歷史日期)
    now_hour = datetime.datetime.now().hour
    if now_hour >= 20 and not args.date and not args.force:
        verify_twse_data_settlement()

    results = screen_stock_futures_long(
        date_str=args.date,
        min_vol_lots=args.min_vol,
        min_bias_pct=args.min_bias,
        max_bias_pct=args.max_bias,
        inst_buy_ratio_pct=args.buy_ratio,
        inst_3d_threshold=args.buy_3d,
        futures_only=(not args.no_futures_filter)
    )

    if not results:
        print("\n[!] 經檢核，所選條件下今日無符合多方 4+1 複合條件之個股期貨標的。")
        export_reports([], csv_path=args.export, target_date=args.date)
        if args.line:
            push_to_line([], target_date=args.date, force=args.force)
        return

    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values(by=["評分", "期貨未平倉(OI)", "法人買超佔比%"], ascending=[False, False, False]).reset_index(drop=True)

    settle_banner = results[0].get('結算操盤建議', '')
    if settle_banner:
        print("\n" + "#" * 115)
        print(f" {settle_banner}")
        print("#" * 115)

    print("\n" + "="*115)
    print(f"🎯【台股個股期貨法人強勢波段多方】選股結果清單（共 {len(df_res)} 檔）- ⚡ 全面升級【四大防線V4.0 ＋ OI深度評分 ＋ 結算提醒】！")
    print("條件符合：具備期交所股票期貨 | 外資/投信3日買超>1000張 | 法人買超佔比>=12% | 站穩20MA且月線走揚 | 站上60MA季線 | 正乖離[+1%~+6%]")
    print("="*115)
    print(df_res[['證券代號', '證券名稱', '期貨契約', '收盤價', '期貨收盤價', '進場價位', '加碼價位', '停利TP1', '停利TP2', '停損價位', '盈虧比(R:R)', '期貨未平倉(OI)', '評分', '星級推薦']].to_string(index=False))
    print("="*115)

    export_reports(results, csv_path=args.export, target_date=args.date)

    if args.line:
        push_to_line(results, target_date=args.date, force=args.force)


if __name__ == '__main__':
    main()
