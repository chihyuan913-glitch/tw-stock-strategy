#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 07：高融資使用率×低維持率斷頭伏擊與浮額清洗策略 (Margin Call Ambush Screener)
================================================================================
本選股器嚴格遵循《總指揮中心規範》之 4+1 維度硬篩、雙向狀態機與二階段動態降級容錯架構：
【一、策略核心邏輯 (Why)】
  散戶融資是市場最脆弱的槓桿籌碼。當個股融資使用率偏高 (>=25%) 且融資維持率逼近 130% 追繳線時：
  1. 若收盤摜破月線、法人持續出貨，融資戶面臨補錢或斷頭賣壓，形成多殺多骨牌效應 -> 觸發【🔴斷頭助跌・放空獵殺】。
  2. 若融資已出現斷頭多殺多清洗 (單日減 >=2% 或 3日減 >=4.5%)，且三大法人逆勢大額承接、留下長下影線止跌，
     代表籌碼由散戶移轉至主力巨鯨手中，浮額洗淨即將展開暴力反彈 -> 觸發【🟢浮額清洗・逆勢抄底】。

【二、4+1 五維度硬性過濾條件 (Hard Filters - 不超過 5 個)】
  1. 流動性防守：5日均量 >= 1,000 張，排除處置/全額交割/DR/ETF（限定 4 位數字普通股）。
  2. 槓桿高危線 (Tier-1)：融資使用率 >= 25.0%，融資維持率 <= 142.0%，融資餘額 >= 3,000 張。
  3. 雙向狀態機：必須明確命中 SHORT_BREAKDOWN 或 LONG_REVERSAL 狀態。
  4. 型態防守面：空方需在月線下；多方需收紅或下影線 >= 30% 具備止跌結構。
  5. 空間防追高：月線乖離率絕對值 |Bias20| <= 10.0%。

【三、雙層門檻動態降級容錯機制 (Dynamic Fallback Matrix)】
  - Tier-1 絕對高危：若市場大盤平穩無直接斷頭股 (Tier-1 為 0 檔)，自動啟動 Tier-2 降級：
  - Tier-2 相對高危候補：放寬至 5日均量 >= 800 張、使用率 >= 18.0%、維持率 <= 165.0%、餘額 >= 1500 張，
    依維持率由低至高排序取前 6 檔，標註【⚠️ Tier-2 相對高危候補】。
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
from futures_helper import (
    load_stock_futures_pool,
    fetch_taifex_futures_quotes,
    calculate_futures_pricing
)
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

STRATEGY_ID = "07"
STRATEGY_NAME = "策略 07：高融資使用率×低維持率斷頭伏擊與浮額清洗策略"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')
MD_FILE = os.path.join(os.path.dirname(__file__), 'result.md')

# docs/api-spec.json 規範之 40 個標準 CSV 欄位清單
CSV_COLUMNS = [
    "證券代號",
    "證券名稱",
    "收盤價",
    "多空狀態",
    "狀態代碼",
    "門檻層級",
    "融資使用率%",
    "融資維持率%",
    "預估融資均價",
    "融資餘額(張)",
    "融資1日增減%",
    "融資3日增減%",
    "法人當日買賣超(張)",
    "法人5日買賣超(張)",
    "當日成交量(張)",
    "5日均量(張)",
    "20MA",
    "月線乖離%",
    "進場價位",
    "加碼價位",
    "停利TP1",
    "停利TP2",
    "停損價位",
    "現貨進場",
    "現貨加碼",
    "現貨TP1",
    "現貨TP2",
    "現貨停損",
    "期貨契約",
    "期貨月份",
    "期貨收盤價",
    "價差(Basis)",
    "期貨未平倉(OI)",
    "1口保證金(約)",
    "結算倒數",
    "次月合約",
    "次月進場參考",
    "隔日沖風控",
    "評分",
    "星級推薦"
]


def calculate_margin_cost_and_maintenance(
    margin_history: list,
    close_price: float,
    initial_vwap: float = 0.0
) -> dict:
    """
    根據融資買賣現償流水帳遞推平均融資持倉成本 C_bar 與融資維持率 MR (%)
    公式依據 docs/api-spec.json 規範：
      - 融資借款成數以 6 成計算 (融資金額 = C_bar * 0.6)
      - 維持率 MR = (close_price / (C_bar * 0.6)) * 100
      - 保留部位 rem = max(prev_balance - sell_lots - cash_repay_lots, 0)
      - 動態成本遞推: C_bar = (rem * C_bar + buy_lots * typical_price) / (rem + buy_lots)
    """
    # 邊界極端保護：若無歷史或價格非法，安全回傳零值，絕不拋出 ZeroDivisionError
    if not margin_history or close_price <= 0:
        return {
            "avg_cost": 0.0,
            "maintenance_ratio": 0.0,
            "usage_ratio": 0.0,
            "balance_lots": 0,
            "margin_change_1d_pct": 0.0,
            "margin_change_3d_pct": 0.0
        }

    last_record = margin_history[-1]
    last_balance = int(last_record.get('balance_lots', 0))

    if last_balance <= 0:
        return {
            "avg_cost": 0.0,
            "maintenance_ratio": 0.0,
            "usage_ratio": 0.0,
            "balance_lots": last_balance,
            "margin_change_1d_pct": 0.0,
            "margin_change_3d_pct": 0.0
        }

    # 初始成本錨定
    first_tp = float(margin_history[0].get('typical_price', close_price))
    c_bar = float(initial_vwap) if initial_vwap > 0 else (first_tp if first_tp > 0 else close_price)

    for item in margin_history:
        prev_b = float(item.get('prev_balance_lots', 0))
        buy_l = float(item.get('buy_lots', 0))
        sell_l = float(item.get('sell_lots', 0))
        cash_l = float(item.get('cash_repay_lots', 0))
        bal_l = float(item.get('balance_lots', 0))
        tp = float(item.get('typical_price', close_price))
        if tp <= 0:
            tp = close_price

        if bal_l > 0:
            rem = max(prev_b - sell_l - cash_l, 0.0)
            denom = rem + buy_l
            if denom > 0:
                c_bar = (rem * c_bar + buy_l * tp) / denom

    avg_cost = round(c_bar, 2)

    # 融資維持率估算 (以標準 6 成融資借款額為分母)
    if avg_cost > 0:
        maintenance_ratio = round((close_price / (avg_cost * 0.6)) * 100.0, 2)
    else:
        maintenance_ratio = 0.0

    # 融資使用率
    quota = float(last_record.get('quota_lots', 0))
    if quota > 0:
        usage_ratio = round((last_balance / quota) * 100.0, 2)
    else:
        usage_ratio = 0.0

    # 融資單日增減 % (對比前日餘額)
    prev_bal = float(last_record.get('prev_balance_lots', 0))
    if prev_bal > 0:
        margin_change_1d_pct = round(((last_balance - prev_bal) / prev_bal) * 100.0, 2)
    else:
        margin_change_1d_pct = 0.0

    # 融資近 3 日增減 % (相較於 3 日前餘額)
    if len(margin_history) >= 4:
        bal_3d = float(margin_history[-4].get('balance_lots', 0))
    elif len(margin_history) > 1:
        bal_3d = float(margin_history[0].get('prev_balance_lots', margin_history[0].get('balance_lots', 0)))
    else:
        bal_3d = prev_bal

    if bal_3d > 0:
        margin_change_3d_pct = round(((last_balance - bal_3d) / bal_3d) * 100.0, 2)
    else:
        margin_change_3d_pct = 0.0

    return {
        "avg_cost": avg_cost,
        "maintenance_ratio": maintenance_ratio,
        "usage_ratio": usage_ratio,
        "balance_lots": last_balance,
        "margin_change_1d_pct": margin_change_1d_pct,
        "margin_change_3d_pct": margin_change_3d_pct
    }


def classify_dual_state_and_pricing(stock_info: dict) -> dict:
    """
    雙向狀態機分流與四大防線定價
    狀態分流：
      1. LONG_REVERSAL: 🟢【浮額清洗・逆勢抄底】
      2. SHORT_BREAKDOWN: 🔴【斷頭助跌・放空獵殺】
    """
    close = float(stock_info.get('close', stock_info.get('收盤價', 0.0)))
    open_p = float(stock_info.get('open', stock_info.get('open_p', close)))
    high = float(stock_info.get('high', close))
    low = float(stock_info.get('low', close))
    ma20 = float(stock_info.get('ma20', stock_info.get('20MA', close)))

    inst_1d = float(stock_info.get('inst_1d_lots', stock_info.get('法人當日買賣超(張)', 0.0)))
    inst_3d = float(stock_info.get('inst_3d_lots', 0.0))
    inst_5d = float(stock_info.get('inst_5d_lots', stock_info.get('法人5日買賣超(張)', 0.0)))

    chg_1d = float(stock_info.get('margin_change_1d_pct', stock_info.get('融資1日增減%', 0.0)))
    chg_3d = float(stock_info.get('margin_change_3d_pct', stock_info.get('融資3日增減%', 0.0)))
    force_classify = bool(stock_info.get('force_classify', False))

    candle_range = max(high - low, 0.01)
    lower_shadow = min(open_p, close) - low
    lower_shadow_ratio = max(0.0, lower_shadow / candle_range)

    # 1. 多方清洗抄底判定 (浮額清洗、法人低接、長下影線或收紅)
    is_long = (
        (chg_1d <= -2.0 or chg_3d <= -4.5)
        and (inst_1d > 0 or inst_3d > 0)
        and (close >= open_p or lower_shadow_ratio >= 0.30)
    )

    # 2. 空方斷頭助跌判定 (摜破月線、法人棄守、融資死抱未撤)
    is_short = (
        close < ma20
        and (inst_5d <= 0 or inst_1d < 0)
        and chg_3d >= -3.0
    )

    # 若啟動 Tier-2 降級強制分流
    if force_classify and not is_long and not is_short:
        if inst_1d > 0 and chg_1d < 0:
            is_long = True
        else:
            is_short = True

    if is_long:
        state_code = "LONG_REVERSAL"
        state_badge = "🟢【浮額清洗・逆勢抄底】"
        entry_p = round(close, 2)
        addon_p = round(max(high * 1.01, close * 1.025), 2)
        tp1_p = round(max(ma20, close * 1.07), 2)
        tp2_p = round(close * 1.14, 2)
        sl_p = round(min(low * 0.985, close * 0.96), 2)

        # 嚴格確保數學關係：sl_price < entry_price < addon_price < tp1_price < tp2_price
        if addon_p <= entry_p:
            addon_p = round(entry_p * 1.02, 2)
        if tp1_p <= addon_p:
            tp1_p = round(addon_p * 1.03, 2)
        if tp2_p <= tp1_p:
            tp2_p = round(tp1_p * 1.05, 2)
        if sl_p >= entry_p:
            sl_p = round(entry_p * 0.96, 2)

        return {
            "state_code": state_code,
            "state_badge": state_badge,
            "badge": state_badge,
            "多空狀態": state_badge,
            "entry_price": entry_p,
            "addon_price": addon_p,
            "tp1_price": tp1_p,
            "tp2_price": tp2_p,
            "sl_price": sl_p,
            "lower_shadow_ratio": round(lower_shadow_ratio, 2)
        }
    elif is_short:
        state_code = "SHORT_BREAKDOWN"
        state_badge = "🔴【斷頭助跌・放空獵殺】"
        entry_p = round(close, 2)
        addon_p = round(min(low * 0.995, close * 0.975), 2)
        tp1_p = round(close * 0.92, 2)
        tp2_p = round(close * 0.85, 2)
        sl_p = round(max(min(ma20 * 1.01, close * 1.035), close * 1.025), 2)

        # 嚴格確保數學關係：tp2_price < tp1_price < addon_price < entry_price < sl_price
        if addon_p >= entry_p:
            addon_p = round(entry_p * 0.98, 2)
        if tp1_p >= addon_p:
            tp1_p = round(addon_p * 0.95, 2)
        if tp2_p >= tp1_p:
            tp2_p = round(tp1_p * 0.92, 2)
        if sl_p <= entry_p:
            sl_p = round(entry_p * 1.025, 2)

        return {
            "state_code": state_code,
            "state_badge": state_badge,
            "badge": state_badge,
            "多空狀態": state_badge,
            "entry_price": entry_p,
            "addon_price": addon_p,
            "tp1_price": tp1_p,
            "tp2_price": tp2_p,
            "sl_price": sl_p,
            "lower_shadow_ratio": round(lower_shadow_ratio, 2)
        }
    else:
        return {
            "state_code": "NONE",
            "state_badge": "⚪【未達狀態閾值】",
            "badge": "⚪【未達狀態閾值】",
            "多空狀態": "⚪【未達狀態閾值】",
            "entry_price": round(close, 2),
            "addon_price": round(close, 2),
            "tp1_price": round(close, 2),
            "tp2_price": round(close, 2),
            "sl_price": round(close, 2),
            "lower_shadow_ratio": round(lower_shadow_ratio, 2)
        }


def apply_screening_with_fallback(candidates_raw: list) -> tuple:
    """
    執行 4+1 五維度硬性濾網，若 Tier-1 為 0 檔，自動降級啟動 Tier-2 相對高危候補
    回傳: (filtered_list: list, tier_mode: str)
    """
    tier1_list = []
    for c in candidates_raw:
        code = str(c.get('code', c.get('證券代號', ''))).strip()
        # 1. 流動性防守與代碼合法性 (排除處置/DR/ETF/權證)
        if not (code.isdigit() and len(code) == 4):
            continue
        vol_5d = float(c.get('vol_5d_lots') or c.get('volume_5d_avg') or c.get('5日均量(張)') or 0)
        if vol_5d < 1000:
            continue

        # 2. Tier-1 絕對門檻：使用率 >= 25%, 維持率 <= 142%, 融資餘額 >= 3000 張
        usage = float(c.get('usage_ratio', c.get('融資使用率%', 0)))
        mr = float(c.get('maintenance_ratio', c.get('融資維持率%', 0)))
        bal = float(c.get('balance_lots', c.get('融資餘額(張)', 0)))
        if usage < 25.0 or mr > 142.0 or bal < 3000:
            continue

        # 3. 雙向狀態機判定
        st = classify_dual_state_and_pricing(c)
        if st['state_code'] not in ("SHORT_BREAKDOWN", "LONG_REVERSAL"):
            continue

        # 4. K 線型態與均線配合
        close = float(c.get('close', c.get('收盤價', 0)))
        ma20 = float(c.get('ma20', c.get('20MA', close)))
        open_p = float(c.get('open', c.get('open_p', close)))
        lower_shadow_ratio = st.get('lower_shadow_ratio', 0.0)

        if st['state_code'] == "SHORT_BREAKDOWN" and close >= ma20:
            continue
        if st['state_code'] == "LONG_REVERSAL" and not (close >= open_p or lower_shadow_ratio >= 0.30):
            continue

        # 5. 空間防追高：月線乖離率絕對值 <= 10.0%
        if c.get('bias20') is not None:
            bias20 = float(c.get('bias20'))
        elif c.get('月線乖離%') is not None:
            bias20 = float(c.get('月線乖離%'))
        else:
            bias20 = round(((close - ma20) / ma20) * 100.0, 2) if ma20 > 0 else 0.0

        if abs(bias20) > 10.0:
            continue

        item = dict(c)
        item.update(st)
        item['tier_mode'] = "TIER1_ABSOLUTE"
        item['門檻層級'] = "🎯 Tier-1 絕對斷頭高危"
        item['maintenance_ratio'] = mr
        item['usage_ratio'] = usage
        tier1_list.append(item)

    if tier1_list:
        return tier1_list, "TIER1_ABSOLUTE"

    # 若 Tier-1 為 0 檔，自動啟動 Tier-2 降級容錯
    tier2_pool = []
    for c in candidates_raw:
        code = str(c.get('code', c.get('證券代號', ''))).strip()
        if not (code.isdigit() and len(code) == 4):
            continue
        vol_5d = float(c.get('vol_5d_lots') or c.get('volume_5d_avg') or c.get('5日均量(張)') or 0)
        if vol_5d < 800:
            continue
        usage = float(c.get('usage_ratio', c.get('融資使用率%', 0)))
        mr = float(c.get('maintenance_ratio', c.get('融資維持率%', 0)))
        bal = float(c.get('balance_lots', c.get('融資餘額(張)', 0)))

        close = float(c.get('close', c.get('收盤價', 0)))
        ma20 = float(c.get('ma20', c.get('20MA', close)))
        if c.get('bias20') is not None:
            bias20 = float(c.get('bias20'))
        elif c.get('月線乖離%') is not None:
            bias20 = float(c.get('月線乖離%'))
        else:
            bias20 = round(((close - ma20) / ma20) * 100.0, 2) if ma20 > 0 else 0.0

        # Tier-2 寬鬆指標
        if usage >= 18.0 and mr <= 165.0 and bal >= 1500 and abs(bias20) <= 12.0:
            c_copy = dict(c)
            c_copy['force_classify'] = True
            st = classify_dual_state_and_pricing(c_copy)
            c_copy.update(st)
            c_copy['tier_mode'] = "TIER2_RELATIVE_WARNING"
            c_copy['門檻層級'] = "⚠️ Tier-2 相對高危候補"
            c_copy['maintenance_ratio'] = mr
            c_copy['usage_ratio'] = usage
            tier2_pool.append(c_copy)

    # 依維持率由低至高排序 (維持率越低越脆弱)，取前 6 檔
    tier2_pool.sort(key=lambda x: float(x.get('maintenance_ratio', x.get('融資維持率%', 999.0))))
    return tier2_pool[:6], "TIER2_RELATIVE_WARNING"


def get_recent_trading_dates(n_days: int = 5, max_lookback: int = 20) -> list:
    """取得最近 n_days 個證交所實際有開盤的交易日 (YYYYMMDD)"""
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


def fetch_twse_margin_history(dates: list) -> dict:
    """
    批次抓取臺灣證券交易所 MI_MARGN (融資融券) 歷史明細
    回傳: {code: [item_oldest, ..., item_newest]}
    """
    print(f"[*] 正在抓取近 {len(dates)} 個交易日的證交所融資流水帳數據: {dates} ...")
    margin_by_date = {}
    for d_str in dates:
        url = f'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d_str}&selectType=ALL&response=json'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        daily_records = {}
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                for t in data.get('tables', []):
                    # 融資融券彙總明細表
                    fields = t.get('fields', [])
                    if len(fields) >= 7 and '代號' in str(fields[0]):
                        for row in t.get('data', []):
                            code = str(row[0]).strip()
                            if len(code) == 4 and code.isdigit():
                                try:
                                    name = str(row[1]).strip()
                                    buy_lots = int(row[2].replace(',', ''))
                                    sell_lots = int(row[3].replace(',', ''))
                                    cash_repay = int(row[4].replace(',', ''))
                                    prev_bal = int(row[5].replace(',', ''))
                                    curr_bal = int(row[6].replace(',', ''))
                                    quota = int(row[7].replace(',', '')) if len(row) > 7 else 0
                                    daily_records[code] = {
                                        'name': name,
                                        'buy_lots': buy_lots,
                                        'sell_lots': sell_lots,
                                        'cash_repay_lots': cash_repay,
                                        'prev_balance_lots': prev_bal,
                                        'balance_lots': curr_bal,
                                        'quota_lots': quota
                                    }
                                except (ValueError, IndexError):
                                    pass
        except Exception as e:
            print(f"[!] 抓取 {d_str} 融資數據失敗: {e}")
        margin_by_date[d_str] = daily_records

    # 依時間先後排序重組 (由舊到新)
    sorted_dates = sorted(dates)
    all_codes = set()
    for d_str in sorted_dates:
        all_codes.update(margin_by_date.get(d_str, {}).keys())

    history_by_code = {}
    for c in all_codes:
        hist = []
        for d_str in sorted_dates:
            rec = margin_by_date.get(d_str, {}).get(c)
            if rec:
                r_copy = dict(rec)
                r_copy['date'] = d_str
                hist.append(r_copy)
        if hist:
            history_by_code[c] = hist

    return history_by_code


def fetch_twse_quotes_and_chips(date_str: str) -> tuple:
    """抓取基準日的收盤行情 (MI_INDEX) 與三大法人 (T86)"""
    quotes = {}
    chips = {}

    # 1. 法人買賣超 (T86)
    url_t86 = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json'
    req_t86 = urllib.request.Request(url_t86, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req_t86, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get('stat') == 'OK':
                for row in data.get('data', []):
                    code = str(row[0]).strip()
                    if len(code) == 4 and code.isdigit():
                        try:
                            foreign = int(row[4].replace(',', '')) // 1000
                            trust = int(row[10].replace(',', '')) // 1000
                            dealer = int(row[11].replace(',', '')) // 1000
                            total = int(row[18].replace(',', '')) // 1000
                            chips[code] = {
                                'foreign_lots': foreign,
                                'trust_lots': trust,
                                'dealer_lots': dealer,
                                'total_lots': total
                            }
                        except (ValueError, IndexError):
                            pass
    except Exception as e:
        print(f"[!] 抓取三大法人失敗: {e}")

    # 2. 收盤行情 (MI_INDEX)
    url_mi = f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=json'
    req_mi = urllib.request.Request(url_mi, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req_mi, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for t in data.get('tables', []):
                if '每日收盤行情' in t.get('title', ''):
                    for row in t.get('data', []):
                        code = str(row[0]).strip()
                        if len(code) == 4 and code.isdigit():
                            try:
                                name = str(row[1]).strip()
                                vol_shares = int(row[2].replace(',', ''))
                                open_p = row[5].replace(',', '').strip()
                                high_p = row[6].replace(',', '').strip()
                                low_p = row[7].replace(',', '').strip()
                                close_p = row[8].replace(',', '').strip()
                                if close_p != '--' and open_p != '--':
                                    quotes[code] = {
                                        'name': name,
                                        'volume_lots': vol_shares // 1000,
                                        'open': float(open_p),
                                        'high': float(high_p),
                                        'low': float(low_p),
                                        'close': float(close_p)
                                    }
                            except (ValueError, IndexError):
                                pass
    except Exception as e:
        print(f"[!] 抓取每日行情失敗: {e}")

    return quotes, chips


def calculate_margin_soft_score(
    item: dict,
    overnight_guard: dict,
    fut_pricing: dict
) -> tuple:
    """
    二階段 100 分制軟評分矩陣
    回傳: (score: int, stars: str)
    """
    base_score = 40

    # 1. 融資維持率脆弱度 (越低分越高，逼近斷頭危險線)
    mr = float(item.get('maintenance_ratio', item.get('融資維持率%', 160.0)))
    if mr <= 135.0:
        score_mr = 25
    elif mr <= 145.0:
        score_mr = 20
    elif mr <= 155.0:
        score_mr = 12
    else:
        score_mr = 5

    # 2. 融資使用率沉重度 (使用率越高，籌碼越凌亂)
    usage = float(item.get('usage_ratio', item.get('融資使用率%', 0.0)))
    if usage >= 40.0:
        score_usage = 20
    elif usage >= 30.0:
        score_usage = 15
    elif usage >= 20.0:
        score_usage = 10
    else:
        score_usage = 5

    # 3. 期貨流動性加分 (具有個股期貨且未平倉深度佳)
    fut_oi = fut_pricing.get('futures_oi', 0)
    if fut_oi >= 5000:
        score_oi = 15
    elif fut_oi >= 2000:
        score_oi = 10
    elif fut_oi >= 500:
        score_oi = 5
    else:
        score_oi = 0

    # 扣除隔日沖倒貨風險扣分
    penalty = overnight_guard.get('penalty_score', 0)

    raw_total = base_score + score_mr + score_usage + score_oi - penalty
    final_score = max(40, min(100, raw_total))

    if final_score >= 85:
        stars = "★★★★★"
    elif final_score >= 75:
        stars = "★★★★☆"
    else:
        stars = "★★★☆☆"

    return final_score, stars


def run_screening(date_str: str = None) -> list:
    """執行策略 07 完整選股作業"""
    recent_dates = get_recent_trading_dates(n_days=5)
    if not recent_dates:
        print("[X] 無法取得近期交易日數據，請檢查網路連線。")
        return []

    latest_date = date_str or recent_dates[0]
    print("=" * 70)
    print(f"🚀 開始執行【{STRATEGY_NAME}】選股引擎 (基準日: {latest_date})")
    print("=" * 70)

    # 抓取融資歷史 (5日) 與最新行情、法人
    margin_history_all = fetch_twse_margin_history(recent_dates)
    quotes, chips = fetch_twse_quotes_and_chips(latest_date)

    if not quotes or not margin_history_all:
        print("[X] 行情或融資流水帳數據不齊全，結束選股。")
        return []

    # 初篩候選人：具備融資資料且成交量 >= 500 張
    candidate_codes = [c for c, q in quotes.items() if c in margin_history_all and q['volume_lots'] >= 500]
    print(f"[*] 具備融資追蹤與基本成交量之標的共：{len(candidate_codes)} 檔。正在下載歷史 K 線以計算 20MA 與典型價...")

    if not candidate_codes:
        return []

    tickers = [f"{c}.TW" for c in candidate_codes]
    try:
        hist_data = yf.download(tickers, period='3mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 下載 Yahoo 歷史數據失敗: {e}")
        hist_data = None

    futures_map = load_stock_futures_pool()
    taifex_quotes = fetch_taifex_futures_quotes()

    raw_candidates = []

    for code in candidate_codes:
        q = quotes[code]
        m_hist = margin_history_all.get(code, [])
        if not m_hist:
            continue

        close_p = q['close']
        t_key = f"{code}.TW"

        # 歷史技術面指標 (20MA, 5MA Volume)
        ma20 = close_p
        v5_avg = q['volume_lots']
        typical_price_map = {}

        if hist_data is not None:
            try:
                df = hist_data[t_key] if len(candidate_codes) > 1 and t_key in hist_data.columns.levels[0] else hist_data
                if 'Close' in df and not df['Close'].dropna().empty:
                    c_s = df['Close'].dropna()
                    v_s = df['Volume'].dropna()
                    h_s = df['High'].dropna() if 'High' in df else c_s
                    l_s = df['Low'].dropna() if 'Low' in df else c_s

                    if len(c_s) >= 20:
                        ma20 = float(c_s.rolling(20).mean().iloc[-1])
                    if len(v_s) >= 5:
                        v5_avg = float(v_s.rolling(5).mean().iloc[-1]) // 1000

                    # 填補歷史典型價
                    for dt, row in df.iterrows():
                        ds = dt.strftime('%Y%m%d') if hasattr(dt, 'strftime') else str(dt)[:10].replace('-', '')
                        typical_price_map[ds] = float((row.get('High', close_p) + row.get('Low', close_p) + row.get('Close', close_p)) / 3.0)
            except Exception:
                pass

        # 為 m_hist 填入典型價格
        for r in m_hist:
            r_d = r.get('date', '')
            r['typical_price'] = typical_price_map.get(r_d, close_p)

        # 呼叫核心融資成本與維持率引擎
        m_metrics = calculate_margin_cost_and_maintenance(m_hist, close_p)

        chip_info = chips.get(code, {'foreign_lots': 0, 'trust_lots': 0, 'dealer_lots': 0, 'total_lots': 0})
        bias20 = round(((close_p - ma20) / ma20) * 100.0, 2) if ma20 > 0 else 0.0

        raw_candidates.append({
            'code': code,
            '證券代號': code,
            '證券名稱': q['name'],
            'close': close_p,
            '收盤價': close_p,
            'open': q['open'],
            'open_p': q['open'],
            'high': q['high'],
            'low': q['low'],
            'ma20': round(ma20, 2),
            '20MA': round(ma20, 2),
            'bias20': bias20,
            '月線乖離%': bias20,
            'vol_today_lots': q['volume_lots'],
            '當日成交量(張)': q['volume_lots'],
            'vol_5d_lots': int(v5_avg),
            '5日均量(張)': int(v5_avg),
            'inst_1d_lots': chip_info['total_lots'],
            '法人當日買賣超(張)': chip_info['total_lots'],
            'inst_3d_lots': chip_info['total_lots'] * 2,  # 概估
            'inst_5d_lots': chip_info['total_lots'] * 3,
            '法人5日買賣超(張)': chip_info['total_lots'] * 3,
            'usage_ratio': m_metrics['usage_ratio'],
            '融資使用率%': m_metrics['usage_ratio'],
            'maintenance_ratio': m_metrics['maintenance_ratio'],
            '融資維持率%': m_metrics['maintenance_ratio'],
            'avg_cost': m_metrics['avg_cost'],
            '預估融資均價': m_metrics['avg_cost'],
            'balance_lots': m_metrics['balance_lots'],
            '融資餘額(張)': m_metrics['balance_lots'],
            'margin_change_1d_pct': m_metrics['margin_change_1d_pct'],
            '融資1日增減%': m_metrics['margin_change_1d_pct'],
            'margin_change_3d_pct': m_metrics['margin_change_3d_pct'],
            '融資3日增減%': m_metrics['margin_change_3d_pct'],
            'dealer_lots': chip_info['dealer_lots'],
            'foreign_lots': chip_info['foreign_lots']
        })

    # 執行 4+1 維度硬篩與動態降級容錯
    screened_list, tier_mode = apply_screening_with_fallback(raw_candidates)
    print(f"[✓] 篩選完成：命中層級【{tier_mode}】，共 {len(screened_list)} 檔標的。")

    results = []

    for item in screened_list:
        code = item['code']
        close_p = item['close']
        st_code = item['state_code']
        is_long = (st_code == "LONG_REVERSAL")

        # 股票期貨報價與定價引擎
        f_info = futures_map.get(code, {'contract': '--', 'name': item['證券名稱']})
        contract_base = f_info.get('contract', '--')
        fut_q = taifex_quotes.get(contract_base)

        fut_pricing = calculate_futures_pricing(
            spot_close=close_p,
            futures_quote=fut_q,
            entry_spot=item['entry_price'],
            addon_spot=item['addon_price'],
            tp1_spot=item['tp1_price'],
            tp2_spot=item['tp2_price'],
            sl_spot=item['sl_price'],
            contract_base=contract_base,
            is_long=is_long
        )

        # 隔日沖風控評估
        broker_guard = evaluate_overnight_broker_risk(
            code=code,
            meta={
                'volume': item['vol_today_lots'],
                'foreign': item.get('foreign_lots', 0),
                'dealer_hedge': item.get('dealer_lots', 0),
                'close': close_p
            }
        )

        # 軟評分系統
        score, stars = calculate_margin_soft_score(item, broker_guard, fut_pricing)

        # 決定最終報價：若有期貨且具備真實報價則期貨優先
        has_fut = fut_pricing.get('has_real_quote', False)
        entry_final = fut_pricing['futures_entry'] if has_fut else item['entry_price']
        addon_final = fut_pricing['futures_addon'] if has_fut else item['addon_price']
        tp1_final = fut_pricing['futures_tp1'] if has_fut else item['tp1_price']
        tp2_final = fut_pricing['futures_tp2'] if has_fut else item['tp2_price']
        sl_final = fut_pricing['futures_sl'] if has_fut else item['sl_price']

        has_contract = fut_pricing.get('has_contract', False)

        results.append({
            "證券代號": code,
            "證券名稱": item['證券名稱'],
            "收盤價": close_p,
            "多空狀態": item['state_badge'],
            "狀態代碼": item['state_code'],
            "門檻層級": item['門檻層級'],
            "融資使用率%": item['融資使用率%'],
            "融資維持率%": item['融資維持率%'],
            "預估融資均價": item['預估融資均價'],
            "融資餘額(張)": item['融資餘額(張)'],
            "融資1日增減%": item['融資1日增減%'],
            "融資3日增減%": item['融資3日增減%'],
            "法人當日買賣超(張)": item['法人當日買賣超(張)'],
            "法人5日買賣超(張)": item['法人5日買賣超(張)'],
            "當日成交量(張)": item['當日成交量(張)'],
            "5日均量(張)": item['5日均量(張)'],
            "20MA": item['20MA'],
            "月線乖離%": item['月線乖離%'],
            "進場價位": entry_final,
            "加碼價位": addon_final,
            "停利TP1": tp1_final,
            "停利TP2": tp2_final,
            "停損價位": sl_final,
            "現貨進場": item['entry_price'],
            "現貨加碼": item['addon_price'],
            "現貨TP1": item['tp1_price'],
            "現貨TP2": item['tp2_price'],
            "現貨停損": item['sl_price'],
            "期貨契約": fut_pricing.get('contract_sym', '--') if has_contract else '--',
            "期貨月份": fut_pricing.get('month', '--') if has_contract else '--',
            "期貨收盤價": fut_pricing.get('futures_close', '') if (has_contract and fut_pricing.get('futures_close') is not None) else '',
            "價差(Basis)": fut_pricing.get('basis_desc', '') if has_contract else '',
            "期貨未平倉(OI)": fut_pricing.get('futures_oi', '') if has_contract else '',
            "1口保證金(約)": fut_pricing.get('margin_1lot', '') if has_contract else '',
            "結算倒數": fut_pricing.get('settlement_tag', '') if has_contract else '',
            "次月合約": fut_pricing.get('next_month', '') if has_contract else '',
            "次月進場參考": fut_pricing.get('next_entry', '') if (has_contract and fut_pricing.get('next_entry') is not None) else '',
            "隔日沖風控": broker_guard.get('tag', '安全・主力沉澱'),
            "評分": score,
            "星級推薦": stars
        })

    # 排序：評分由高至低，若同分則依維持率由低至高 (越低越危險脆弱)
    results.sort(key=lambda x: (x['評分'], -float(x['融資維持率%'])), reverse=True)
    return results


def export_reports(stocks: list, csv_path: str = CSV_FILE, md_path: str = MD_FILE):
    """匯出 UTF-8-BOM CSV 與高對比字卡 Markdown 戰報"""
    if not stocks:
        df_empty = pd.DataFrame(columns=CSV_COLUMNS)
        df_empty.to_csv(csv_path, index=False, encoding='utf-8-sig')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(f"# 【{STRATEGY_NAME}】最新選股戰報\n\n今日無標的符合斷頭高危或浮額清洗條件。\n")
        print(f"[✓] 今日無入選標的，已建立空白報表: {csv_path}")
        return

    df = pd.DataFrame(stocks)
    # 確保所有規範欄位都在
    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[CSV_COLUMNS]

    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"[✓] 已成功匯出 CSV 報表至: {csv_path}")

    today_str = datetime.date.today().strftime('%Y/%m/%d')
    cards_md = format_screener_markdown_cards(
        results=stocks[:8],
        date_str=today_str,
        strategy_id=STRATEGY_ID,
        max_stocks=8
    )

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# 【{STRATEGY_NAME}】盤後選股戰訊報表\n\n")
        f.write(f"產出時間：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(cards_md)
        f.write("\n\n## 完整入選量化數據明細表\n\n")
        f.write(df.to_markdown(index=False))
        f.write("\n")
    print(f"[✓] 已成功匯出 Markdown 戰報至: {md_path}")


def push_to_line(stocks: list, date_str: str = None):
    """使用 line_sender 推播橫向輪播卡片與純文字備援至策略 07 專屬群組"""
    if not stocks:
        msg = f"📊【{STRATEGY_NAME}】今日盤後選股無符合條件標的。"
        send_to_line(msg, strategy=STRATEGY_ID)
        return

    today_str = date_str or datetime.date.today().strftime('%Y/%m/%d')
    top_stocks = stocks[:6]
    carousel = build_screener_carousel(
        results=top_stocks,
        date_str=today_str,
        strategy_id=STRATEGY_ID,
        max_stocks=6
    )

    clean_text = format_clean_screener_text(
        results=top_stocks,
        date_str=today_str,
        strategy_id=STRATEGY_ID,
        max_stocks=6
    )

    print(f"[*] 正在推播 {len(top_stocks)} 檔斷頭伏擊精華至 LINE (strategy={STRATEGY_ID})...")
    success = send_to_line(carousel, strategy=STRATEGY_ID)
    if not success:
        send_to_line(clean_text, strategy=STRATEGY_ID)


def main():
    parser = argparse.ArgumentParser(description=f"{STRATEGY_NAME} 盤後選股器")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (YYYYMMDD)")
    parser.add_argument("--line", action="store_true", help="執行後推播 LINE 戰報")
    parser.add_argument("--export", type=str, default=CSV_FILE, help="匯出 CSV 路徑")
    args = parser.parse_args()

    results = run_screening(date_str=args.date)
    export_reports(results, csv_path=args.export, md_path=MD_FILE)

    if args.line:
        push_to_line(results, date_str=args.date)


if __name__ == "__main__":
    main()
