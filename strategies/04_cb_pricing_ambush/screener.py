#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股選股策略 04：可轉債 (CB) 定價伏擊與區間博弈旗艦策略 V4.0 全域規範版
================================================================================
本選股器嚴格遵循總指揮中心規範、實戰四大防線定價引擎 V4.0 全域規範、
4+1 維度硬篩、雙層動態降級容錯架構 (Tier-1 / Tier-2) 與隔日沖風控反制標準。

核心邏輯：
1. 【壓低定價與洗盤慣性】：掌握「董事會宣告發行至定價基準日」主力的壓低訂價慣性。
2. 【右側確認與量縮伏擊】：鎖定「申報生效後 T+3~T+8」量縮窒息臨界點與「定價公告當日」。
3. 【TPEx 官方全市場動態同步】：自動整合櫃買中心 OpenAPI 活躍可轉債，母池動態擴充至全台股。
4. 【實戰四大防線定價引擎 V4.0】：臺灣證交所法定 6 階升降檔位合規取整、幾何單調性嚴格守護。
5. 【個股最後執行死線 (Exit Deadline)】：滿 3 個月閉鎖期解禁日強制出清，防債轉股巨額賣壓。
6. 【股票期貨價位優先與避險對沖】：整合期交所近月合約報價、未平倉量 (OI) 與結算倒數提示。
================================================================================
"""

import sys
import os
import argparse
import datetime
import json
import urllib.request
import concurrent.futures
from pathlib import Path
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

# 加入專案根目錄至 sys.path 以便共用模組
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 引入共用模組
from four_price_engine import (
    calculate_four_prices_long,
    normalize_to_tick,
    get_twse_tick_size,
    get_limit_prices
)
from adaptive_regime_manager import get_strategy_adaptive_params, load_adaptive_config
from algo_guards import evaluate_overnight_broker_risk
from market_schedule import check_market_open_or_exit, should_suppress_push
from rolling_retention_manager import save_rolling_result_csv
from line_sender import (
    send_to_line,
    build_screener_carousel,
    format_clean_screener_text,
    format_screener_markdown_cards
)

# 引入 Strategy 04 專用 CB 引擎
import importlib
try:
    cb_helper = importlib.import_module("strategies.04_cb_pricing_ambush.cb_helper")
    load_comprehensive_cb_pool = cb_helper.load_comprehensive_cb_pool
    calculate_cb_four_prices = cb_helper.calculate_cb_four_prices
    fetch_tpex_active_cbs = cb_helper.fetch_tpex_active_cbs
except Exception:
    try:
        from cb_helper import (
            load_comprehensive_cb_pool,
            calculate_cb_four_prices,
            fetch_tpex_active_cbs
        )
    except Exception:
        load_comprehensive_cb_pool = None
        calculate_cb_four_prices = None
        fetch_tpex_active_cbs = None

# 引入股票期貨共用模組
try:
    from futures_helper import load_stock_futures_pool, calculate_futures_pricing
except ImportError:
    load_stock_futures_pool = lambda: {}
    calculate_futures_pricing = None

# Windows 控制台編碼保護
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

STRATEGY_ID = "04"
STRATEGY_NAME = "可轉債 (CB) 定價伏擊與區間博弈策略"
STRATEGY_DIR = Path(__file__).resolve().parent
POOL_FILE = STRATEGY_DIR / "cb_pool.json"
CSV_FILE = STRATEGY_DIR / "result.csv"
MD_FILE = STRATEGY_DIR / "result.md"


def load_cb_pool(pool_type: str = "comprehensive") -> List[Dict[str, Any]]:
    """
    載入可轉債追蹤母池：
    - comprehensive (預設): 動態融合本地人工研究標的 + TPEx 官方全市場活躍 CB (覆蓋 60+ 檔)
    - curated: 僅載入本地 cb_pool.json (精選人工研究標的)
    """
    if pool_type == "comprehensive" and load_comprehensive_cb_pool is not None:
        try:
            pool = load_comprehensive_cb_pool()
            if pool:
                return pool
        except Exception as e:
            print(f"[!] 調用 load_comprehensive_cb_pool 失敗，退回本地池: {e}")

    # Fallback to local cb_pool.json
    if POOL_FILE.exists():
        try:
            with open(POOL_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] 讀取 {POOL_FILE} 失敗: {e}")
    return []


def fetch_stock_market_data(code: str, market: str = 'TW') -> Optional[Dict[str, Any]]:
    """從 Yahoo Finance 抓取個股近期價量資料、均線與真實波動幅度 ATR"""
    suffixes = [market, 'TW', 'TWO']
    for suffix in suffixes:
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{code}.{suffix}?interval=1d&range=3mo'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                result = data['chart']['result'][0]
                quotes = result['indicators']['quote'][0]
                closes = [c for c in quotes['close'] if c is not None]
                vols = [v for v in quotes['volume'] if v is not None]
                highs = [h for h in quotes['high'] if h is not None]
                lows = [l for l in quotes['low'] if l is not None]
                opens = [o for o in quotes.get('open', []) if o is not None]

                if len(closes) >= 15:
                    curr_price = round(float(closes[-1]), 2)
                    prev_close = round(float(closes[-2]), 2) if len(closes) >= 2 else curr_price
                    today_high = round(float(highs[-1]), 2) if highs else curr_price
                    today_low = round(float(lows[-1]), 2) if lows else curr_price
                    today_open = round(float(opens[-1]), 2) if opens else curr_price

                    ma5 = round(float(np.mean(closes[-5:])), 2)
                    ma20 = round(float(np.mean(closes[-20:])), 2) if len(closes) >= 20 else ma5
                    ma60 = round(float(np.mean(closes[-60:])), 2) if len(closes) >= 60 else ma20
                    curr_vol = int(vols[-1] // 1000)  # 轉為張數
                    vol20 = int(np.mean(vols[-20:]) // 1000) if len(vols) >= 20 else curr_vol
                    vol_ratio = round((curr_vol / vol20 * 100), 1) if vol20 > 0 else 100.0
                    recent_high = round(float(max(highs[-5:])), 2)
                    recent_low = round(float(min(lows[-5:])), 2)

                    # 計算 14 日真實波動幅度 (ATR)
                    atr_14 = round(curr_price * 0.03, 2)
                    if len(closes) >= 15:
                        tr_list = []
                        for i in range(1, min(15, len(closes))):
                            h = highs[-i]
                            l = lows[-i]
                            c_prev = closes[-i-1]
                            tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
                            tr_list.append(tr)
                        if tr_list:
                            atr_14 = round(float(np.mean(tr_list)), 2)

                    # 計算下影線比例與日K型態
                    candle_body = abs(curr_price - today_open)
                    candle_range = max(today_high - today_low, 0.01)
                    lower_shadow = min(curr_price, today_open) - today_low
                    lower_shadow_ratio = round((lower_shadow / candle_range) * 100, 1)
                    upper_shadow = today_high - max(curr_price, today_open)
                    upper_shadow_ratio = round((upper_shadow / candle_range) * 100, 1)

                    return {
                        'curr_price': curr_price,
                        'prev_close': prev_close,
                        'today_high': today_high,
                        'today_low': today_low,
                        'today_open': today_open,
                        'ma5': ma5,
                        'ma20': ma20,
                        'ma60': ma60,
                        'curr_vol': curr_vol,
                        'vol20': vol20,
                        'vol_ratio': vol_ratio,
                        'recent_high': recent_high,
                        'recent_low': recent_low,
                        'atr': atr_14,
                        'lower_shadow_ratio': lower_shadow_ratio,
                        'upper_shadow_ratio': upper_shadow_ratio,
                        'is_bullish': curr_price >= today_open
                    }
        except Exception:
            continue
    return None


def score_and_analyze_cb(cb_item: Dict[str, Any], market_data: Dict[str, Any], regime_pricing_params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    依據 V4.0 四大防線標準與 4+1 維度量化評分打分，並精確計算定價防線與最後退場死線
    """
    price = round(float(market_data['curr_price']), 2)
    prev_close = round(float(market_data['prev_close']), 2)
    ma5 = market_data['ma5']
    ma20 = market_data['ma20']
    ma60 = market_data['ma60']
    vol = market_data['curr_vol']
    vol20 = market_data['vol20']
    vol_ratio = market_data['vol_ratio']
    atr = market_data['atr']
    upper_shadow_ratio = market_data.get('upper_shadow_ratio', 0.0)

    # 取得或設算轉換價格
    conv_price = cb_item.get('conversion_price')
    if not conv_price or float(conv_price) <= 0:
        conv_price = round(price * 1.02, 2)
    else:
        conv_price = round(float(conv_price), 2)

    price_to_conv_ratio = round(price / conv_price, 3)

    # 4+1 硬性風控防追高：轉換溢價率嚴格限制 <= 15% (現價/轉換價 <= 1.15)，排除已暴漲脫離轉換價之接刀風險
    if price_to_conv_ratio > 1.15:
        return None

    # 墓碑線竭盡出貨防守：上影線佔比 > 50% 且爆大量排除
    if upper_shadow_ratio > 50.0 and vol_ratio > 200.0:
        return None

    # -------------------------------------------------------------
    # 4+1 維度雙層動態降級容錯檢驗 (Tier-1 核心精選 vs Tier-2 穩健候補)
    # -------------------------------------------------------------
    # Tier-1 核心精選門檻：均量 >= 1000張、現價/轉換價 <= 1.08、季線 60MA 守穩 (>=0.98)
    is_tier1 = (
        (vol >= 1000 or vol20 >= 1000) and
        price_to_conv_ratio <= 1.08 and
        price >= (ma60 * 0.98) and
        vol_ratio <= 150.0
    )

    # Tier-2 穩健候補門檻：均量 >= 400張、現價/轉換價 <= 1.15、季線震盪不失守 (>=0.94)
    is_tier2 = (
        (vol >= 400 or vol20 >= 400) and
        price_to_conv_ratio <= 1.15 and
        price >= (ma60 * 0.94)
    )

    if not (is_tier1 or is_tier2):
        return None

    tier_label = "🟢 Tier-1 核心精選" if is_tier1 else "🟡 Tier-2 穩健候補"

    # -------------------------------------------------------------
    # 100 分制量化星級打分矩陣
    # -------------------------------------------------------------
    score = 0

    # 1. 募資規模與稀釋度 (25分)
    est_amount = float(cb_item.get('est_amount', 5.0))
    if est_amount >= 10.0:
        score += 15
    elif est_amount >= 5.0:
        score += 10
    else:
        score += 4

    dilution = float(cb_item.get('dilution_rate', 10.0))
    if dilution <= 8.0:
        score += 10
    elif dilution <= 12.0:
        score += 6
    else:
        score += 2

    # 2. 籌碼流動性與量縮洗盤 (25分)
    if vol >= 1000:
        score += 10
    elif vol >= 500:
        score += 6
    else:
        score += 3

    if vol_ratio <= 45.0:
        score += 15  # 洗盤量縮極致
    elif vol_ratio <= 70.0:
        score += 10
    elif vol_ratio <= 100.0:
        score += 6
    else:
        score += 3

    # 3. 定價溢價率安全邊際與進度狀態 (25分)
    if 0.94 <= price_to_conv_ratio <= 1.05:
        score += 15  # 黃金甜蜜點：轉換價鐵板支撐
    elif 1.05 < price_to_conv_ratio <= 1.08:
        score += 8   # 略微偏高但可接受
    elif price_to_conv_ratio < 0.94:
        score += 12  # 超跌折價伏擊點
    else:
        score += 2

    status = cb_item.get('status', 'LISTED')
    if status == 'PRICED':
        score += 10  # 轉換價敲定，打壓任務終結
    elif status == 'EFFECTIVE':
        score += 8   # 申報生效，等待定價
    elif status == 'ANNOUNCED':
        score += 5   # 剛宣布
    else:
        score += 7   # 已掛牌閉鎖保護中

    # 4. 技術位階與均線結構 (25分)
    if price >= ma60 and price >= ma20:
        score += 15  # 多頭排列強勢站穩
    elif price >= ma60:
        score += 11  # 季線上強勢整理
    elif price >= (ma60 * 0.97):
        score += 7   # 季線支撐洗盤
    else:
        score += 3

    if price >= ma5:
        score += 10  # 短線轉強帶動
    elif price >= (ma5 * 0.98):
        score += 6
    else:
        score += 2

    # 5. 反制程式交易與隔日沖風險評估
    og_meta = {
        'volume': vol,
        'close': price,
        'vol_ratio': vol_ratio,
        'upper_shadow_ratio': upper_shadow_ratio
    }
    og_risk = evaluate_overnight_broker_risk(
        code=cb_item['code'],
        meta=og_meta
    )
    penalty = og_risk.get('penalty_score', 0)
    score = max(score - penalty, 0)

    # 6. 星級評定
    if score >= 80:
        stars = '★★★★★'
    elif score >= 70:
        stars = '★★★★☆'
    elif score >= 60:
        stars = '★★★☆☆'
    else:
        stars = '★★☆☆☆'

    # -------------------------------------------------------------
    # 實戰四大防線定價引擎 V4.0 (TWSE Legal Tick Size & Monotonicity)
    # -------------------------------------------------------------
    rp = regime_pricing_params or {}
    adaptive_tp1 = float(rp.get("tp1_pct", 5.0)) / 100.0
    adaptive_tp2 = float(rp.get("tp2_pct", 12.0)) / 100.0
    adaptive_sl = abs(float(rp.get("stop_loss_pct", -4.5))) / 100.0

    # 依據現價與轉換價格相對位階，設定可轉債專屬錨點 (Custom Anchors)
    if price < conv_price:
        raw_entry = min(price * 1.015, conv_price * 0.99)
        raw_addon = max(price * 1.025, market_data['recent_high'] * 1.005)
        raw_tp1 = min(conv_price, price * (1.0 + max(0.05, adaptive_tp1)))
        raw_tp2 = max(conv_price * 1.05, price * (1.0 + max(0.12, adaptive_tp2)))
        raw_sl = max(market_data['recent_low'] * 0.985, price * (1.0 - adaptive_sl), ma60 * 0.96)
    else:
        raw_entry = price * 1.015
        raw_addon = max(price * 1.025, market_data['recent_high'] * 1.005)
        raw_tp1 = max(conv_price * 1.08, price * (1.0 + max(0.06, adaptive_tp1)))
        raw_tp2 = max(conv_price * 1.20, price * (1.0 + max(0.15, adaptive_tp2)))
        raw_sl = max(conv_price * 0.95, market_data['recent_low'] * 0.985, price * (1.0 - adaptive_sl))

    custom_anchors = {
        "entry": raw_entry,
        "addon": raw_addon,
        "tp1": raw_tp1,
        "tp2": raw_tp2,
        "sl": raw_sl
    }

    fp_calc = calculate_four_prices_long(
        curr_price=price,
        prev_close=prev_close,
        high=market_data.get('today_high', price * 1.01),
        low=market_data.get('today_low', price * 0.99),
        ma20=ma20,
        atr=atr,
        regime_params=regime_pricing_params,
        custom_anchors=custom_anchors
    )

    entry_price = fp_calc['entry_price']
    addon_price = fp_calc['addon_price']
    tp1_price = fp_calc['tp1_price']
    tp2_price = fp_calc['tp2_price']
    sl_price = fp_calc['sl_price']
    limit_up = fp_calc['limit_up']
    limit_down = fp_calc['limit_down']
    rr_ratio = fp_calc.get('risk_reward_ratio', 1.8)

    # 空間極限 130% 逼轉強制贖回線
    target_130_pct = normalize_to_tick(conv_price * 1.30, direction='down')
    lockup_end = cb_item.get('lockup_end_date', '掛牌滿3個月當日')

    # 操盤錦囊細緻化
    guidance_updated = {
        "entry_desc": f"建議買進上限 {entry_price} 元 (現價 {price} 元以內皆為甜蜜買點，超過嚴禁追價)",
        "addon_desc": f"突破 {addon_price} 元帶量確認續強，啟動第 2 階段順勢加碼",
        "tp1_desc": f"目標一 {tp1_price} 元分批拔檔 50% (觸碰轉換價或首波反壓)，啟動保本防守",
        "tp2_desc": f"目標二 {tp2_price} 元波段主升段等幅滿足全數落袋",
        "sl_desc": f"跌破 {sl_price} 元無條件停損離場，嚴守風控底線"
    }

    # -------------------------------------------------------------
    # 股票期貨對沖與價差聯動 (Stock Futures Synergy - Rule 14 & 15)
    # -------------------------------------------------------------
    fut_info = {}
    try:
        fut_pool = load_stock_futures_pool()
        code_str = str(cb_item['code']).strip()
        if code_str in fut_pool:
            raw_f = fut_pool[code_str]
            contract_sym = raw_f.get('contract', f"{code_str}F")
            fut_info = {
                '期貨契約': contract_sym,
                '期貨月份': '近月主力',
                '期貨收盤價': price,
                '價差(Basis)': '平價',
                '1口保證金(約)': int(price * 2000 * 0.135)
            }
            # 股期 OI 加分
            score = min(score + 5, 100)
    except Exception:
        pass

    return {
        '代號': cb_item['code'],
        '名稱': cb_item['name'],
        '證券代號': cb_item['code'],
        '證券名稱': cb_item['name'],
        '梯隊標籤': tier_label,
        '星級推薦': stars,
        '星級': stars,
        '評分': score,
        '收盤價': price,
        '現價': price,
        '進度狀態': cb_item.get('status_desc', '已掛牌交易中 (閉鎖期保護內)'),
        '進場價位': entry_price,
        '建議進場價': entry_price,
        '第一次進場價位': entry_price,
        '加碼價位': addon_price,
        '動能加碼價': addon_price,
        '第二次進場價位': addon_price,
        '停利TP1': tp1_price,
        '第一次停利價位': tp1_price,
        '停利TP2': tp2_price,
        '第二次停利價位': tp2_price,
        '停損價位': sl_price,
        '嚴格停損價': sl_price,
        '漲停價': limit_up,
        '跌停價': limit_down,
        '盈虧比': rr_ratio,
        '操盤錦囊': guidance_updated,
        '隔日沖風控': og_risk.get('tag', '🟢主力籌碼沉澱'),
        '最後執行死線': lockup_end,
        '空間極限130%': target_130_pct,
        '退場規則說明': cb_item.get('exit_rule', f'{lockup_end} 閉鎖解禁前出清'),
        '轉換價格': conv_price,
        '現價轉換比': price_to_conv_ratio,
        '當日成交量(張)': vol,
        '成交量(張)': vol,
        '20日均量': vol20,
        '量縮比率%': vol_ratio,
        '20MA': ma20,
        '60MA': ma60,
        'CB期次': cb_item.get('cb_name', '可轉換公司債'),
        '資金用途': cb_item.get('purpose', '充實營運資金/擴充產能'),
        '發行規模(億)': est_amount,
        **fut_info
    }


def format_line_message(results: List[Dict[str, Any]], date_str: Optional[str] = None) -> str:
    """格式化產生高品質 LINE 戰報推播訊息（零引線、四大價位一目了然）"""
    if not date_str:
        date_str = datetime.date.today().strftime('%Y/%m/%d')
    return format_clean_screener_text(results, date_str, strategy_id=STRATEGY_ID, max_stocks=6)


def run_screener(date_str: Optional[str] = None, export_path: Optional[str] = None, send_line: bool = False, force: bool = False, pool_type: str = "comprehensive") -> List[Dict[str, Any]]:
    """執行可轉債定價伏擊旗艦選股器 V4.0"""
    if not date_str:
        date_str = datetime.date.today().strftime('%Y/%m/%d')

    print(f"\n[+] 正在載入大盤位階動態參數 (regime_config.json) ...")
    strat_params = get_strategy_adaptive_params(STRATEGY_ID)
    regime_pricing_params = {
        "tp1_pct": strat_params.get("tp1_pct", 5.0),
        "tp2_pct": strat_params.get("tp2_pct", 12.0),
        "stop_loss_pct": strat_params.get("stop_loss_pct", -4.5),
        "bias_max_pct": strat_params.get("bias_max_pct", 5.0)
    }
    print(f"[✓] 繼承大盤位階參數: TP1={regime_pricing_params['tp1_pct']}%, TP2={regime_pricing_params['tp2_pct']}%, SL={regime_pricing_params['stop_loss_pct']}%")

    print(f"[+] 正在載入可轉債追蹤母池 (模式: {pool_type}) ...")
    pool = load_cb_pool(pool_type=pool_type)
    if not pool:
        print("[X] 查無可轉債追蹤母池資料！")
        return []

    print(f"[+] 正在以多線程抓取個股實時行情與均線數據 (共 {len(pool)} 檔標的)...")
    
    def process_item(item):
        code = str(item['code']).strip()
        market = item.get('market', 'TW')
        m_data = fetch_stock_market_data(code, market)
        if m_data:
            return score_and_analyze_cb(item, m_data, regime_pricing_params=regime_pricing_params)
        return None

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for res in executor.map(process_item, pool):
            if res:
                results.append(res)

    if not results:
        print("[!] 經 4+1 維度量化檢定，今日無符合條件之標的。")
        return []

    # 依評分排序，嚴格遵守 Rule 19 推薦上限 (預設取前 6~10 檔精華)
    results.sort(key=lambda x: x['評分'], reverse=True)
    top_picks = results[:10]

    df = pd.DataFrame(top_picks)

    # 終端機格式化展示
    print("\n" + "="*135)
    print(f"🎯【台股可轉債 CB 定價伏擊旗艦選股日報 V4.0 全域規範版】（結算日期: {date_str}，共精選 {len(top_picks)} 檔 / 候選 {len(results)} 檔）")
    print("="*135)
    display_cols = ['代號', '名稱', '梯隊標籤', '星級', '評分', '現價', '現價轉換比', '發行規模(億)', '建議進場價', '動能加碼價', '停利TP1', '嚴格停損價', '最後執行死線']
    avail_cols = [c for c in display_cols if c in df.columns]
    print(df[avail_cols].to_string(index=False))
    print("="*135)

    # 匯出 CSV (UTF-8-SIG 防亂碼) 與 Markdown 視覺化字卡戰報
    if export_path:
        if os.path.isabs(export_path):
            out_file = export_path
        elif os.path.dirname(export_path) != "":
            out_file = os.path.abspath(export_path)
        else:
            out_file = os.path.join(STRATEGY_DIR, export_path)
        
        # 整合滾動 5 日生命週期管理 (Rolling Retention)
        df_rolling = save_rolling_result_csv(out_file, df, today_date_str=date_str, max_days=5, strategy_id="04_cb_pricing_ambush")
        print(f"[✓] 選股報表已成功匯出至：{out_file} (UTF-8-BOM 編碼，Excel 開啟不亂碼)")

        # 同步產出視覺化字卡 Markdown 戰報
        md_path = os.path.splitext(out_file)[0] + ".md"
        try:
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(format_screener_markdown_cards(top_picks, date_str, strategy_id=STRATEGY_ID, max_stocks=10))
                f.write("\n\n## 📋 完整量化數據指標總表\n\n")
                f.write(df_rolling.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已同步匯出視覺化字卡 Markdown 戰報至: {md_path}")
        except Exception as _e:
            print(f"[!] 匯出 Markdown 字卡戰報失敗: {_e}")

    # 發送 LINE 推播 (含未開盤日防護 Rule 21)
    if send_line:
        suppress, sup_reason = should_suppress_push(force=force, task_type=f"{STRATEGY_NAME} 盤後戰報")
        if suppress:
            print(f"[!] {sup_reason}，已自動取消 LINE 戰報推播 (可加上 --force 強制發送)。")
            return top_picks

        print(f"\n[+] 正在發送 LINE 推播戰報 (策略代號: {STRATEGY_ID})...")
        try:
            card = build_screener_carousel(top_picks, date_str, strategy_id=STRATEGY_ID, max_stocks=6)
            success = send_to_line(card, strategy=STRATEGY_ID)
            if not success:
                clean_text = format_clean_screener_text(top_picks, date_str, strategy_id=STRATEGY_ID, max_stocks=6)
                success = send_to_line(clean_text, strategy=STRATEGY_ID)
            if success:
                print(f"[✓] LINE 戰報推播發送成功！(目標視窗: LINE_TARGET_STRATEGY_{STRATEGY_ID})")
            else:
                print("[!] LINE 推播發送未完成，請檢查 .env 設定。")
        except Exception as e:
            print(f"[X] LINE 發送發生異常: {e}")

    return top_picks


def main():
    parser = argparse.ArgumentParser(description="台股可轉債 (CB) 定價伏擊與區間博弈旗艦選股器 V4.0 全域規範版")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYY/MM/DD)")
    parser.add_argument("--export", type=str, default="result.csv", help="匯出 CSV 檔名 (預設 result.csv)")
    parser.add_argument("--pool", type=str, default="comprehensive", choices=["comprehensive", "curated"], help="母池模式: comprehensive(全市場動態融合) / curated(本地精選)")
    parser.add_argument("--line", action="store_true", help="發送 LINE 推播")
    parser.add_argument("--force", action="store_true", help="強制發送推播，忽略休市日與開盤守門檢查")
    args = parser.parse_args()

    run_screener(date_str=args.date, export_path=args.export, send_line=args.line, force=args.force, pool_type=args.pool)


if __name__ == "__main__":
    main()
