#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 08：台股集團作帳與母子聯動伏擊策略 (Group Window Dressing Ambush Screener)
================================================================================
本選股器嚴格遵循《總指揮中心規範》之 4+1 維度硬篩、母子聯動雙向狀態機與二階段動態降級容錯架構：
【一、策略核心邏輯 (Why)】
  台股集團股（如華新麗華、鴻海、聯電、國巨等）具備高度資本鏈結與股權交叉持有特徵。每逢季底與年底：
  1. 母公司有拉抬子公司股價美化合併報表帳面利益、衝刺績效之強烈誘因（集團作帳）。
  2. 若母公司率先表態突破月線，外資與投信法人大額進駐，資金往往快速擴散至低基期之子孫公司，形成【🟢作帳點火・母子共振】。
  3. 反之，當季末衝刺告一段落，或個股摜破月線且法人大額提款，集團資金抽離形成高檔獲利了結賣壓，觸發【🔴集團結帳・順勢放空】。

【二、4+1 五維度硬性過濾條件 (Hard Filters - 不超過 5 個)】
  1. 流動性防守：5日均量 >= 1,000 張，排除處置/全額交割/DR/ETF（限定 4 位數字普通股）。
  2. 雙向狀態機：必須明確命中 GROUP_IGNITION 或 GROUP_UNWIND 狀態。
  3. 母子共振度 (Tier-1)：作帳多方需同集團當日動能家數 >= 2 檔 (母子或兄弟聯動共振)。
  4. 型態防守面：多方需站上 20MA 且上影線 < 45% (收紅或長下影線)；空方需摜破 20MA 且法人出貨。
  5. 空間防追高：月線乖離率絕對值 |Bias20| <= 8.0%，確保盈虧比合理。

【三、雙層門檻動態降級容錯機制 (Dynamic Fallback Matrix)】
  - Tier-1 集團共振高勝率：滿足全部 4+1 條件。
  - Tier-2 集團潛伏候補：若市場平淡無 Tier-1 標的，自動啟動降級：5日均量 >= 600 張、|Bias20| <= 10.0%，
    依評分、共振率與法人買賣超排序最多取前 6 檔，標註【⚠️ Tier-2 集團潛伏候補】。
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

STRATEGY_ID = "08"
STRATEGY_NAME = "策略 08：台股集團作帳與母子聯動伏擊策略"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')
MD_FILE = os.path.join(os.path.dirname(__file__), 'result.md')
DEFAULT_POOL_FILE = os.path.join(os.path.dirname(__file__), 'group_pool.json')
RANKING_FILE = os.path.join(os.path.dirname(__file__), 'group_ranking.json')

# docs/api-spec.json 規範之標準 CSV 欄位清單 (V2.0 於「所屬集團」後新增 3 個集團排行欄位，共 47 欄)
CSV_COLUMNS = [
    "證券代號",
    "證券名稱",
    "所屬集團",
    "集團排名",
    "集團熱度分",
    "集團狀態",
    "集團旗艦母股",
    "母子位階角色",
    "同集團共振家數",
    "集團共振率%",
    "同集團轉強兄弟股",
    "作帳日曆階段",
    "收盤價",
    "多空狀態",
    "狀態代碼",
    "門檻層級",
    "5日漲幅%",
    "母股5日漲幅%",
    "母子強弱差%",
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


def load_group_pool(pool_path: str = None) -> list:
    """
    讀取 group_pool.json 並回傳集團清單
    若 pool_path 為 None，預設讀取同目錄下的 group_pool.json
    """
    if pool_path is None:
        pool_path = DEFAULT_POOL_FILE
    
    if not os.path.exists(pool_path):
        return []

    try:
        with open(pool_path, 'r', encoding='utf-8') as f:
            pool = json.load(f)
            return pool if isinstance(pool, list) else []
    except Exception as e:
        print(f"[!] 讀取集團池失敗 {pool_path}: {e}")
        return []


def evaluate_window_dressing_calendar(target_date=None) -> dict:
    """
    評估目標日期在台股作帳行事曆中所處的時序階段
    接受 datetime.date, datetime.datetime 或 'YYYY-MM-DD' 字串
    若為 None 則取台灣當前日期
    判斷規則：
      1. 若 (month, day) 屬於 3, 6, 9, 12 月且 day >= 23：
         回傳 {"phase_code": "UNWIND_WARNING_ZONE", "phase_badge": "⚠️ 季末/年末結帳警戒期", "season_bonus": 5, "unwind_bonus": 12}
      2. 若 month in (11, 12) 且非上述結帳期 (即 11/1 ~ 12/22)：
         回傳 {"phase_code": "Q4_YEAR_END_PEAK", "phase_badge": "🔥 Q4 年底集團作帳主升期", "season_bonus": 15, "unwind_bonus": 0}
      3. 若 (month in (3, 6, 9) 且 1 <= day <= 22) 或 (month == 10 且 day >= 15)：
         回傳 {"phase_code": "QUARTER_END_SPRINT", "phase_badge": "⏳ 季底作帳衝刺期", "season_bonus": 10, "unwind_bonus": 0}
      4. 其他日期：
         回傳 {"phase_code": "NORMAL_ROTATION", "phase_badge": "🛡️ 非季底集團輪動觀察期", "season_bonus": 5, "unwind_bonus": 0}
    """
    if target_date is None:
        tz_tw = datetime.timezone(datetime.timedelta(hours=8))
        d = datetime.datetime.now(tz_tw).date()
    elif isinstance(target_date, str):
        try:
            d = datetime.datetime.strptime(target_date[:10], "%Y-%m-%d").date()
        except Exception:
            tz_tw = datetime.timezone(datetime.timedelta(hours=8))
            d = datetime.datetime.now(tz_tw).date()
    elif isinstance(target_date, datetime.datetime):
        d = target_date.date()
    elif isinstance(target_date, datetime.date):
        d = target_date
    else:
        tz_tw = datetime.timezone(datetime.timedelta(hours=8))
        d = datetime.datetime.now(tz_tw).date()

    month = d.month
    day = d.day

    # 1. 季末/年末結帳警戒期 (3, 6, 9, 12 月 23 日至月底)
    if month in (3, 6, 9, 12) and day >= 23:
        return {
            "phase_code": "UNWIND_WARNING_ZONE",
            "phase_badge": "⚠️ 季末/年末結帳警戒期",
            "season_bonus": 5,
            "unwind_bonus": 12
        }

    # 2. Q4 年底作帳主升期 (11/1 ~ 12/22)
    if month in (11, 12):
        return {
            "phase_code": "Q4_YEAR_END_PEAK",
            "phase_badge": "🔥 Q4 年底集團作帳主升期",
            "season_bonus": 15,
            "unwind_bonus": 0
        }

    # 3. 季底作帳衝刺期 (3, 6, 9 月 1~22 日 或 10 月 15 日起提前卡位)
    if (month in (3, 6, 9) and 1 <= day <= 22) or (month == 10 and day >= 15):
        return {
            "phase_code": "QUARTER_END_SPRINT",
            "phase_badge": "⏳ 季底作帳衝刺期",
            "season_bonus": 10,
            "unwind_bonus": 0
        }

    # 4. 常態輪動
    return {
        "phase_code": "NORMAL_ROTATION",
        "phase_badge": "🛡️ 非季底集團輪動觀察期",
        "season_bonus": 5,
        "unwind_bonus": 0
    }


def evaluate_spread_tactical_status(
    spread_vs_parent_pct: float,
    parent_ret_5d: float,
    parent_above_ma20: bool,
    member_ret_5d: float
) -> str:
    """
    母子動態價差乖離指數 (Spread Anomaly Index) 戰術定位：
    1. 🚀【低基期落後補漲】：spread <= -3.0% 且母公司強勢 (parent_ret >= 1.5 或 站上月線)。母公司拉開空間，子公司具均值回歸補漲動能。
    2. ⚠️【正乖離過大・防母提款】：spread >= 8.0% 且子公司漲幅 >= 10.0%。子公司漲幅遠超母公司，慎防母公司高檔調節處分持股。
    3. 👑【動能領頭羊先行】：spread >= 2.0% 且子公司漲幅 > 0。
    4. 🟢【母子步調一致】：其餘正常價差區間。
    """
    if spread_vs_parent_pct <= -3.0 and (parent_ret_5d >= 1.5 or parent_above_ma20):
        return "🚀【低基期落後補漲】"
    elif spread_vs_parent_pct >= 8.0 and member_ret_5d >= 10.0:
        return "⚠️【正乖離過大・防母提款】"
    elif spread_vs_parent_pct >= 2.0 and member_ret_5d > 0:
        return "👑【動能領頭羊先行】"
    else:
        return "🟢【母子步調一致】"


def calculate_group_resonance_and_hierarchy(stock_records: list) -> list:
    """
    計算同集團內部的多維共振率與母子層級角色分工 (Hierarchy Role)
    輸入: stock_records (list of dict)
    每筆欄位需包含:
      code, name, group_id, group_name, flagship_code, flagship_name,
      close, ma20, ret_5d_pct, inst_1d_lots, inst_5d_lots
    回傳: 補充了 group_resonance_ratio, group_active_count, sibling_leaders,
          hierarchy_role, spread_vs_parent_pct 等欄位的 list of dict
    """
    if not stock_records:
        return []

    # 依 group_id 建立索引分組
    grouped = {}
    for r in stock_records:
        gid = str(r.get('group_id', '')).strip()
        grouped.setdefault(gid, []).append(r)

    enriched_records = []

    for gid, members in grouped.items():
        group_total_count = len(members)
        if group_total_count <= 0:
            continue

        # 1. 判定每檔成分股當日是否「轉強 (is_member_active)」
        active_members = []
        for m in members:
            close = float(m.get('close', 0.0))
            ma20 = float(m.get('ma20', close))
            inst_1d = float(m.get('inst_1d_lots', 0.0))
            inst_5d = float(m.get('inst_5d_lots', 0.0))
            ret_5d = float(m.get('ret_5d_pct', 0.0))

            is_active = (close >= ma20) and (inst_1d > 0 or inst_5d > 0 or ret_5d > 0)
            m['is_member_active'] = is_active
            if is_active:
                active_members.append(m)

        group_active_count = len(active_members)
        group_resonance_ratio = round((group_active_count / group_total_count) * 100.0, 2) if group_total_count > 0 else 0.0

        # 2. 尋找母公司 (flagship) 資訊以計算母子利差
        sample_m = members[0]
        flagship_code = str(sample_m.get('flagship_code', '')).strip()

        flagship_record = None
        for m in members:
            if str(m.get('code', '')).strip() == flagship_code:
                flagship_record = m
                break

        if not flagship_record:
            # 若無明確母公司代號比對，找 role == FLAGSHIP 或以第一檔為基準
            for m in members:
                if str(m.get('role', '')).upper() == 'FLAGSHIP':
                    flagship_record = m
                    break
        if not flagship_record:
            flagship_record = members[0]

        parent_close = float(flagship_record.get('close', 0.0))
        parent_ma20 = float(flagship_record.get('ma20', parent_close))
        parent_ret_5d = float(flagship_record.get('ret_5d_pct', 0.0))
        parent_above_ma20 = (parent_close >= parent_ma20)

        # 集團內最高 5日漲幅
        max_ret_5d = max([float(m.get('ret_5d_pct', 0.0)) for m in members]) if members else 0.0

        # 3. 為各成員計算專屬欄位與角色判定
        for m in members:
            code = str(m.get('code', '')).strip()
            name = str(m.get('name', '')).strip()
            close = float(m.get('close', 0.0))
            ma20 = float(m.get('ma20', close))
            ret_5d = float(m.get('ret_5d_pct', 0.0))
            inst_1d = float(m.get('inst_1d_lots', 0.0))
            inst_5d = float(m.get('inst_5d_lots', 0.0))

            spread_vs_parent_pct = round(ret_5d - parent_ret_5d, 2)
            spread_tactical_status = evaluate_spread_tactical_status(
                spread_vs_parent_pct=spread_vs_parent_pct,
                parent_ret_5d=parent_ret_5d,
                parent_above_ma20=parent_above_ma20,
                member_ret_5d=ret_5d
            )

            # 同集團協同動能夥伴 (排除自己，最多取前 4 檔)
            sibling_leaders = [
                f"{act.get('code', '')} {act.get('name', '')}".strip()
                for act in active_members
                if str(act.get('code', '')).strip() != code
            ][:4]

            # 角色階層判定 (Hierarchy Role)
            # 條件一：高檔結帳提款
            if (close < ma20) and (inst_1d <= -300 or inst_5d <= -800 or ret_5d < -2.0):
                hierarchy_role = "⚠️【高檔結帳提款】"
            # 條件二：集團領頭羊主攻
            elif (code == flagship_code and close >= ma20) or (
                close >= ma20 and ret_5d == max_ret_5d and ret_5d > 0 and (parent_ret_5d - ret_5d < 1.0)
            ):
                hierarchy_role = "👑【集團領頭羊主攻】"
            # 條件三：母強子落後補漲
            elif (
                close >= ma20
                and (parent_ret_5d >= 1.5 or parent_above_ma20)
                and ((parent_ret_5d - ret_5d) >= 1.0)
            ):
                hierarchy_role = "🚀【母強子落後補漲】"
            # 條件四：集團協同齊漲
            else:
                hierarchy_role = "🟢【集團協同齊漲】"

            item = dict(m)
            item['group_total_count'] = group_total_count
            item['group_active_count'] = group_active_count
            item['group_resonance_ratio'] = group_resonance_ratio
            item['sibling_leaders'] = sibling_leaders
            item['sibling_leaders_str'] = " / ".join(sibling_leaders) if sibling_leaders else "集團獨立先鋒"
            item['spread_vs_parent_pct'] = spread_vs_parent_pct
            item['hierarchy_role'] = hierarchy_role
            item['spread_tactical_status'] = spread_tactical_status
            item['parent_ret_5d_pct'] = parent_ret_5d

            enriched_records.append(item)

    return enriched_records


def classify_dual_state_and_pricing(stock_info: dict, adaptive_params: dict = None) -> dict:
    """
    雙向狀態機分流與四大防線定價
    狀態分流：
      1. GROUP_IGNITION: 🟢【作帳點火・母子共振】(多方)
      2. GROUP_UNWIND:   🔴【集團結帳・順勢放空】(空方)
      3. NEUTRAL:        ⚪【中性觀望】
    四大防線嚴格保證數學單調性：
      - 多方: sl_price < entry_price < addon_price < tp1_price < tp2_price
      - 空方: tp2_price < tp1_price < addon_price < entry_price < sl_price
    """
    close = float(stock_info.get('close', stock_info.get('收盤價', 0.0)))
    open_price = float(stock_info.get('open_price', stock_info.get('open', close)))
    high = float(stock_info.get('high', close))
    low = float(stock_info.get('low', close))
    ma20 = float(stock_info.get('ma20', stock_info.get('20MA', close)))

    inst_1d = float(stock_info.get('inst_1d_lots', stock_info.get('法人當日買賣超(張)', 0.0)))
    inst_5d = float(stock_info.get('inst_5d_lots', stock_info.get('法人5日買賣超(張)', 0.0)))
    ret_5d = float(stock_info.get('ret_5d_pct', stock_info.get('近5日漲跌幅%', 0.0)))

    # K 棒上下影線佔比
    candle_range = max(high - low, 0.01)
    upper_shadow = high - max(open_price, close)
    lower_shadow = min(open_price, close) - low
    upper_shadow_pct = round(max(0.0, upper_shadow / candle_range) * 100.0, 2)
    lower_shadow_pct = round(max(0.0, lower_shadow / candle_range) * 100.0, 2)

    force_long = bool(stock_info.get('force_long', False))

    # 1. 多方 GROUP_IGNITION 條件判斷
    # 型態維度只保留「上影線 < 45%」(墓碑線出貨) 為硬防守；「收黑且下影線不足」改為 candle_weak 旗標，
    # 由軟評分扣分 (法人明明在買卻因一根收黑 K 被整檔排除，過於武斷)。
    candle_weak = not (close >= open_price or lower_shadow_pct >= 25.0)
    is_ignition = (
        (close >= ma20)
        and (inst_1d >= 200 or (inst_5d >= 500 and inst_1d > 0))
        and (upper_shadow_pct < 45.0)
    )

    # 2. 空方 GROUP_UNWIND 條件判斷
    is_unwind = (
        (close < ma20)
        and (inst_1d <= -300 or inst_5d <= -800)
        and (close < open_price or upper_shadow_pct >= 35.0 or ret_5d <= -2.0)
    )

    if force_long and not is_ignition and not is_unwind:
        is_ignition = True

    if is_ignition:
        state_code = "GROUP_IGNITION"
        state_badge = "🟢【作帳點火・母子共振】"

        # 自適應市場位階動態停利停損係數
        tp1_mult = 1.0 + (float(adaptive_params.get("tp1_pct", 7.0)) / 100.0) if adaptive_params else 1.07
        tp2_mult = 1.0 + (float(adaptive_params.get("tp2_pct", 14.0)) / 100.0) if adaptive_params else 1.14
        sl_pct_raw = float(adaptive_params.get("stop_loss_pct", -4.5)) if adaptive_params else -4.5
        sl_mult = 1.0 - (abs(sl_pct_raw) / 100.0)

        # 價差戰術加乘：若為落後補漲，目標空間向上延展；若正乖離過大防母提款，縮緊停利防線
        spread_status = stock_info.get('spread_tactical_status', '')
        if spread_status == "🚀【低基期落後補漲】":
            tp1_mult = max(tp1_mult, 1.08)
            tp2_mult = max(tp2_mult, 1.16)
        elif spread_status == "⚠️【正乖離過大・防母提款】":
            tp1_mult = min(tp1_mult, 1.05)

        entry_price = round(close, 2)
        addon_price = round(max(high * 1.01, close * 1.025, entry_price + 0.5), 2)
        tp1_price = round(max(addon_price * 1.03, close * tp1_mult, addon_price + 0.5), 2)
        tp2_price = round(max(tp1_price * 1.04, close * tp2_mult, tp1_price + 0.5), 2)
        sl_price = round(min(low * 0.985, ma20 * 0.985, close * sl_mult, entry_price - 0.5), 2)

        # 嚴格數學單調性保護
        if addon_price <= entry_price:
            addon_price = round(entry_price + 0.5, 2)
        if tp1_price <= addon_price:
            tp1_price = round(addon_price + 0.5, 2)
        if tp2_price <= tp1_price:
            tp2_price = round(tp1_price + 0.5, 2)
        if sl_price >= entry_price:
            sl_price = round(entry_price - 0.5, 2)

        return {
            "state_code": state_code,
            "state_badge": state_badge,
            "badge": state_badge,
            "多空狀態": state_badge,
            "entry_price": entry_price,
            "addon_price": addon_price,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "sl_price": sl_price,
            "candle_weak": candle_weak,
            "upper_shadow_pct": upper_shadow_pct,
            "lower_shadow_pct": lower_shadow_pct
        }

    elif is_unwind:
        state_code = "GROUP_UNWIND"
        state_badge = "🔴【集團結帳・順勢放空】"

        entry_price = round(close, 2)
        addon_price = round(min(low * 0.995, close * 0.975, entry_price - 0.5), 2)
        tp1_price = round(min(addon_price * 0.96, close * 0.92, addon_price - 0.5), 2)
        tp2_price = round(min(tp1_price * 0.94, close * 0.85, tp1_price - 0.5), 2)
        sl_price = round(max(ma20 * 1.01, high * 1.01, close * 1.035, entry_price + 0.5), 2)

        # 嚴格數學單調性保護: tp2 < tp1 < addon < entry < sl
        if addon_price >= entry_price:
            addon_price = round(entry_price - 0.5, 2)
        if tp1_price >= addon_price:
            tp1_price = round(addon_price - 0.5, 2)
        if tp2_price >= tp1_price:
            tp2_price = round(tp1_price - 0.5, 2)
        if sl_price <= entry_price:
            sl_price = round(entry_price + 0.5, 2)

        return {
            "state_code": state_code,
            "state_badge": state_badge,
            "badge": state_badge,
            "多空狀態": state_badge,
            "entry_price": entry_price,
            "addon_price": addon_price,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "sl_price": sl_price,
            "upper_shadow_pct": upper_shadow_pct,
            "lower_shadow_pct": lower_shadow_pct
        }

    else:
        return {
            "state_code": "NEUTRAL",
            "state_badge": "⚪【中性觀望】",
            "badge": "⚪【中性觀望】",
            "多空狀態": "⚪【中性觀望】",
            "entry_price": round(close, 2),
            "addon_price": round(close, 2),
            "tp1_price": round(close, 2),
            "tp2_price": round(close, 2),
            "sl_price": round(close, 2),
            "upper_shadow_pct": upper_shadow_pct,
            "lower_shadow_pct": lower_shadow_pct
        }


def calculate_group_soft_score(
    item: dict,
    calendar_meta: dict,
    overnight_guard: dict = None,
    fut_pricing: dict = None,
    group_rank: int = None,
    adaptive_params: dict = None
) -> tuple:
    """
    二階段 100 分制集團作帳綜合軟評分矩陣
    評分維度：
      1. 基準分: 50 分
      2. 時序紅利 (season_bonus / unwind_bonus): 5 ~ 15 分
      3. 集團共振率 (group_resonance_ratio): >= 50% (+15), >= 30% (+10), >= 20% (+5)
      4. 法人買超動能: inst_1d >= 1000 (+12), >= 500 (+8), >= 200 (+5); 空方結帳 inst_1d <= -800 (+12)
      5. 角色加分: 領頭羊主攻 (+10), 母強子補漲 (+8), 齊漲 (+5)
      6. 隔日沖風控扣分: -5 ~ -15
      7. 股期 OI 深度流動性加分: +5
      8. 集團作帳熱度名次 (group_rank, 僅多方): 第1/2/3名 +6/+4/+2
      9. 型態扣分 (candle_weak, 僅多方): 收黑且下影線 < 25% 扣 10 分；法人當日買超 >= 1000 張扣 5 分
    回傳: (score: int, stars: str)
    """
    score = 50

    season_weight = float(adaptive_params.get('season_focus_weight', 1.0)) if adaptive_params else 1.0
    state_code = item.get('state_code', 'NEUTRAL')
    if state_code == 'GROUP_IGNITION':
        score += int(round(calendar_meta.get('season_bonus', 5) * season_weight))
    elif state_code == 'GROUP_UNWIND':
        score += int(round(calendar_meta.get('unwind_bonus', 5) * season_weight))
    else:
        score += 3

    # 價差戰術地位加減分 (落後補漲 vs 過熱提款)
    spread_tactical = item.get('spread_tactical_status', '')
    if spread_tactical == "🚀【低基期落後補漲】":
        score += 5
    elif spread_tactical == "⚠️【正乖離過大・防母提款】":
        score -= 5

    # 季末結帳出貨防禦 (Quarter-End Unwind Defense)
    # 季末警戒期若個股短線大漲 (ret_5d >= 8%) 且爆量收黑或長上影線，嚴扣 15 分防最後一棒追高
    phase_code = calendar_meta.get('phase_code', '')
    ret_5d = float(item.get('ret_5d_pct', 0.0))
    upper_shadow = float(item.get('upper_shadow_pct', 0.0))
    if phase_code == 'UNWIND_WARNING_ZONE' and state_code == 'GROUP_IGNITION':
        if ret_5d >= 8.0 and (upper_shadow >= 30.0 or item.get('candle_weak')):
            score -= 15
            item['unwind_warning'] = True

    # 共振率加分
    res_ratio = float(item.get('group_resonance_ratio', 0.0))
    if res_ratio >= 60.0:
        score += 15
    elif res_ratio >= 40.0:
        score += 10
    elif res_ratio >= 20.0:
        score += 6

    # 法人動能
    inst_1d = float(item.get('inst_1d_lots', 0.0))
    if state_code == 'GROUP_IGNITION':
        if inst_1d >= 1000:
            score += 12
        elif inst_1d >= 500:
            score += 8
        elif inst_1d >= 200:
            score += 5
    elif state_code == 'GROUP_UNWIND':
        if inst_1d <= -1000:
            score += 12
        elif inst_1d <= -500:
            score += 8
        elif inst_1d <= -200:
            score += 5

    # 角色加分
    role = str(item.get('hierarchy_role', ''))
    if "領頭羊" in role:
        score += 10
    elif "補漲" in role:
        score += 8
    elif "協同" in role:
        score += 5
    elif "結帳" in role:
        score += 8

    # 隔日沖風控檢核
    if overnight_guard and overnight_guard.get('risk_level') == 'HIGH':
        score -= int(overnight_guard.get('penalty_score', 10))

    # 期貨 OI 加分
    if fut_pricing and fut_pricing.get('has_futures'):
        oi = int(fut_pricing.get('oi', 0))
        if oi >= 2000:
            score += 5

    # 型態扣分：多方標的收黑且下影線不足 (candle_weak)，不再硬性排除，改為扣分；
    # 法人當日大買 (>= 1000 張) 視為有承接，只扣 5 分，否則扣 10 分
    if state_code == 'GROUP_IGNITION' and item.get('candle_weak'):
        score -= 5 if inst_1d >= 1000 else 10

    # 集團作帳熱度名次加分 (僅多方點火標的；空方結帳不因集團熱門而加分)
    if group_rank in (1, 2, 3) and state_code == 'GROUP_IGNITION':
        score += {1: 6, 2: 4, 3: 2}[group_rank]

    score = max(30, min(99, score))

    if score >= 85:
        stars = "★★★★★"
    elif score >= 75:
        stars = "★★★★☆"
    elif score >= 65:
        stars = "★★★☆☆"
    elif score >= 55:
        stars = "★★☆☆☆"
    else:
        stars = "★☆☆☆☆"

    return score, stars


def apply_screening_with_fallback(candidates_raw: list, adaptive_bias_max: float = 8.0) -> tuple:
    """
    執行 4+1 五維度硬性濾網，若 Tier-1 為 0 檔，自動降級啟動 Tier-2 集團潛伏候補
    條件規範：
      Tier-1 (TIER1_RESONANCE):
        - state_code in ("GROUP_IGNITION", "GROUP_UNWIND")
        - avg_vol_5d >= 1000
        - abs(bias20_pct) <= 8.0
        - 若為 GROUP_IGNITION，額外要求 group_active_count >= 2
      Tier-2 (TIER2_GROUP_WATCH) Fallback:
        - state_code in ("GROUP_IGNITION", "GROUP_UNWIND") (或 close >= ma20 且 法人 > 0 自動賦予多方定價)
        - avg_vol_5d >= 600
        - abs(bias20_pct) <= 10.0
        - 每筆標註 tier_level = "⚠️ Tier-2 集團潛伏候補"
        - 最多取前 6 檔
    回傳: (filtered_list: list, tier_mode: str)
    """
    tier1_list = []

    for c in candidates_raw:
        code = str(c.get('code', c.get('證券代號', ''))).strip()
        if not (code.isdigit() and len(code) == 4):
            continue

        vol_5d = float(c.get('avg_vol_5d') or c.get('vol_5d_lots') or c.get('5日均量(張)') or 0)
        if vol_5d < 1000:
            continue

        close = float(c.get('close', c.get('收盤價', 0)))
        ma20 = float(c.get('ma20', c.get('20MA', 0)))
        if ma20 > 0 and close > 0:
            bias20 = round(((close - ma20) / ma20) * 100.0, 2)
        else:
            bias20 = float(c.get('bias20_pct', c.get('月線乖離%', 0.0)))

        bias_cap = max(3.0, min(8.0, float(adaptive_bias_max))) if adaptive_bias_max is not None else 8.0
        if abs(bias20) > bias_cap:
            continue

        state_code = str(c.get('state_code', 'NEUTRAL'))
        if state_code not in ("GROUP_IGNITION", "GROUP_UNWIND"):
            continue

        group_active_cnt = int(c.get('group_active_count', 0))
        if state_code == "GROUP_IGNITION" and group_active_cnt < 2:
            continue

        item = dict(c)
        item['tier_mode'] = "TIER1_RESONANCE"
        item['tier_level'] = "🎯 Tier-1 母子共振精選"
        item['門檻層級'] = "🎯 Tier-1 母子共振精選"
        item['bias20_pct'] = bias20
        item['月線乖離%'] = bias20
        tier1_list.append(item)

    if tier1_list:
        tier1_list.sort(
            key=lambda x: (
                float(x.get('score', 0)),
                float(x.get('group_resonance_ratio', 0)),
                abs(float(x.get('inst_1d_lots', 0)))
            ),
            reverse=True
        )
        return tier1_list, "TIER1_RESONANCE"

    # 若 Tier-1 為 0 檔，自動降級啟動 Tier-2
    return _build_tier2_candidates(candidates_raw, adaptive_bias_max=adaptive_bias_max)[:6], "TIER2_GROUP_WATCH"


def _build_tier2_candidates(candidates_raw: list, exclude_codes=(), rescore=None, adaptive_bias_max: float = 8.0) -> list:
    """
    Tier-2 集團潛伏候補 (放寬版硬篩)：5日均量 >= 600、|Bias20| <= 10。
    非 IGNITION/UNWIND 但站上月線且法人買超者，強制賦予多方四大防線 (force_long)。
    rescore: 可選回呼，於強制定價後重算該標的分數/股期價位，讓後續「依分數取前 N 檔」使用正確分數
             (否則會沿用 NEUTRAL 狀態時的舊分數，法人買超越多反而排越後面)。
    exclude_codes: 已入選 Tier-1 之代號，避免重複入列。回傳依分數排序之完整清單 (不截斷)。
    """
    exclude = {str(c).strip() for c in exclude_codes}
    tier2_list = []
    tier2_bias_cap = min(10.0, max(5.0, float(adaptive_bias_max) + 1.5)) if adaptive_bias_max is not None else 10.0
    for c in candidates_raw:
        code = str(c.get('code', c.get('證券代號', ''))).strip()
        if not (code.isdigit() and len(code) == 4) or code in exclude:
            continue

        vol_5d = float(c.get('avg_vol_5d') or c.get('vol_5d_lots') or c.get('5日均量(張)') or 0)
        if vol_5d < 600:
            continue

        close = float(c.get('close', c.get('收盤價', 0)))
        ma20 = float(c.get('ma20', c.get('20MA', 0)))
        if ma20 > 0 and close > 0:
            bias20 = round(((close - ma20) / ma20) * 100.0, 2)
        else:
            bias20 = float(c.get('bias20_pct', c.get('月線乖離%', 0.0)))

        if abs(bias20) > tier2_bias_cap:
            continue

        c_copy = dict(c)
        state_code = str(c_copy.get('state_code', 'NEUTRAL'))
        inst_1d = float(c_copy.get('inst_1d_lots', 0.0))
        inst_5d = float(c_copy.get('inst_5d_lots', 0.0))

        if state_code not in ("GROUP_IGNITION", "GROUP_UNWIND"):
            if close >= ma20 and (inst_1d > 0 or inst_5d > 0):
                c_copy['force_long'] = True
                st = classify_dual_state_and_pricing(c_copy)
                c_copy.update(st)
            else:
                continue

        c_copy['tier_mode'] = "TIER2_GROUP_WATCH"
        c_copy['tier_level'] = "⚠️ Tier-2 集團潛伏候補"
        c_copy['門檻層級'] = "⚠️ Tier-2 集團潛伏候補"
        c_copy['bias20_pct'] = bias20
        c_copy['月線乖離%'] = bias20
        if rescore is not None and c_copy.get('force_long'):
            rescore(c_copy)
        tier2_list.append(c_copy)

    tier2_list.sort(
        key=lambda x: (
            float(x.get('score', 0)),
            float(x.get('group_resonance_ratio', 0)),
            abs(float(x.get('inst_1d_lots', 0)))
        ),
        reverse=True
    )
    return tier2_list


def apply_screening_parallel(candidates_raw: list, tier2_cap: int = 6, rescore=None, adaptive_bias_max: float = 8.0) -> tuple:
    """
    Tier-1 精選與 Tier-2 潛伏候補「並行」輸出 (apply_screening_with_fallback 則是 Tier-1 為 0 才降級)。
    Why: 單純降級時，只要 Tier-1 有 1 檔，Tier-2 就永遠沒有機會出手，站上月線且法人買超、
    但差一個條件 (如月乖離 8~10%、同集團共振不足) 的標的整天都看不到。並行後 Tier-2 作為
    「補位觀察名單」，與 Tier-1 以層級標籤區隔 (Tier-2 最多 tier2_cap 檔，不與 Tier-1 重複)。
    回傳 (filtered_list, tier_mode)，tier_mode 為 'TIER1_PLUS_TIER2' / 'TIER1_RESONANCE' / 'TIER2_GROUP_WATCH'。
    """
    first_list, first_mode = apply_screening_with_fallback(candidates_raw, adaptive_bias_max=adaptive_bias_max)
    tier1_list = first_list if first_mode == "TIER1_RESONANCE" else []

    tier2_list = _build_tier2_candidates(
        candidates_raw,
        exclude_codes=[str(it.get('code', '')).strip() for it in tier1_list],
        rescore=rescore,
        adaptive_bias_max=adaptive_bias_max,
    )[:max(0, int(tier2_cap))]

    if tier1_list and tier2_list:
        return tier1_list + tier2_list, "TIER1_PLUS_TIER2"
    if tier1_list:
        return tier1_list, "TIER1_RESONANCE"
    return tier2_list, "TIER2_GROUP_WATCH"


def fetch_group_market_data(group_pool: list) -> list:
    """
    批次抓取集團池成分股的最新行情、均線、法人買賣超與型態指標
    優先使用 yfinance 批次並行拉取 1 個月日 K 線，計算穩健指標
    並向 TWSE T86 / MI_INDEX 嘗試抓取最新法人籌碼
    """
    all_members = []
    code_meta = {}
    for g in group_pool:
        gid = g.get('group_id')
        gname = g.get('group_name')
        fcode = g.get('flagship_code')
        fname = g.get('flagship_name')
        for m in g.get('members', []):
            code = str(m.get('code')).strip()
            name = str(m.get('name')).strip()
            role = str(m.get('role', 'SUBSIDIARY')).strip()
            meta_item = {
                'code': code,
                'name': name,
                'role': role,
                'group_id': gid,
                'group_name': gname,
                'flagship_code': fcode,
                'flagship_name': fname
            }
            code_meta[code] = meta_item
            all_members.append(meta_item)

    unique_codes = list(code_meta.keys())
    print(f"[*] 正在獲取 25 大集團共 {len(unique_codes)} 檔成分股之歷史日 K 線與籌碼...")

    # yfinance 批次代碼轉換 (台股上市 .TW / 上櫃 .TWO，若無則依序嘗試)
    yf_tickers = [f"{c}.TW" for c in unique_codes]
    data_df = None
    try:
        data_df = yf.download(yf_tickers, period="1mo", interval="1d", progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] yfinance 批次下載異常: {e}")

    # 若有缺失個股（例如上櫃股 .TWO），單檔嘗試補充
    results = []
    for code in unique_codes:
        meta = code_meta[code]
        df_stock = None
        if data_df is not None and f"{code}.TW" in data_df:
            df_stock = data_df[f"{code}.TW"].dropna(how='all')

        if df_stock is None or df_stock.empty or len(df_stock) < 5:
            # 嘗試 .TWO
            try:
                df_stock = yf.download(f"{code}.TWO", period="1mo", interval="1d", progress=False)
                if isinstance(df_stock.columns, pd.MultiIndex):
                    df_stock = df_stock.droplevel(1, axis=1)
            except Exception:
                pass

        if df_stock is None or df_stock.empty or len(df_stock) < 3:
            continue

        try:
            close_s = df_stock['Close']
            open_s = df_stock['Open']
            high_s = df_stock['High']
            low_s = df_stock['Low']
            vol_s = df_stock['Volume']

            close = float(close_s.iloc[-1])
            open_p = float(open_s.iloc[-1])
            high = float(high_s.iloc[-1])
            low = float(low_s.iloc[-1])
            volume_shares = float(vol_s.iloc[-1])
            volume_lots = int(volume_shares // 1000)

            # 20MA
            ma20 = float(close_s.rolling(20, min_periods=5).mean().iloc[-1])
            # 5日均量 (張)
            vol_5d_lots = int(vol_s.rolling(5, min_periods=3).mean().iloc[-1] // 1000)

            # 近 5 日漲跌幅 %
            p_5d_ago = float(close_s.iloc[-5]) if len(close_s) >= 5 else float(close_s.iloc[0])
            ret_5d_pct = round(((close - p_5d_ago) / p_5d_ago) * 100.0, 2) if p_5d_ago > 0 else 0.0

            # 月線乖離率 %
            bias20_pct = round(((close - ma20) / ma20) * 100.0, 2) if ma20 > 0 else 0.0

            # K 棒上下影線
            candle_range = max(high - low, 0.01)
            upper_shadow = high - max(open_p, close)
            lower_shadow = min(open_p, close) - low
            upper_shadow_pct = round(max(0.0, upper_shadow / candle_range) * 100.0, 2)
            lower_shadow_pct = round(max(0.0, lower_shadow / candle_range) * 100.0, 2)

            # 法人買賣超估算 (結合成交量與價量漲跌，若有 TWSE T86 則覆蓋)
            # 基礎由量能與 K 棒實體推算估計值，確保永不掛零
            price_change_ratio = (close - open_p) / open_p if open_p > 0 else 0.0
            est_inst_1d = int(volume_lots * 0.18 * np.sign(price_change_ratio)) if abs(price_change_ratio) >= 0.005 else 0
            est_inst_5d = int(est_inst_1d * 3.2 + volume_lots * 0.1)

            rec = dict(meta)
            rec.update({
                'close': close,
                'open': open_p,
                'open_price': open_p,
                'high': high,
                'low': low,
                'ma20': ma20,
                'volume_lots': volume_lots,
                'avg_vol_5d': vol_5d_lots,
                'vol_5d_lots': vol_5d_lots,
                'ret_5d_pct': ret_5d_pct,
                'bias20_pct': bias20_pct,
                'upper_shadow_pct': upper_shadow_pct,
                'lower_shadow_pct': lower_shadow_pct,
                'inst_1d_lots': est_inst_1d,
                'inst_5d_lots': est_inst_5d
            })
            results.append(rec)
        except Exception as e:
            continue

    return results


# ============================================================================
# V2.0 升級模組：真實法人 (T86)、集團作帳熱度排行、集團聚合排序與戰報
# ============================================================================
def parse_t86_payload(payload: dict) -> dict:
    """
    解析證交所 T86 JSON，回傳 {代號: 三大法人買賣超(張)}。
    Why: 原本法人數據由量價推估 (成交量 x 18% x 漲跌方向)，會讓「籌碼」濾網與漲跌高度共線，
    改用官方 T86 才是真正獨立的籌碼維度。股數除以 1000 並向 0 取整，非 4 碼普通股略過。
    """
    result = {}
    if not isinstance(payload, dict) or payload.get('stat') != 'OK':
        return result
    for row in payload.get('data', []) or []:
        try:
            code = str(row[0]).strip()
            if not (len(code) == 4 and code.isdigit()):
                continue
            total_shares = int(str(row[18]).replace(',', ''))
            result[code] = int(total_shares / 1000)
        except (ValueError, IndexError, TypeError):
            continue
    return result


def fetch_real_institutional(n_days: int = 5, max_lookback: int = 14) -> tuple:
    """
    抓取最近 n_days 個有資料之交易日 T86，回傳 ({代號: {'inst_1d_lots','inst_5d_lots'}}, 資料日清單)。
    當日資料尚未公布 (盤後 16:00 前) 時該日回傳空，自然略過並往前補足天數。
    任一日抓取失敗不中斷，全部失敗則回傳空 dict，由呼叫端維持量價推估值。
    """
    import time
    today = datetime.date.today()
    per_day = []
    for i in range(max_lookback):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        d_str = d.strftime('%Y%m%d')
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                parsed = parse_t86_payload(json.loads(resp.read().decode('utf-8')))
            if parsed:
                per_day.append((d_str, parsed))
        except Exception as e:
            print(f"[!] T86 {d_str} 抓取失敗: {e}")
        if len(per_day) >= n_days:
            break
        time.sleep(0.4)

    chips = {}
    for idx, (_, parsed) in enumerate(per_day):
        for code, lots in parsed.items():
            slot = chips.setdefault(code, {'inst_1d_lots': 0, 'inst_5d_lots': 0})
            if idx == 0:
                slot['inst_1d_lots'] = lots
            slot['inst_5d_lots'] += lots
    return chips, [d for d, _ in per_day]


def apply_real_institutional(records: list, chips: dict) -> int:
    """以 T86 覆蓋 records 內法人欄位並標註來源 (T86 / EST)，回傳被真實數據覆蓋的檔數。"""
    hit = 0
    for rec in records or []:
        c = chips.get(str(rec.get('code', '')).strip()) if chips else None
        if c:
            rec['inst_1d_lots'] = c['inst_1d_lots']
            rec['inst_5d_lots'] = c['inst_5d_lots']
            rec['inst_source'] = 'T86'
            hit += 1
        else:
            rec['inst_source'] = 'EST'
    return hit


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def calculate_group_ranking(records: list, group_pool: list = None) -> list:
    """
    當日集團作帳熱度排行 (輸入為已含 state_code 的全集團成分股，而非僅入選股，才能公平比較各集團)。
    熱度分 (上限 100) = 共振率x0.35 + 5日均漲動能(0~25) + 法人淨買佔量比(0~25)
                        + 點火家數占比(0~15) + 歷史作帳權重(0~5)
    Why: 只看入選檔數會讓成分股多的集團天生佔優；改為比率化指標並納入法人資金流向，
    才能反映「哪個集團今天資金最集中」。分母皆有防呆，空輸入回傳 []。
    回傳依熱度分由高至低排序之 list，每筆含 rank 與各項分項指標。
    """
    weights = {}
    for g in group_pool or []:
        try:
            weights[str(g.get('group_id', '')).strip()] = float(g.get('historical_weight', 1.0) or 1.0)
        except (TypeError, ValueError):
            weights[str(g.get('group_id', '')).strip()] = 1.0

    grouped = {}
    for r in records or []:
        gid = str(r.get('group_id', '')).strip()
        if gid:
            grouped.setdefault(gid, []).append(r)

    ranking = []
    for gid, ms in grouped.items():
        n = len(ms)
        if n <= 0:
            continue

        def _active(m):
            close = float(m.get('close', 0.0) or 0.0)
            ma20 = float(m.get('ma20', close) or close)
            return close >= ma20 and (
                float(m.get('inst_1d_lots', 0) or 0) > 0
                or float(m.get('inst_5d_lots', 0) or 0) > 0
                or float(m.get('ret_5d_pct', 0) or 0) > 0
            )

        active = [m for m in ms if _active(m)]
        resonance = round(len(active) / n * 100.0, 2)
        avg_ret = round(sum(float(m.get('ret_5d_pct', 0) or 0) for m in ms) / n, 2)
        net_1d = int(sum(float(m.get('inst_1d_lots', 0) or 0) for m in ms))
        net_5d = int(sum(float(m.get('inst_5d_lots', 0) or 0) for m in ms))
        vol_base = max(sum(float(m.get('avg_vol_5d', 0) or 0) for m in ms), 1.0)
        flow_pct = round(net_1d / vol_base * 100.0, 2)
        ignition = sum(1 for m in ms if m.get('state_code') == 'GROUP_IGNITION')
        unwind = sum(1 for m in ms if m.get('state_code') == 'GROUP_UNWIND')

        flagship_code = str(ms[0].get('flagship_code', '')).strip()
        flagship = next((m for m in ms if str(m.get('code', '')).strip() == flagship_code), None)
        flagship_ret = round(float(flagship.get('ret_5d_pct', 0) or 0), 2) if flagship else None

        w = weights.get(gid, 1.0)
        heat = (
            resonance * 0.35
            + _clamp((avg_ret + 3.0) / 9.0, 0.0, 1.0) * 25.0
            + _clamp((flow_pct + 5.0) / 10.0, 0.0, 1.0) * 25.0
            + (ignition / n) * 15.0
            + _clamp((w - 1.0) * 10.0, 0.0, 5.0)
        )
        heat = round(_clamp(heat, 0.0, 100.0), 1)

        if unwind > ignition and avg_ret < 0 and resonance < 60.0:
            status = "⚠️ 結帳提款"
        elif heat >= 65:
            status = "🔥 作帳主攻"
        elif heat >= 45:
            status = "🟡 作帳醞釀"
        else:
            status = "🧊 觀望冷卻"

        movers = sorted(active, key=lambda m: float(m.get('ret_5d_pct', 0) or 0), reverse=True)[:3]
        total_turnover = sum(
            float(m.get('close', 0.0) or 0.0) * float(m.get('volume_lots', m.get('avg_vol_5d', 0.0)) or 0.0) * 1000.0
            for m in ms
        )
        turnover_billion = round(total_turnover / 100000000.0, 2)

        ranking.append({
            'group_id': gid,
            'group_name': str(ms[0].get('group_name', gid)),
            'flagship_code': flagship_code,
            'flagship_name': str(ms[0].get('flagship_name', '')),
            'member_count': n,
            'active_count': len(active),
            'resonance_ratio': resonance,
            'avg_ret_5d_pct': avg_ret,
            'flagship_ret_5d_pct': flagship_ret,
            'net_inst_1d_lots': net_1d,
            'net_inst_5d_lots': net_5d,
            'flow_pct': flow_pct,
            'turnover_billion_ntd': turnover_billion,
            'ignition_count': ignition,
            'unwind_count': unwind,
            'heat_score': heat,
            'status_badge': status,
            'top_movers': [f"{m.get('code', '')} {m.get('name', '')}".strip() for m in movers],
        })

    ranking.sort(key=lambda x: (-x['heat_score'], -x['resonance_ratio'], -x['net_inst_1d_lots'], x['group_name']))
    for i, g in enumerate(ranking, 1):
        g['rank'] = i
    return ranking


def cluster_items_by_group_rank(items: list, ranking: list) -> list:
    """
    讓入選個股依集團排名集中排列：集團名次 → 多方在前空方在後 → 母子角色
    (領頭羊 > 補漲 > 協同 > 結帳) → 評分高低 → 代號 (確保輸出順序穩定可重現)。
    """
    rank_map = {g['group_id']: g['rank'] for g in ranking or []}
    role_keys = ("領頭羊", "補漲", "協同", "結帳")

    def _role_order(it):
        role = str(it.get('hierarchy_role', it.get('role', '')))
        for i, k in enumerate(role_keys):
            if k in role:
                return i
        return len(role_keys)

    def _key(it):
        gid = str(it.get('group_id', '')).strip()
        return (
            rank_map.get(gid, 9999),
            0 if it.get('state_code') == 'GROUP_IGNITION' else 1,
            1 if "Tier-2" in str(it.get('tier_level', '')) else 0,
            _role_order(it),
            -float(it.get('score', 0) or 0),
            str(it.get('code', '')),
        )

    return sorted(items or [], key=_key)


_RANK_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _fmt_signed(v, digits: int = 1) -> str:
    return f"{v:+,.{digits}f}"


def build_group_markdown_report(ranking: list, items: list, calendar_meta: dict, date_str: str,
                                tier_mode: str, cards_md: str = "",
                                inst_real_count: int = 0, inst_total_count: int = 0,
                                adaptive_cfg: dict = None) -> str:
    """result.md：① 當日集團作帳排行榜 ② 各集團入選個股集中排列 ③ 個股完整字卡。"""
    n_t2 = sum(1 for it in items or [] if "Tier-2" in str(it.get('tier_level', '')))
    n_t1 = len(items or []) - n_t2
    tier_label = {
        "TIER1_RESONANCE": "🎯 Tier-1 母子共振精選",
        "TIER2_GROUP_WATCH": "⚠️ Tier-2 集團潛伏候補",
        "TIER1_PLUS_TIER2": "🎯 Tier-1 精選 + ⚠️ Tier-2 潛伏候補並行",
    }.get(tier_mode, str(tier_mode)) + f" (T1 {n_t1} 檔 / T2 {n_t2} 檔)"
    picks_by_group = {}
    for it in items or []:
        picks_by_group.setdefault(str(it.get('group_id', '')).strip(), []).append(it)

    regime_info = (adaptive_cfg or {}).get('market_regime', {})
    global_ctrl = (adaptive_cfg or {}).get('global_controls', {})
    regime_label = regime_info.get('label', '🟡【高檔震盪整理・箱型洗盤】')
    tactical_txt = global_ctrl.get('tactical_guidance', '維持紀律，嚴守 4+1 維度防線。')

    lines = [
        f"# 🏆 策略 08：集團作帳日報 ({date_str})",
        "",
        f"- **🌐 大盤位階**：{regime_label} ｜ 模式：`{global_ctrl.get('mode', '標準')}` (建議現金 `{global_ctrl.get('cash_target_pct', 35)}%`)",
        f"- **💡 操盤指引**：{tactical_txt}",
        f"- **📅 作帳階段**：{calendar_meta.get('phase_badge', '')}",
        f"- **🎯 選股模式**：{tier_label} ｜ 入選 `{len(items or [])}` 檔 / `{len(picks_by_group)}` 個集團",
        f"- **📊 法人資料**：證交所 T86 真實數據覆蓋 `{inst_real_count}/{inst_total_count}` 檔 (上櫃股無 T86，以量價推估)",
        "",
        "## 🏆 今日集團作帳熱度排行榜",
        "",
        "| 名次 | 集團 | 熱度 | 狀態 | 成交額(億) | 共振 | 5日均漲 | 法人淨買(張) | 旗艦母股 | 入選 |",
        "| :-: | :-- | :-: | :-- | --: | :-: | :-: | --: | :-- | :-: |",
    ]
    for g in ranking or []:
        medal = _RANK_MEDALS.get(g['rank'], f"{g['rank']}")
        flag_ret = g.get('flagship_ret_5d_pct')
        flag_txt = f"{g['flagship_code']} {g['flagship_name']}" + (f" ({_fmt_signed(flag_ret)}%)" if flag_ret is not None else "")
        picked = len(picks_by_group.get(g['group_id'], []))
        turnover_txt = f"{g.get('turnover_billion_ntd', 0.0):.1f}"
        lines.append(
            f"| {medal} | {g['group_name']} | **{g['heat_score']}** | {g['status_badge']} | "
            f"{turnover_txt} | {g['active_count']}/{g['member_count']} ({g['resonance_ratio']:.0f}%) | "
            f"{_fmt_signed(g['avg_ret_5d_pct'], 2)}% | {g['net_inst_1d_lots']:+,} | {flag_txt} | {picked} |"
        )

    lines += ["", "> 熱度分 = 共振率 + 5日均漲動能 + 法人淨買佔量比 + 點火家數占比 + 歷史作帳權重 (滿分 100)。", "",
              "---", "", "## 🧩 各集團入選個股 (依排行集中排列)", ""]
    shown = False
    for g in ranking or []:
        group_items = picks_by_group.get(g['group_id'], [])
        if not group_items:
            continue
        shown = True
        medal = _RANK_MEDALS.get(g['rank'], "📌")
        lines += [
            f"### {medal} 第 {g['rank']} 名｜{g['group_name']}｜熱度 {g['heat_score']}｜{g['status_badge']}",
            "",
            "| 個股 | 層級 | 母子角色 | 多空 | 進場 | 加碼 | TP1 | 停損 | 評分 |",
            "| :-- | :-: | :-- | :-- | --: | --: | --: | --: | :-: |",
        ]
        for it in group_items:
            tier_icon = "⚠️ T2" if "Tier-2" in str(it.get('tier_level', '')) else "🎯 T1"
            weak = " ·收黑扣分" if it.get('candle_weak') and it.get('state_code') == 'GROUP_IGNITION' else ""
            lines.append(
                f"| {it.get('code', '')} {it.get('name', '')} | {tier_icon} | {it.get('hierarchy_role', '')} | "
                f"{it.get('state_badge', '')} | {it.get('entry_price', 0):.2f} | {it.get('addon_price', 0):.2f} | "
                f"{it.get('tp1_price', 0):.2f} | {it.get('sl_price', 0):.2f} | {it.get('score', 0)} {it.get('stars', '')}{weak} |"
            )
        lines.append("")
    if not shown:
        lines += ["今日無符合條件之入選個股，維持資金安全。", ""]

    if cards_md:
        lines += ["---", "", "## 📇 個股完整四大防線字卡", "", cards_md]
    return "\n".join(lines)


def format_group_ranking_text(ranking: list, items: list, date_str: str,
                              calendar_meta: dict, top_n: int = 8) -> str:
    """LINE 純文字集團排行榜 (零 ASCII 引線、窄螢幕不折行碎裂)。"""
    picks_by_group = {}
    for it in items or []:
        picks_by_group.setdefault(str(it.get('group_id', '')).strip(), []).append(it)

    lines = [f"🏆【集團作帳排行榜】{date_str}", calendar_meta.get('phase_badge', ''), ""]
    for g in (ranking or [])[:top_n]:
        medal = _RANK_MEDALS.get(g['rank'], f"{g['rank']}.")
        lines.append(f"{medal} {g['group_name']}　熱度 {g['heat_score']}　{g['status_badge']}")
        t_bil = g.get('turnover_billion_ntd', 0.0)
        lines.append(
            f"　成交 {t_bil:.1f} 億｜共振 {g['active_count']}/{g['member_count']}｜5日均漲 {_fmt_signed(g['avg_ret_5d_pct'], 1)}%"
            f"｜法人淨買 {g['net_inst_1d_lots']:+,} 張"
        )
        picks = picks_by_group.get(g['group_id'], [])
        if picks:
            lines.append("　入選：" + "、".join(
                f"{p.get('code', '')} {p.get('name', '')}" + ("⚠️" if "Tier-2" in str(p.get('tier_level', '')) else "")
                for p in picks
            ))
        lines.append("")
    lines.append("💡 熱度越高代表集團資金越集中；結帳提款集團請勿追價。")
    return "\n".join(lines)


def run_screener(export_path: str = None, send_line_flag: bool = False) -> tuple:
    """
    執行策略 08 完整選股流水線：
    1. 載入 25 大集團池
    2. 評估當前時序階段 (Window Dressing Calendar)
    3. 獲取成分股行情與籌碼
    4. 計算母子共振率與階層角色
    5. 雙向狀態機分流與四大防線定價
    6. 軟評分與 4+1 五維度篩選 (含 Tier-2 降級)
    7. 整合期貨定價與隔日沖風控
    8. 產出 result.csv (44 欄位) 與 result.md
    9. 推播 LINE 卡片
    """
    print("=" * 75)
    print(f"🚀 啟動【{STRATEGY_NAME}】選股流水線")
    print("=" * 75)

    if export_path is None:
        export_path = CSV_FILE

    # 1. 載入集團池
    group_pool = load_group_pool()
    if not group_pool:
        print("[X] 錯誤：無法載入 group_pool.json！")
        return [], "ERROR"
    print(f"[✓] 成功載入 {len(group_pool)} 大集團池配置。")

    # 1.5 載入全域自適應操盤引擎參數 (Adaptive Market Regime Engine)
    adaptive_cfg = load_adaptive_config()
    adaptive_params = get_strategy_adaptive_params("08")
    adaptive_bias_max = float(adaptive_params.get("bias_max_pct", 8.0))
    regime_info = adaptive_cfg.get("market_regime", {})
    global_ctrl = adaptive_cfg.get("global_controls", {})
    print(f"[✓] 自適應大盤位階：{regime_info.get('label', '標準箱型')} | 模式：{global_ctrl.get('mode', '標準')} | 乖離防追高上限：{adaptive_bias_max}%")

    # 2. 評估作帳時序
    calendar_meta = evaluate_window_dressing_calendar()
    print(f"[✓] 當前台股時序狀態：{calendar_meta['phase_badge']} ({calendar_meta['phase_code']})")

    # 3. 獲取市場行情
    market_records = fetch_group_market_data(group_pool)
    print(f"[✓] 成功擷取 {len(market_records)} 檔成分股即時量化指標。")

    # 3.5 以證交所 T86 真實法人買賣超覆蓋量價推估值 (上櫃股 T86 無資料，維持推估並於戰報標註)
    chips, chip_dates = fetch_real_institutional()
    inst_real_count = apply_real_institutional(market_records, chips)
    print(f"[✓] 真實法人 (T86) 覆蓋 {inst_real_count}/{len(market_records)} 檔，資料日：{', '.join(chip_dates) or '無'}")

    # 4. 計算母子共振與角色
    resonance_records = calculate_group_resonance_and_hierarchy(market_records)

    # 4.5 當日集團作帳熱度排行 (預先以純狀態機分類，不依賴評分，避免評分與排行循環相依)
    pre_states = []
    for r in resonance_records:
        tmp = dict(r)
        tmp.update(classify_dual_state_and_pricing(r, adaptive_params=adaptive_params))
        pre_states.append(tmp)
    group_ranking = calculate_group_ranking(pre_states, group_pool)
    group_rank_map = {g['group_id']: g['rank'] for g in group_ranking}
    if group_ranking:
        top3 = "、".join(f"#{g['rank']} {g['group_name']}({g['heat_score']})" for g in group_ranking[:3])
        print(f"[✓] 今日集團作帳熱度前三：{top3}")

    # 5. 期貨輔助池載入
    futures_pool = load_stock_futures_pool()
    taifex_quotes = fetch_taifex_futures_quotes()

    # 6. 狀態機分類與定價
    def _enrich(merged: dict) -> dict:
        """依目前 state/定價計算隔日沖、股期定價與軟評分 (Tier-2 強制多方補位標的需於定價後重算)。"""
        code = str(merged.get('code', ''))
        overnight_guard = evaluate_overnight_broker_risk(code, merged)

        f_info = futures_pool.get(code, {'contract': '--', 'name': merged.get('name', '')})
        contract_base = f_info.get('contract', '--')
        fut_q = taifex_quotes.get(contract_base)
        is_long = (merged.get('state_code') == 'GROUP_IGNITION')

        fut_pricing = calculate_futures_pricing(
            spot_close=merged.get('close', 0.0),
            futures_quote=fut_q,
            entry_spot=merged.get('entry_price', 0.0),
            addon_spot=merged.get('addon_price', 0.0),
            tp1_spot=merged.get('tp1_price', 0.0),
            tp2_spot=merged.get('tp2_price', 0.0),
            sl_spot=merged.get('sl_price', 0.0),
            contract_base=contract_base,
            is_long=is_long
        )

        score, stars = calculate_group_soft_score(
            item=merged,
            calendar_meta=calendar_meta,
            overnight_guard=overnight_guard,
            fut_pricing=fut_pricing,
            group_rank=group_rank_map.get(str(merged.get('group_id', '')).strip()),
            adaptive_params=adaptive_params
        )

        merged['score'] = score
        merged['評分'] = score
        merged['stars'] = stars
        merged['星級推薦'] = stars
        merged['overnight_guard'] = overnight_guard
        merged['fut_pricing'] = fut_pricing
        return merged

    classified_records = []
    for r in resonance_records:
        merged = dict(r)
        merged.update(classify_dual_state_and_pricing(r, adaptive_params=adaptive_params))
        classified_records.append(_enrich(merged))

    # 7. 4+1 維度硬篩：Tier-1 精選與 Tier-2 潛伏候補並行
    filtered_list, tier_mode = apply_screening_parallel(classified_records, rescore=_enrich, adaptive_bias_max=adaptive_bias_max)
    n_t1 = sum(1 for it in filtered_list if it.get('tier_mode') == "TIER1_RESONANCE")
    n_t2 = len(filtered_list) - n_t1
    print(f"[✓] 篩選完成！入選檔數：{len(filtered_list)} 檔 (Tier-1 {n_t1} 檔 + Tier-2 {n_t2} 檔，模式: {tier_mode})")

    # 8. 構建標準輸出表：同集團個股集中排列，集團依當日作帳熱度排名由高至低
    rank_lookup = {g['group_id']: g for g in group_ranking}
    for it in filtered_list:
        g = rank_lookup.get(str(it.get('group_id', '')).strip(), {})
        it['group_rank'] = g.get('rank', 0)
        it['group_heat'] = g.get('heat_score', 0.0)
        it['group_status'] = g.get('status_badge', '')
    filtered_list = cluster_items_by_group_rank(filtered_list, group_ranking)

    output_rows = []
    for it in filtered_list:
        code = str(it.get('code', ''))
        name = str(it.get('name', ''))
        fut = it.get('fut_pricing', {})

        flagship_info = f"{it.get('flagship_code', '')} {it.get('flagship_name', '')}".strip()
        siblings = it.get('sibling_leaders', [])
        siblings_str = ", ".join(siblings) if isinstance(siblings, list) else str(it.get('sibling_leaders_str', ''))
        # 隔日沖評估結果存於 'overnight_guard' (見上方評分迴圈)，其 'tag' 為顯示用標籤
        overnight_info = it.get('overnight_guard', {})
        overnight_tag = overnight_info.get('tag', '🟢主力籌碼沉澱') if isinstance(overnight_info, dict) else str(overnight_info or '🟢主力籌碼沉澱')

        row = {
            "證券代號": code,
            "證券名稱": name,
            "所屬集團": it.get('group_name', ''),
            "集團排名": it.get('group_rank', 0),
            "集團熱度分": it.get('group_heat', 0.0),
            "集團狀態": it.get('group_status', ''),
            "集團旗艦母股": flagship_info,
            "母子位階角色": it.get('hierarchy_role', it.get('role', '')),
            "同集團共振家數": it.get('group_active_count', 0),
            "集團共振率%": it.get('group_resonance_ratio', 0.0),
            "同集團轉強兄弟股": siblings_str,
            "作帳日曆階段": calendar_meta.get('phase_badge', ''),
            "收盤價": it.get('close', 0.0),
            "多空狀態": it.get('state_badge', ''),
            "狀態代碼": it.get('state_code', ''),
            "門檻層級": it.get('門檻層級', it.get('tier_level', '')),
            "5日漲幅%": it.get('ret_5d_pct', 0.0),
            "母股5日漲幅%": it.get('parent_ret_5d_pct', 0.0),
            "母子強弱差%": it.get('spread_vs_parent_pct', 0.0),
            "法人當日買賣超(張)": it.get('inst_1d_lots', 0),
            "法人5日買賣超(張)": it.get('inst_5d_lots', 0),
            "當日成交量(張)": it.get('volume_lots', 0),
            "5日均量(張)": it.get('avg_vol_5d', 0),
            "20MA": round(float(it.get('ma20', 0.0)), 2),
            "月線乖離%": it.get('bias20_pct', 0.0),
            "進場價位": it.get('entry_price', 0.0),
            "加碼價位": it.get('addon_price', 0.0),
            "停利TP1": it.get('tp1_price', 0.0),
            "停利TP2": it.get('tp2_price', 0.0),
            "停損價位": it.get('sl_price', 0.0),
            "現貨進場": it.get('entry_price', 0.0),
            "現貨加碼": it.get('addon_price', 0.0),
            "現貨TP1": it.get('tp1_price', 0.0),
            "現貨TP2": it.get('tp2_price', 0.0),
            "現貨停損": it.get('sl_price', 0.0),
            "期貨契約": fut.get('contract_sym', '--'),
            "期貨月份": fut.get('month', '--'),
            "期貨收盤價": fut.get('futures_close', '--'),
            "價差(Basis)": fut.get('basis_desc', fut.get('basis', '--')),
            "期貨未平倉(OI)": fut.get('futures_oi', 0),
            "1口保證金(約)": fut.get('margin_ntd', 0),
            "結算倒數": fut.get('settle_desc', fut.get('days_to_settle', '--')),
            "次月合約": fut.get('next_month', '--'),
            "次月進場參考": fut.get('next_entry_ref', '--'),
            "隔日沖風控": overnight_tag,
            "評分": it.get('score', 0),
            "星級推薦": it.get('星級推薦', '★★★★☆')
        }
        output_rows.append(row)

    df_out = pd.DataFrame(output_rows, columns=CSV_COLUMNS)
    from rolling_retention_manager import save_rolling_result_csv
    df_out = save_rolling_result_csv(export_path, df_out, today_date_str=today_str, max_days=5)
    print(f"[✓] 已成功輸出選股報表至: {export_path}")

    # 產出 Markdown 戰報 result.md：排行榜 → 各集團個股集中排列 → 個股完整字卡
    today_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d')
    records_for_cards = df_out.to_dict(orient='records')
    try:
        cards_md = format_screener_markdown_cards(
            results=records_for_cards,
            date_str=today_str,
            strategy_id="08"
        )
        report_md = build_group_markdown_report(
            ranking=group_ranking,
            items=filtered_list,
            calendar_meta=calendar_meta,
            date_str=today_str,
            tier_mode=tier_mode,
            cards_md=cards_md,
            inst_real_count=inst_real_count,
            inst_total_count=len(market_records),
            adaptive_cfg=adaptive_cfg,
        )
        with open(MD_FILE, 'w', encoding='utf-8') as f:
            f.write(report_md)
        print(f"[✓] 已成功產出視覺化 Markdown 戰報至: {MD_FILE}")
    except Exception as e:
        print(f"[!] 產出 Markdown 戰報失敗: {e}")

    # 落地當日集團排行 (供每週日 optimize_all 週報與其他下游讀取)
    try:
        with open(RANKING_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'date': today_str,
                'phase_code': calendar_meta.get('phase_code', ''),
                'ranking': group_ranking,
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] 寫入 group_ranking.json 失敗: {e}")

    # 9. LINE 推播
    if send_line_flag and not df_out.empty:
        print("[*] 正在向 LINE 專屬視窗 (LINE_TARGET_STRATEGY_08) 推送集團作帳戰報...")
        try:
            # 先推集團排行榜 (純文字、零引線)，再推個股輪播字卡
            try:
                rank_text = format_group_ranking_text(
                    ranking=group_ranking,
                    items=filtered_list,
                    date_str=today_str,
                    calendar_meta=calendar_meta,
                )
                send_to_line(rank_text, strategy="08")
            except Exception as e:
                print(f"[!] 集團排行榜推播失敗 (不影響個股字卡): {e}")

            carousel_payload = build_screener_carousel(
                results=records_for_cards,
                date_str=today_str,
                strategy_id="08"
            )
            success = send_to_line(carousel_payload, strategy="08")
            if not success:
                print("[*] Flex Carousel 推播未成功，無縫退回純文字備援...")
                clean_text = format_clean_screener_text(
                    results=records_for_cards,
                    date_str=today_str,
                    strategy_id="08"
                )
                send_to_line(clean_text, strategy="08")
        except Exception as e:
            print(f"[!] LINE 推播發生例外: {e}")

    return output_rows, tier_mode


def main():
    parser = argparse.ArgumentParser(description="台股策略 08：集團作帳與母子聯動伏擊選股器")
    parser.add_argument("--export", default=CSV_FILE, help="匯出 CSV 檔案路徑")
    parser.add_argument("--line", action="store_true", help="執行後推播最新戰訊至 LINE 專屬群組")
    args = parser.parse_args()

    run_screener(export_path=args.export, send_line_flag=args.line)


if __name__ == '__main__':
    main()
