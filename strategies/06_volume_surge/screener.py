#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 06：盤中成交量異常爆量起漲動能選股器 (Volume Surge Momentum Screener)
================================================================================
本選股器嚴格遵循《總指揮中心規範》之 4+1 維度硬篩與二階段軟評分機制：
【一、硬性過濾條件 (Hard Filters - 不超過 5 個)】
  1. 流動性防守：當日成交量 >= 1,000 張 且 5日均量 >= 1,000 張，排除處置/全額交割/DR股。
  2. 趨勢位階線：收盤價 >= 20MA（月線），且月線走平或向上 (20MA >= 20MA_5日前)。
  3. 爆量突破動能：當日成交量 >= 2.5 倍 5日均量 (量比 Rvol >= 2.5)。
  4. 型態防守面：當日收紅K棒 (Close >= Open)，漲幅 >= +2.0%，
                 且實體比例 (Close - Low) / (High - Low) >= 0.60 (上影線佔比 < 40%)。
  5. 空間防追高：月線正乖離率 < 10.0%，確保於起漲初段介入，防止過度追高。

【二、第二階段軟評分矩陣 (Soft Scoring Matrix)】
  - 爆量強度 (最高 35 分)：Rvol >= 4.0 (+35分), >= 3.0 (+25分), >= 2.5 (+15分)
  - K線實體飽滿度 (最高 25 分)：SolidRatio >= 0.85 (+25分), >= 0.70 (+15分)
  - 法人動能 (最高 20 分)：外資或投信買超 >= 1,000 張 (+20分), >= 500 張 (+10分)
  - 週轉率健康度 (最高 20 分)：當日週轉率 5% ~ 18% (+20分)
  - 風控扣分 (algo_guards)：隔日沖分點高度扎堆扣 10~15 分，標註警示。

【三、實戰四大防線價位】
  - 🟢 建議進場價：收盤價 * 1.01 (買進上限)
  - 🔵 動能加碼價：最高價 * 1.005 (突破高點續強)
  - 🔴 停利目標價：TP1 +8%, TP2 +15%
  - 🛑 嚴格停損價：max(當日低點, 進場價 * 0.965)
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
from adaptive_regime_manager import get_strategy_adaptive_params, load_adaptive_config
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

STRATEGY_ID = "06"
STRATEGY_NAME = "策略 06：盤中成交量異常爆量起漲動能"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')
MD_FILE = os.path.join(os.path.dirname(__file__), 'result.md')


def get_latest_trading_date(max_days_back=15) -> str:
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
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('stat') == 'OK' and len(data.get('data', [])) > 0:
                    return d_str
        except Exception:
            pass
    return None


def fetch_twse_market_data(date_str: str):
    """
    抓取證交所當日每日收盤行情 (MI_INDEX) 與三大法人買賣超 (T86)
    """
    print(f"[*] 正在連線台灣證券交易所抓取 {date_str} 交易數據...")
    chips = {}
    quotes = {}

    # 1. 抓取三大法人 (T86)
    url_t86 = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json'
    req_t86 = urllib.request.Request(url_t86, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req_t86, timeout=10) as resp:
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
        print(f"[!] 抓取三大法人失敗: {e}")

    # 2. 抓取收盤行情 (MI_INDEX)
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
                                vol = int(row[2].replace(',', ''))
                                open_p = row[5].replace(',', '').strip()
                                high_p = row[6].replace(',', '').strip()
                                low_p = row[7].replace(',', '').strip()
                                close_p = row[8].replace(',', '').strip()
                                if close_p != '--' and open_p != '--':
                                    quotes[code] = {
                                        'name': name,
                                        'volume': vol,
                                        'open': float(open_p),
                                        'high': float(high_p),
                                        'low': float(low_p),
                                        'close': float(close_p)
                                    }
                            except (ValueError, IndexError):
                                pass
    except Exception as e:
        print(f"[!] 抓取收盤行情失敗: {e}")

    return quotes, chips


def calculate_candle_metrics(open_p: float, high_p: float, low_p: float, close_p: float) -> dict:
    """計算 K 線實體比例與型態健康度"""
    amplitude = max(high_p - low_p, 0.001)
    solid_ratio = (close_p - low_p) / amplitude
    upper_shadow = max(high_p - max(open_p, close_p), 0.0)
    upper_shadow_ratio = upper_shadow / amplitude
    is_bullish = close_p >= open_p
    
    return {
        'amplitude': amplitude,
        'solid_ratio': solid_ratio,
        'upper_shadow_ratio': upper_shadow_ratio,
        'is_bullish': is_bullish
    }


def calculate_score_and_stars(rvol: float, solid_ratio: float, foreign_lots: int, trust_lots: int,
                              broker_penalty: int = 0, curr_vol_lots: int = 0, return_quality: bool = False) -> tuple:
    """
    第二階段軟評分矩陣 (滿分 100 分)
    包含爆量強度、K線實體飽滿度、法人籌碼動能與吃貨品質指標
    回傳: (total_score, stars) 或 (total_score, stars, quality_tag)
    """
    # 1. 爆量強度 (0~35 分)
    if rvol >= 4.0:
        s_vol = 35
    elif rvol >= 3.0:
        s_vol = 25
    elif rvol >= 2.5:
        s_vol = 15
    else:
        s_vol = 5

    # 2. 實體飽滿度 (0~25 分)
    if solid_ratio >= 0.85:
        s_candle = 25
    elif solid_ratio >= 0.70:
        s_candle = 15
    else:
        s_candle = 5

    # 3. 法人動能與吃貨品質評估 (0~20 分 + 品質調節)
    inst_lots = foreign_lots + trust_lots
    absorption_ratio = (inst_lots / curr_vol_lots) if curr_vol_lots > 0 else 0.0

    abs_pct = round(absorption_ratio * 100.0, 2)
    if inst_lots >= 1000 and abs_pct >= 12.0:
        s_inst = 20
        quality_tag = "🟢 法人吃貨鎖碼"
    elif inst_lots >= 1000:
        s_inst = 18
        quality_tag = "🟢 法人大額買超"
    elif inst_lots >= 500:
        s_inst = 15
        quality_tag = "🔵 法人溫和吃貨"
    elif inst_lots > 0:
        if abs_pct <= 10.0:
            s_inst = 5
            quality_tag = "⚖️ 良性換手整理"
        else:
            s_inst = 10
            quality_tag = "⚪ 法人小幅偏多"
    elif inst_lots <= -250 or abs_pct <= -5.0:
        s_inst = -5
        quality_tag = "⚠️ 主力調節倒貨"
    else:
        s_inst = 0
        quality_tag = "⚪ 內資散戶換手"

    # 4. 基礎動態權重 (20 分)
    s_base = 20

    raw_score = s_vol + s_candle + s_inst + s_base
    final_score = max(0, min(100, raw_score - broker_penalty))

    if final_score >= 85:
        stars = "★★★★★"
    elif final_score >= 70:
        stars = "★★★★☆"
    elif final_score >= 55:
        stars = "★★★☆☆"
    else:
        stars = "★★☆☆☆"

    quality_info = {
        'quality_tag': quality_tag,
        'quality_label': quality_tag,
        'quality_bonus': s_inst,
        'inst_lots': inst_lots,
        'inst_absorption_ratio': round(absorption_ratio * 100.0, 2)
    }

    if return_quality:
        return final_score, stars, quality_info
    return final_score, stars


def screen_volume_surge(date_str: str = None, min_volume_lots: int = 1000, min_rvol: float = None, max_bias_pct: float = None):
    """
    執行策略 06 盤後選股主流程 (V2.0 旗艦自適應版)
    """
    # 載入全域大盤自適應操盤參數
    adaptive_cfg = load_adaptive_config()
    adaptive_params = get_strategy_adaptive_params("06")
    regime_info = adaptive_cfg.get("market_regime", {})
    global_ctrl = adaptive_cfg.get("global_controls", {})

    effective_rvol_min = min_rvol if min_rvol is not None else float(adaptive_params.get("volume_surge_multiplier", 2.5))
    effective_bias_max = max_bias_pct if max_bias_pct is not None else float(adaptive_params.get("bias_max_pct", 10.0))
    adaptive_tp1_pct = float(adaptive_params.get("tp1_pct", 8.0))
    adaptive_tp2_pct = float(adaptive_params.get("tp2_pct", 15.0))
    adaptive_sl_pct = float(adaptive_params.get("stop_loss_pct", -4.5))
    rec_limit = int(adaptive_params.get("recommendation_limit", 4))

    if not date_str:
        date_str = get_latest_trading_date()
        if not date_str:
            print("[X] 無法取得證交所最新交易日期，請檢查網路或手動指定 --date YYYYMMDD")
            return []

    print(f"============================================================")
    print(f"🚀 開始執行【{STRATEGY_NAME}】選股流程 (基準日: {date_str})")
    print(f"📡 當前大盤位階：{regime_info.get('label', '標準箱型')} ｜ 操盤模式：{global_ctrl.get('mode', '標準')}")
    print(f"⚙️ 自適應參數：量比低標 {effective_rvol_min}x ｜ 乖離上限 {effective_bias_max}% ｜ TP1 +{adaptive_tp1_pct}% ｜ TP2 +{adaptive_tp2_pct}% ｜ 停損 {adaptive_sl_pct}%")
    print(f"============================================================")

    quotes, chips = fetch_twse_market_data(date_str)
    if not quotes:
        print("[X] 行情數據為空，結束篩選。")
        return []

    # 第一階段初步粗篩：當日成交量 >= 1,000 張 且收紅K 且漲幅 >= 2.0%
    min_vol_shares = min_volume_lots * 1000
    preliminary_codes = []

    for code, q in quotes.items():
        if q['volume'] >= min_vol_shares:
            # 檢查收紅與初階漲幅
            chg_pct = ((q['close'] - q['open']) / q['open']) * 100.0 if q['open'] > 0 else 0.0
            if q['close'] >= q['open'] and chg_pct >= 1.5:
                candle_m = calculate_candle_metrics(q['open'], q['high'], q['low'], q['close'])
                # 上影線不可過長 (上影線比例 < 40%)
                if candle_m['solid_ratio'] >= 0.60:
                    preliminary_codes.append(code)

    print(f"[*] 初步形態與量能初篩通過：{len(preliminary_codes)} 檔。正在下載歷史 K 線以計算 5MA 與 20MA...")

    if not preliminary_codes:
        print("[-] 今日無標的符合初步門檻。")
        return []

    tickers = [f"{c}.TW" for c in preliminary_codes]
    try:
        hist_data = yf.download(tickers, period='2mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 下載 Yahoo 歷史數據失敗: {e}")
        return []

    qualified_stocks = []

    for code in preliminary_codes:
        t_key = f"{code}.TW"
        q = quotes[code]
        try:
            if len(preliminary_codes) == 1:
                df = hist_data
            else:
                if t_key in hist_data.columns.levels[0]:
                    df = hist_data[t_key]
                else:
                    continue

            if 'Close' not in df or df['Close'].dropna().empty:
                continue

            close_series = df['Close'].dropna()
            vol_series = df['Volume'].dropna()
            if len(close_series) < 20 or len(vol_series) < 5:
                continue

            # 計算 20MA 與 5MA Volume
            ma20_now = float(close_series.rolling(20).mean().iloc[-1])
            ma20_prev = float(close_series.rolling(20).mean().iloc[-5])
            v5_avg = float(vol_series.rolling(5).mean().iloc[-1]) // 1000  # 張

            if v5_avg < min_volume_lots:
                continue

            curr_vol_lots = q['volume'] // 1000
            rvol = round(curr_vol_lots / v5_avg, 2) if v5_avg > 0 else 0.0

            # 硬篩 3：爆量突破動能 Rvol >= effective_rvol_min
            if rvol < effective_rvol_min:
                continue

            curr_close = q['close']
            # 硬篩 2：站上月線且月線向上或走平
            if curr_close < ma20_now or ma20_now < (ma20_prev * 0.995):
                continue

            # 硬篩 5：月線正乖離率 < effective_bias_max (自適應防追高)
            bias_pct = round(((curr_close - ma20_now) / ma20_now) * 100.0, 2)
            if bias_pct >= effective_bias_max:
                continue

            candle_m = calculate_candle_metrics(q['open'], q['high'], q['low'], q['close'])
            # 硬篩 4：實體紅棒比例 >= 0.60
            if candle_m['solid_ratio'] < 0.60:
                continue

            # 籌碼面與隔日沖風控
            c_info = chips.get(code, {'foreign': 0, 'trust': 0, 'dealer': 0, 'total': 0})
            f_lots = c_info['foreign'] // 1000
            t_lots = c_info['trust'] // 1000
            inst_lots = c_info['total'] // 1000

            broker_guard = evaluate_overnight_broker_risk(
                code=code,
                meta={
                    'volume': curr_vol_lots,
                    'foreign': f_lots,
                    'dealer_hedge': c_info['dealer'] // 1000,
                    'close': curr_close
                }
            )

            penalty = broker_guard.get('penalty_score', 0)
            score, stars, quality_tag = calculate_score_and_stars(
                rvol=rvol,
                solid_ratio=candle_m['solid_ratio'],
                foreign_lots=f_lots,
                trust_lots=t_lots,
                broker_penalty=penalty,
                curr_vol_lots=curr_vol_lots,
                return_quality=True
            )

            # 四大實戰價位精算 (自適應動態定價，嚴守數學單調性)
            entry_p = round(curr_close * 1.01, 2)       # 建議進場買進上限
            addon_p = round(max(q['high'] * 1.005, entry_p + 0.05), 2)  # 突破今日高點動能加碼
            tp1_mult = 1.0 + (adaptive_tp1_pct / 100.0)
            tp2_mult = 1.0 + (adaptive_tp2_pct / 100.0)
            sl_pct_abs = abs(adaptive_sl_pct)
            sl_mult = 1.0 - (sl_pct_abs / 100.0)

            tp1_p = round(max(curr_close * tp1_mult, addon_p + 0.05), 2)
            tp2_p = round(max(curr_close * tp2_mult, tp1_p + 0.05), 2)
            sl_p = round(min(curr_close * 0.985, max(q['low'], curr_close * sl_mult)), 2)

            # 錦囊指引
            playbook_cash = f"【自適應{global_ctrl.get('mode', '標準')}】於 {curr_close}~{entry_p} 區間掛單，超過 {entry_p} 嚴禁追高。"
            playbook_hold = f"以 {sl_p} 為嚴格防守底線，突破 {addon_p} 動態加碼，攻抵 {tp1_p} 先獲利落袋 1/3~1/2。"

            qualified_stocks.append({
                '證券代號': code,
                '證券名稱': q['name'],
                '收盤價': curr_close,
                '當日漲跌%': round(((curr_close - q['open']) / q['open']) * 100.0, 2),
                '成交量(張)': curr_vol_lots,
                '5日均量(張)': int(v5_avg),
                '量比倍數(Rvol)': rvol,
                '實體紅棒比例': round(candle_m['solid_ratio'], 2),
                '月線(20MA)': round(ma20_now, 2),
                '月線乖離%': bias_pct,
                '外資買超(張)': f_lots,
                '投信買超(張)': t_lots,
                '換手品質': quality_tag,
                '綜合評分': score,
                '星級推薦': stars,
                '隔日沖警示': broker_guard.get('tag', '安全・無顯著集中'),
                '大盤位階': regime_info.get('label', '標準箱型'),
                '建議進場價': entry_p,
                '動能加碼價': addon_p,
                '停利TP1': tp1_p,
                '停利TP2': tp2_p,
                '嚴格停損價': sl_p,
                '空手者策略': playbook_cash,
                '持股者策略': playbook_hold
            })
        except Exception as e:
            continue

    qualified_stocks.sort(key=lambda x: (x['綜合評分'], x['量比倍數(Rvol)']), reverse=True)
    print(f"[✓] 最終入選【{STRATEGY_NAME}】共 {len(qualified_stocks)} 檔標的。")

    return qualified_stocks


def export_reports(stocks: list, csv_path: str = CSV_FILE, md_path: str = MD_FILE):
    """匯出 UTF-8-BOM CSV 與高對比字卡 Markdown 戰報 (附自適應大盤位階)"""
    adaptive_cfg = load_adaptive_config()
    regime_info = adaptive_cfg.get("market_regime", {})
    global_ctrl = adaptive_cfg.get("global_controls", {})
    adaptive_params = get_strategy_adaptive_params("06")
    rec_limit = int(adaptive_params.get("recommendation_limit", 6))

    if not stocks:
        empty_cols = ['證券代號', '證券名稱', '收盤價', '當日漲跌%', '成交量(張)', '5日均量(張)',
                      '量比倍數(Rvol)', '換手品質', '建議進場價', '動能加碼價', '停利TP1', '停利TP2', '嚴格停損價']
        df_empty = pd.DataFrame(columns=empty_cols)
        from rolling_retention_manager import save_rolling_result_csv
        save_rolling_result_csv(csv_path, df_empty, today_date_str=datetime.date.today().strftime('%Y/%m/%d'), max_days=5)
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(f"# 【{STRATEGY_NAME}】最新選股戰報\n\n")
            f.write(f"> [!NOTE]\n> **當前大盤位階**：{regime_info.get('label', '標準箱型')} ｜ **操盤模式**：{global_ctrl.get('mode', '標準')} ｜ **建議現金水位**：{global_ctrl.get('cash_target_pct', 40)}%\n\n")
            f.write("今日無標的符合 4+1 維度爆量條件，建議保留現金耐心觀望。\n")
        return

    df = pd.DataFrame(stocks)
    from rolling_retention_manager import save_rolling_result_csv
    df = save_rolling_result_csv(csv_path, df, today_date_str=today_str, max_days=5)
    print(f"[✓] 已成功匯出 CSV 報表至: {csv_path}")

    today_str = datetime.date.today().strftime('%Y/%m/%d')

    # 產出標準 Markdown 字卡
    cards_md = format_screener_markdown_cards(
        results=stocks[:rec_limit],
        date_str=today_str,
        strategy_id=STRATEGY_ID,
        max_stocks=rec_limit
    )
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# 【{STRATEGY_NAME}】盤後選股戰訊報表\n\n")
        f.write(f"產出時間：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"> [!NOTE]\n> **當前大盤位階**：{regime_info.get('label', '標準箱型')} ｜ **自適應模式**：{global_ctrl.get('mode', '標準')} ｜ **建議現金水位**：{global_ctrl.get('cash_target_pct', 40)}%\n")
        f.write(f"> **量化動態參數**：量比門檻 {adaptive_params.get('volume_surge_multiplier', 2.5)}x ｜ 追高乖離上限 {adaptive_params.get('bias_max_pct', 10.0)}% ｜ TP1 +{adaptive_params.get('tp1_pct', 8.0)}% ｜ SL {adaptive_params.get('stop_loss_pct', -4.5)}%\n\n")
        f.write(cards_md)
        f.write("\n\n## 完整入選量化數據明細表\n\n")
        f.write(df.to_markdown(index=False))
        f.write("\n")
    print(f"[✓] 已成功匯出 Markdown 戰報至: {md_path}")


def push_to_line(stocks: list, date_str: str = None):
    """使用 line_sender 推播橫向輪播卡片與純文字備援至策略 06 專屬群組"""
    if not stocks:
        msg = f"📊【{STRATEGY_NAME}】今日盤後選股無符合條件標的。"
        send_to_line(msg, strategy=STRATEGY_ID)
        return

    today_str = date_str or datetime.date.today().strftime('%Y/%m/%d')
    adaptive_params = get_strategy_adaptive_params("06")
    rec_limit = int(adaptive_params.get("recommendation_limit", 6))
    top_stocks = stocks[:rec_limit]

    carousel = build_screener_carousel(
        results=top_stocks,
        date_str=today_str,
        strategy_id=STRATEGY_ID,
        max_stocks=rec_limit
    )

    clean_text = format_clean_screener_text(
        results=top_stocks,
        date_str=today_str,
        strategy_id=STRATEGY_ID,
        max_stocks=rec_limit
    )

    print(f"[*] 正在推播 {len(top_stocks)} 檔爆量起漲精華至 LINE (strategy={STRATEGY_ID})...")
    success = send_to_line(carousel, strategy=STRATEGY_ID)
    if not success:
        send_to_line(clean_text, strategy=STRATEGY_ID)


def main():
    parser = argparse.ArgumentParser(description=f"{STRATEGY_NAME} 盤後選股器 (V2.0 旗艦自適應版)")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (YYYYMMDD)")
    parser.add_argument("--max-bias", type=float, default=None, help="20MA 正乖離上限百分比(%%)，留空則自動載入自適應參數")
    parser.add_argument("--min-rvol", type=float, default=None, help="最低量比倍數門檻，留空則自動載入自適應參數")
    parser.add_argument("--line", action="store_true", help="執行後推播 LINE 戰報")
    parser.add_argument("--export", type=str, default=CSV_FILE, help="匯出 CSV 路徑")
    args = parser.parse_args()

    results = screen_volume_surge(
        date_str=args.date,
        max_bias_pct=args.max_bias,
        min_rvol=args.min_rvol
    )
    export_reports(results, csv_path=args.export, md_path=MD_FILE)

    if args.line:
        push_to_line(results)


if __name__ == "__main__":
    main()
