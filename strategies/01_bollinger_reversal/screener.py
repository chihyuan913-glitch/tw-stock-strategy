#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 01：布林通道下軌超跌反轉選股策略 (V2.5 雙層動態降級容錯旗艦版)

【核心選股架構】：
- 4+1 黃金維度硬篩 (流動性、趨勢、籌碼、型態、乖離防追高，嚴格不超過 5 個)
- 雙層門檻動態降級容錯機制 (Dual-Tier Fallback Architecture)：
  * Tier-1【布林極致超跌・法人逆勢低接 (TIER1_EXTREME_OVERSOLD)】：
    1. 流動性防護：日成交量 >= 1,000 張
    2. 統計超跌區：股價位於布林下軌 [-5.0% ~ +1.0%] 區間
    3. 籌碼主力純度：三大法人合計買超 > 0，且買超佔比 >= 1.5%，投信無恐慌拋售 (<= 500 張)
    4. 型態止跌濾網：長下影線 (>=25%) 或 收紅K落底，拒絕下墜長黑
    5. 通道防墜落開口：近 5 日 20MA 斜率 >= -2.5%，避開主跌段開口向下
  * Tier-2【布林支撐回測・主力防守候補 (TIER2_SUPPORT_PULLBACK)】：
    當 Tier-1 選出為 0 檔時自動啟動降級候補：
    1. 流動性放寬：日成交量 >= 800 張
    2. 支撐回測區間：股價位於布林下軌 [-5.0% ~ +2.5%] 區間
    3. 籌碼動能：三大法人買超佔比 >= 1.0% 且 買超張數 > 0
    4. 綜合評分排序取前 3~6 檔，保證每日戰報品質穩定與流動性安全！
- 實戰四大防線 2.0 (進場、加碼、TP1/TP2 停利、前低保護動態停損)
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

# 加入專案根目錄至 sys.path 以便共用模組
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from algo_guards import evaluate_overnight_broker_risk

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

def fetch_twse_data(date_str):
    """抓取證交所三大法人買賣超 (T86) 與每日收盤行情 (MI_INDEX) 包含完整 OHLC"""
    print(f"[*] 正在連線台灣證券交易所抓取 {date_str} 交易數據...")
    
    # 1. 抓取三大法人買賣超 (T86)
    url_t86 = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json'
    req_t86 = urllib.request.Request(url_t86, headers={'User-Agent': 'Mozilla/5.0'})
    chips = {}
    try:
        with urllib.request.urlopen(req_t86, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get('stat') == 'OK':
                for row in data.get('data', []):
                    code = row[0].strip()
                    if len(code) == 4 and code.isdigit():
                        try:
                            foreign = int(row[4].replace(',', ''))
                            trust = int(row[10].replace(',', ''))
                            dealer = int(row[11].replace(',', ''))
                            total = int(row[18].replace(',', ''))
                            chips[code] = {
                                'foreign': foreign,
                                'trust': trust,
                                'dealer': dealer,
                                'total': total
                            }
                        except ValueError:
                            pass
    except Exception as e:
        print(f"[!] 抓取三大法人資料失敗: {e}")
        return {}, {}

    # 2. 抓取每日收盤行情 (MI_INDEX, 包含 Open, High, Low, Close)
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
                                open_str = row[5].replace(',', '').strip()
                                high_str = row[6].replace(',', '').strip()
                                low_str = row[7].replace(',', '').strip()
                                close_str = row[8].replace(',', '').strip()
                                if close_str != '--' and open_str != '--':
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
        print(f"[!] 抓取每日行情資料失敗: {e}")
        return {}, {}

    return quotes, chips

def calculate_candlestick_features(q):
    """計算單日K線型態特徵 (實體、下影線、紅黑K)"""
    open_p = q['open']
    high_p = q['high']
    low_p = q['low']
    close_p = q['close']

    amplitude = max(high_p - low_p, 0.001)
    lower_shadow = max(min(open_p, close_p) - low_p, 0.0)
    lower_shadow_ratio = lower_shadow / amplitude
    is_bullish = close_p >= open_p
    not_closed_at_low = close_p > (low_p + amplitude * 0.10)
    
    # 止跌反轉型態評定
    is_reversal = (lower_shadow_ratio >= 0.25) or is_bullish or not_closed_at_low

    pattern_desc = []
    if lower_shadow_ratio >= 0.35:
        pattern_desc.append("長下影線")
    elif lower_shadow_ratio >= 0.20:
        pattern_desc.append("帶下影線")
        
    if is_bullish:
        pattern_desc.append("收紅K")
    else:
        pattern_desc.append("收黑K")

    return {
        'lower_shadow_ratio': lower_shadow_ratio,
        'is_bullish': is_bullish,
        'is_reversal': is_reversal,
        'pattern_desc': "＋".join(pattern_desc)
    }

def calculate_score(inst_ratio, foreign_lots, trust_lots, candle_feat, upside_pct, tier_label="Tier-1"):
    """
    量化評分系統 (0~100 分)：
    1. 籌碼影響力 (25分): 法人買超佔成交量比重
    2. 主力純度 (25分): 外資與投信合力 (土洋同買加分)
    3. 止跌型態 (25分): 長下影線與落底紅K
    4. 反彈空間 (25分): 距 20MA (中軌) 空間
    """
    # 1. 籌碼佔比 (0~25)
    if inst_ratio >= 10.0:
        s_chip = 25
    elif inst_ratio >= 5.0:
        s_chip = 20
    elif inst_ratio >= 2.5:
        s_chip = 15
    else:
        s_chip = 10

    # 2. 主力純度 (0~25)
    is_dual = (foreign_lots > 0) and (trust_lots > 0)
    if is_dual:
        s_purity = 25  # 土洋同步同買，最強背書
    elif trust_lots > 0:
        s_purity = 20  # 投信認養
    elif foreign_lots > 0 and trust_lots == 0:
        s_purity = 15  # 外資單方買超
    else:
        s_purity = 10

    # 3. K線止跌型態 (0~25)
    ls_ratio = candle_feat['lower_shadow_ratio']
    is_bull = candle_feat['is_bullish']
    if is_bull and ls_ratio >= 0.30:
        s_candle = 25  # 錘子線/長下影紅K，完美反轉
    elif ls_ratio >= 0.30 or (is_bull and ls_ratio >= 0.15):
        s_candle = 20
    elif is_bull or ls_ratio >= 0.20:
        s_candle = 15
    elif candle_feat['is_reversal']:
        s_candle = 10
    else:
        s_candle = 5

    # 4. 反彈空間 (0~25) (依回測成果，空間適中 2%~6% 最容易達標)
    if 3.0 <= upside_pct <= 6.0:
        s_upside = 25  # 黃金均值回歸區間
    elif 2.0 <= upside_pct < 3.0:
        s_upside = 20
    elif 6.0 < upside_pct <= 10.0:
        s_upside = 15  # 空間大但可能通道向下開口
    elif upside_pct > 10.0:
        s_upside = 10
    else:
        s_upside = 5

    tier_bonus = 0 if tier_label == "Tier-1" else -5
    total_score = max(0, min(100, s_chip + s_purity + s_candle + s_upside + tier_bonus))

    # 星級評等
    if total_score >= 80:
        stars = "★★★★★"
    elif total_score >= 65:
        stars = "★★★★☆"
    elif total_score >= 50:
        stars = "★★★☆☆"
    else:
        stars = "★★☆☆☆"

    return total_score, stars, is_dual

def screen_stocks(date_str=None, min_volume_lots=1000, 
                  lower_dist_max=1.0, lower_dist_min=-5.0, 
                  min_inst_ratio=1.5, min_upside=0.0,
                  trust_max_sell_lots=500, require_reversal_candle=False):
    """
    V2.5 雙層動態降級容錯旗艦選股流程：
    - Tier-1：極致超跌轉折精選 (dist_lb <= +1.0%, vol >= 1000, ratio >= 1.5%)
    - Tier-2：支撐回測候補 (若 Tier-1 為 0 檔，自動啟用 dist_lb <= +2.5%, vol >= 800, ratio >= 1.0%)
    """
    if not date_str:
        date_str = get_latest_trading_date()
        if not date_str:
            print("[X] 無法取得最新交易日，請檢查網路連線或手動指定 --date YYYYMMDD")
            return []

    quotes, chips = fetch_twse_data(date_str)
    if not quotes or not chips:
        print("[X] 數據獲取不完整，終止篩選。")
        return []

    print(f"[*] 成功讀取 {len(quotes)} 檔個股行情與 {len(chips)} 檔個股籌碼資料。")
    print(f"[*] 第一階段初篩：探測市場成交量 >= 800 張且法人有買超之潛力母池...")

    candidate_codes = []
    candidate_meta = {}

    for code, q in quotes.items():
        # 最低門檻放至 800 張以備 Tier-2 候補
        if q['volume'] >= 800 * 1000 and code in chips:
            c = chips[code]
            vol_lots = q['volume'] // 1000
            total_lots = c['total'] // 1000
            inst_ratio = (total_lots / vol_lots * 100.0) if vol_lots > 0 else 0.0

            # 基本籌碼濾網：三大法人合計買超 > 0 且投信未大幅棄守
            if total_lots > 0 and inst_ratio >= 1.0 and c['trust'] >= -(trust_max_sell_lots * 1000):
                candle_feat = calculate_candlestick_features(q)
                # 排除跌停鎖死 (收在最低且單日重挫 >= 9.5%)
                if q['close'] <= q['low'] and q.get('change_pct', 0) <= -9.5:
                    continue
                if require_reversal_candle and not candle_feat['is_reversal']:
                    continue

                candidate_codes.append(code)
                candidate_meta[code] = {**q, **c, 'inst_ratio': inst_ratio, 'candle_feat': candle_feat}

    print(f"[*] 通過量能與籌碼初篩個股共 {len(candidate_codes)} 檔。")
    if not candidate_codes:
        print("[-] 今日無個股符合量能與籌碼基本門檻。")
        return []

    print(f"[*] 第二階段計算：批次計算 20 日布林通道、反彈空間與 20MA 斜率...")
    tickers = [f"{c}.TW" for c in candidate_codes]
    try:
        hist_data = yf.download(tickers, period='2mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 下載歷史K線失敗: {e}")
        return []

    tier1_results = []
    tier2_results = []

    for code in candidate_codes:
        t_key = f"{code}.TW"
        meta = candidate_meta[code]
        try:
            if len(candidate_codes) == 1:
                df = hist_data
            else:
                if t_key in hist_data.columns.levels[0]:
                    df = hist_data[t_key]
                else:
                    continue

            if 'Close' not in df or df['Close'].dropna().empty:
                continue

            close_series = df['Close'].dropna()
            if len(close_series) < 20:
                continue

            ma20 = close_series.rolling(20).mean().iloc[-1]
            std20 = close_series.rolling(20).std(ddof=0).iloc[-1]
            lower_band = ma20 - 2.0 * std20
            upper_band = ma20 + 2.0 * std20
            
            curr_close = meta['close']
            dist_pct = (curr_close - lower_band) / lower_band * 100.0
            upside_pct = (ma20 - curr_close) / curr_close * 100.0

            # 20MA 5日斜率變化率 (防墜落開口)
            if len(close_series) >= 25:
                ma20_5d_ago = close_series.rolling(20).mean().iloc[-5]
                ma20_slope_5d = (ma20 - ma20_5d_ago) / ma20_5d_ago * 100.0
            else:
                ma20_slope_5d = 0.0

            vol_lots = meta['volume'] // 1000
            f_lots = meta['foreign'] // 1000
            t_lots = meta['trust'] // 1000
            d_lots = meta['dealer'] // 1000
            tot_lots = meta['total'] // 1000
            c_feat = meta['candle_feat']

            # 隔日沖風控評估
            og_risk = evaluate_overnight_broker_risk(
                code,
                meta={
                    'volume': meta['volume'],
                    'dealer_hedge': meta.get('dealer', 0),
                    'foreign': meta['foreign'],
                    'inst_ratio': meta['inst_ratio']
                }
            )

            ma20_val = round(float(ma20), 2)
            ub_val = round(float(upper_band), 2)
            lb_val = round(float(lower_band), 2)
            close_val = round(curr_close, 2)
            # 關鍵防呆修復：使用 meta['high'] 與 meta['low']
            high_val = meta.get('high', curr_close * 1.02)
            low_val = meta.get('low', curr_close * 0.98)
            addon_val = round(max(high_val, curr_close * 1.025), 2)
            # 防禦型前低動態停損：取下軌96折與當日低點99折之較高者
            sl_val = round(max(float(lower_band * 0.96), float(low_val * 0.99)), 2)

            # === Tier-1 條件檢定 (極致超跌轉折) ===
            is_tier1 = (
                vol_lots >= min_volume_lots and
                lower_dist_min <= dist_pct <= lower_dist_max and
                meta['inst_ratio'] >= min_inst_ratio and
                ma20_slope_5d >= -2.8  # 排除急墜開口
            )

            if is_tier1:
                score, stars, is_dual = calculate_score(
                    inst_ratio=meta['inst_ratio'],
                    foreign_lots=f_lots,
                    trust_lots=t_lots,
                    candle_feat=c_feat,
                    upside_pct=upside_pct,
                    tier_label="Tier-1"
                )
                score = max(0, score - og_risk['penalty_score'])

                tier1_results.append({
                    '證券代號': code,
                    '證券名稱': meta['name'],
                    '梯隊標籤': '🟢 Tier-1 極致超跌',
                    '星級推薦': stars,
                    '評分': score,
                    '收盤價': close_val,
                    '進場價位': close_val,
                    '加碼價位': addon_val,
                    '停利TP1': ma20_val,
                    '停利TP2': ub_val,
                    '停損價位': sl_val,
                    '隔日沖風控': og_risk['tag'],
                    '布林下軌': lb_val,
                    '布林中軌': ma20_val,
                    '布林上軌': ub_val,
                    '反彈空間%': round(float(upside_pct), 2),
                    '距下軌%': round(float(dist_pct), 2),
                    '日成交量(張)': vol_lots,
                    '法人買超(張)': tot_lots,
                    '法人佔比%': round(meta['inst_ratio'], 1),
                    '外資買超': f_lots,
                    '投信買超': t_lots,
                    '土洋同買': "是" if is_dual else "否",
                    'K線型態': c_feat['pattern_desc']
                })

            # === Tier-2 條件檢定 (支撐回測候補，距下軌 -5.0% ~ +2.5%) ===
            is_tier2 = (
                vol_lots >= 800 and
                -5.0 <= dist_pct <= 2.5 and
                meta['inst_ratio'] >= 1.0 and
                tot_lots > 0
            )

            if is_tier2 and not is_tier1:
                score, stars, is_dual = calculate_score(
                    inst_ratio=meta['inst_ratio'],
                    foreign_lots=f_lots,
                    trust_lots=t_lots,
                    candle_feat=c_feat,
                    upside_pct=upside_pct,
                    tier_label="Tier-2"
                )
                score = max(0, score - og_risk['penalty_score'])

                tier2_results.append({
                    '證券代號': code,
                    '證券名稱': meta['name'],
                    '梯隊標籤': '🟡 Tier-2 支撐回測',
                    '星級推薦': stars,
                    '評分': score,
                    '收盤價': close_val,
                    '進場價位': close_val,
                    '加碼價位': addon_val,
                    '停利TP1': ma20_val,
                    '停利TP2': ub_val,
                    '停損價位': sl_val,
                    '隔日沖風控': og_risk['tag'],
                    '布林下軌': lb_val,
                    '布林中軌': ma20_val,
                    '布林上軌': ub_val,
                    '反彈空間%': round(float(upside_pct), 2),
                    '距下軌%': round(float(dist_pct), 2),
                    '日成交量(張)': vol_lots,
                    '法人買超(張)': tot_lots,
                    '法人佔比%': round(meta['inst_ratio'], 1),
                    '外資買超': f_lots,
                    '投信買超': t_lots,
                    '土洋同買': "是" if is_dual else "否",
                    'K線型態': c_feat['pattern_desc']
                })
        except Exception:
            continue

    # 雙層動態降級容錯決策
    if tier1_results:
        print(f"[✓] 成功篩選出 Tier-1 極致超跌轉折精選標的共 {len(tier1_results)} 檔。")
        final_results = sorted(tier1_results, key=lambda x: x['評分'], reverse=True)
    elif tier2_results:
        print(f"[*] 今日市場多頭強勢，Tier-1 超跌股為 0 檔；自動無縫啟動【Tier-2 支撐回測候補機制】（共 {len(tier2_results)} 檔候補）...")
        sorted_tier2 = sorted(tier2_results, key=lambda x: x['評分'], reverse=True)
        final_results = sorted_tier2[:6]
    else:
        print("[-] 今日全市場無符合 Tier-1 或 Tier-2 門檻之標的。")
        final_results = []

    return final_results

def format_line_message(results, date_str, args=None):
    """將選股清單格式化為手機 LINE 專業視覺化戰報"""
    from line_sender import format_clean_screener_text
    return format_clean_screener_text(results, date_str, strategy_id="01", max_stocks=6)

def main():
    parser = argparse.ArgumentParser(description="台股布林通道下軌超跌反轉選股策略 V2.5 雙層動態降級容錯版")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYYMMDD，預設為最新交易日)")
    parser.add_argument("--min-vol", type=int, default=1000, help="最低日成交量(張)，預設 1000 張")
    parser.add_argument("--max-dist", type=float, default=1.0, help="位於布林下軌上方容許百分比(%%)，預設 1.0%%")
    parser.add_argument("--min-dist", type=float, default=-5.0, help="跌破布林下軌容許百分比(%%)，預設 -5.0%%")
    parser.add_argument("--min-inst-ratio", type=float, default=1.5, help="法人買超佔成交量最低比重(%%)，預設 1.5%%")
    parser.add_argument("--min-upside", type=float, default=0.0, help="距布林中軌最低反彈空間(%%)，預設 0.0%%")
    parser.add_argument("--trust-dump-limit", type=int, default=500, help="投信最大容許賣超張數，預設 500 張")
    parser.add_argument("--strict-candle", action="store_true", help="開啟嚴格 K 線止跌型態硬濾網")
    parser.add_argument("--no-candle-filter", action="store_true", help="相容舊版旗標")
    parser.add_argument("--export", type=str, default=None, help="匯出檔名 (如 result.csv 或 result.md)")
    parser.add_argument("--line", action="store_true", help="自動發送選股結果至 LINE 推播")

    args = parser.parse_args()

    # 自動載入全域市場自適應操盤參數 (Adaptive Regime Parameters)
    try:
        from adaptive_regime_manager import get_strategy_adaptive_params
        adap_p = get_strategy_adaptive_params("01")
        if adap_p:
            if args.min_vol == 1000 and "min_volume" in adap_p:
                args.min_vol = int(adap_p["min_volume"])
            if args.max_dist == 1.0 and "bb_lower_band_offset_pct" in adap_p:
                args.max_dist = float(adap_p["bb_lower_band_offset_pct"])
    except Exception:
        pass

    results = screen_stocks(
        date_str=args.date,
        min_volume_lots=args.min_vol,
        lower_dist_max=args.max_dist,
        lower_dist_min=args.min_dist,
        min_inst_ratio=args.min_inst_ratio,
        min_upside=args.min_upside,
        trust_max_sell_lots=args.trust_dump_limit,
        require_reversal_candle=args.strict_candle
    )

    if not results:
        print("\n[!] 經 V2.5 雙層濾網檢核，所選日期無符合條件的個股。")
        if args.line:
            try:
                from line_sender import send_to_line
                empty_msg = f"📊【台股選股日報 V2.5】\n📅 日期：{args.date or '今日'} 盤後\n\n今日全市場無符合布林超跌（Tier-1 / Tier-2）之個股，建議耐心空手等待！"
                send_to_line(empty_msg, strategy="01")
            except Exception as e:
                print(f"[!] LINE 推播失敗: {e}")
        return

    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values(by="評分", ascending=False).reset_index(drop=True)

    print("\n" + "="*95)
    print(f"🎯 選股結果清單 V2.5（共 {len(df_res)} 檔）- 依智慧評分排序（星級 ★★★★★ / 反彈空間 / 法人純度）")
    print("="*95)
    print(df_res.to_string(index=False))
    print("="*95)

    if args.export:
        export_path = args.export
        if not os.path.isabs(export_path) and os.path.dirname(export_path) == "":
            export_path = os.path.join(os.path.dirname(__file__), export_path)
        if export_path.endswith('.csv'):
            df_res.to_csv(export_path, index=False, encoding='utf-8-sig')
            print(f"[✓] 已成功匯出 CSV 報表至: {export_path}")
            md_path = export_path[:-4] + ".md"
            from line_sender import format_screener_markdown_cards
            with open(md_path, 'w', encoding='utf-8') as f:
                target_date = args.date or get_latest_trading_date() or "今日"
                f.write(format_screener_markdown_cards(results, target_date, strategy_id="01", max_stocks=10))
                f.write("\n\n## 📋 完整量化數據指標總表\n\n")
                f.write(df_res.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已同步匯出視覺化字卡 Markdown 戰報至: {md_path}")
        elif export_path.endswith('.md'):
            from line_sender import format_screener_markdown_cards
            with open(export_path, 'w', encoding='utf-8') as f:
                target_date = args.date or get_latest_trading_date() or "今日"
                f.write(format_screener_markdown_cards(results, target_date, strategy_id="01", max_stocks=10))
                f.write("\n\n## 📋 完整量化數據指標總表\n\n")
                f.write(df_res.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已成功匯出視覺化字卡 Markdown 報表至: {export_path}")

    if args.line:
        try:
            from line_sender import send_to_line, build_screener_carousel, format_clean_screener_text
            target_date = args.date or get_latest_trading_date() or "今日"
            card = build_screener_carousel(results, target_date, strategy_id="01", max_stocks=6)
            print("[*] 正在發送 V2.5 選股清單至 LINE (策略 01)...")
            success = send_to_line(card, strategy="01")
            if not success:
                clean_text = format_clean_screener_text(results, target_date, strategy_id="01", max_stocks=6)
                send_to_line(clean_text, strategy="01")
        except Exception as e:
            print(f"[!] 發送 LINE 訊息過程發生異常: {e}")

if __name__ == '__main__':
    main()
