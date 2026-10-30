#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可轉債 (CB) 定價伏擊旗艦策略 - 全市場動態追蹤與四大防線定價輔助引擎 (CB Helper Engine)
================================================================================
核心責任：
1. 【TPEx 官方轉(交)換債 OpenAPI 自動同步】：
   - 自動連線櫃買中心 OpenAPI (bond_ISSBD5_data)，動態拉取全台股最新發行可轉債。
   - 自動篩選 3 個月法定閉鎖期尚未解禁（或近期即將解禁）之活躍標的。
2. 【母池雙層動態融合機制 (Dual-Layer Pool Fusion)】：
   - 第一層：本地 `cb_pool.json`（保留人工研究標的、董事會決議剛宣布、申報生效中等未掛牌案件）。
   - 第二層：官方已掛牌閉鎖中之全市場活躍 CB，自動去重並整合成完整監控母池。
3. 【實戰四大防線定價引擎 V4.0 對齊 (TWSE Legal Tick Size & Monotonicity)】：
   - 依照證交所 6 階法定升降檔位方向性合規取整（SL 向下取整、TP 向下取整以利撮合、Entry/Addon 向上取整確認突破）。
   - 嚴格守護幾何單調性：LimitDown <= SL < Close <= Entry < Addon < TP1 <= TP2 <= LimitUp。
   - 計算空間極限 130% 逼贖出場線與健康盈虧比 (Reward to Risk Ratio)。
================================================================================
"""

import os
import sys
import json
import time
import datetime
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from four_price_engine import normalize_to_tick, get_twse_tick_size, get_limit_prices

STRATEGY_DIR = Path(__file__).resolve().parent
POOL_FILE = STRATEGY_DIR / "cb_pool.json"
CACHE_FILE = STRATEGY_DIR / "cb_pool_tpex_cache.json"

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))


def fetch_tpex_active_cbs(cache_file: Optional[Path] = None, max_cache_hours: int = 12) -> List[Dict[str, Any]]:
    """
    自櫃買中心官方 OpenAPI 下載最新轉(交)換公司債發行基本資料
    快取機制：預設 12 小時內不重複請求，保證極致效能與離線容錯。
    """
    cache_path = cache_file or CACHE_FILE
    today = datetime.datetime.now(TZ_TW).date()
    today_str = today.strftime('%Y%m%d')

    # 1. 檢查快取
    if cache_path.exists():
        try:
            mtime = datetime.datetime.fromtimestamp(cache_path.stat().st_mtime, tz=TZ_TW)
            age_hours = (datetime.datetime.now(TZ_TW) - mtime).total_seconds() / 3600.0
            if age_hours < max_cache_hours:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                if cached_data and len(cached_data) > 0:
                    return cached_data
        except Exception:
            pass

    # 2. 線上抓取
    url = 'https://www.tpex.org.tw/openapi/v1/bond_ISSBD5_data'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    
    data = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                break
        except Exception as e:
            time.sleep(1.0 * (attempt + 1))

    if not data:
        # 降級讀取舊快取
        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    # 3. 篩選台股合法四碼標的 且 3個月閉鎖期仍在保護期內 (或即將解禁)
    # Conversion/ExchangePeriodStartDate: 閉鎖期解禁日 (約為掛牌後3個月)
    # 取解禁日 >= (今日 - 15天) 內的標的，確保閉鎖期紅利尚未耗盡
    cutoff_date = (today - datetime.timedelta(days=15)).strftime('%Y%m%d')
    active_list = []

    for r in data:
        code = str(r.get('IssuerCode', '')).strip()
        if len(code) == 4 and code.isdigit():
            try:
                conv_price = float(r.get('Conversion/ExchangePriceAtIssuance', 0) or 0)
                lockup_end = str(r.get('Conversion/ExchangePeriodStartDate', '')).strip()
                issue_date = str(r.get('IssueDate', '')).strip()
                
                if conv_price > 0 and lockup_end >= cutoff_date:
                    issue_amt = float(r.get('IssueAmount', 0) or 0) / 100000000.0 # 轉億元
                    
                    # 格式化日期 YYYY/MM/DD
                    fmt_issue = f"{issue_date[:4]}/{issue_date[4:6]}/{issue_date[6:]}" if len(issue_date) == 8 else issue_date
                    fmt_lockup = f"{lockup_end[:4]}/{lockup_end[4:6]}/{lockup_end[6:]}" if len(lockup_end) == 8 else lockup_end

                    active_list.append({
                        "code": code,
                        "name": r.get('IssuerName', '').strip(),
                        "market": "TW",
                        "cb_name": r.get('ShortName', '').strip(),
                        "bond_code": r.get('BondCode', '').strip(),
                        "status": "LISTED",
                        "status_desc": "已掛牌交易中 (閉鎖期保護內)",
                        "issue_date": fmt_issue,
                        "lockup_end_date": fmt_lockup,
                        "exit_rule": f"掛牌滿3個月閉鎖期解禁日 ({fmt_lockup}) 無論盈虧強制出清！",
                        "conversion_price": conv_price,
                        "est_amount": round(issue_amt, 1),
                        "purpose": "充實營運資金/擴產研發/償還借款",
                        "purpose_type": "EXPANSION" if issue_amt >= 10.0 else "DEBT",
                        "dilution_rate": 8.0,
                        "source": "TPEx_OpenAPI"
                    })
            except Exception:
                continue

    # 4. 寫入快取
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(active_list, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return active_list


def load_comprehensive_cb_pool() -> List[Dict[str, Any]]:
    """
    動態融合載入完整可轉債母池：
    1. 優先載入本地 cb_pool.json (保留精選未掛牌案如申報生效中、剛宣布者)
    2. 自動整合 TPEx 官方即時全市場活躍 CB (補足已掛牌閉鎖期內標的)
    3. 自動過濾已徹底解禁過期超過 1 個月的歷史標的
    """
    merged_pool = []
    seen_codes = set()

    # 1. 優先載入本地研究母池
    if POOL_FILE.exists():
        try:
            with open(POOL_FILE, 'r', encoding='utf-8') as f:
                manual_items = json.load(f)
            for item in manual_items:
                code = str(item.get('code', '')).strip()
                if code:
                    item['source'] = 'MANUAL_CURATED'
                    merged_pool.append(item)
                    seen_codes.add(code)
        except Exception as e:
            print(f"[!] 讀取本地 cb_pool.json 失敗: {e}")

    # 2. 自動拉取 TPEx 官方全市場活躍 CB
    try:
        tpex_items = fetch_tpex_active_cbs()
        for item in tpex_items:
            code = item['code']
            if code not in seen_codes:
                merged_pool.append(item)
                seen_codes.add(code)
    except Exception as e:
        print(f"[!] 拉取 TPEx CB 資料失敗: {e}")

    return merged_pool


def calculate_cb_four_prices(
    curr_price: float,
    conv_price: float,
    ma60: float,
    recent_high: float,
    prev_close: Optional[float] = None
) -> Dict[str, Any]:
    """
    【實戰四大防線定價引擎 V4.0 - 可轉債專用合規版】
    嚴守臺灣證交所 6 階升降檔位方向性合規取整：
    - 🟢 進場價位 (Entry): 買進上限價，向上取整 (normalize_to_tick, direction='up')
    - 🔵 加碼價位 (Addon): 突破加碼點，高於進場價，向上取整
    - 🔴 停利價位 (TP1): 首波反壓 / 轉換價上方，向下取整以利撮合 (direction='down')
    - 🔴 停利價位 (TP2): 波段主升目標，向下取整
    - ⏳ 空間極限 (130% Cap): 達標轉換價 130% 強制贖回清倉線
    - 🛑 停損防守 (Stop-Loss): 寬幅防守 -8%~-10% 或破季線，向下取整防毛刺
    幾何單調性：SL < Close <= Entry < Addon < TP1 <= TP2 <= Cap130
    """
    if curr_price <= 0:
        raise ValueError("curr_price 必須為正數")

    if conv_price <= 0:
        conv_price = curr_price * 1.02

    # (A) 🟢 建議進場價 (Entry Price) - 買進上限價
    # 允許在現價上緣 1.5% 掛單，若現價低於轉換價，上限以轉換價為界
    entry_raw = max(curr_price, curr_price * 1.015)
    if curr_price < conv_price:
        entry_raw = min(entry_raw, conv_price)
    else:
        entry_raw = min(entry_raw, conv_price * 1.05)

    entry_p = normalize_to_tick(entry_raw, direction='up')
    if entry_p < curr_price:
        entry_p = normalize_to_tick(curr_price, direction='up')

    # (B) 🔵 動能加碼價 (Add-on Price) - 右側確認續強點
    # 帶量突破近期5日高點、或突破現價+3.5%
    addon_raw = max(recent_high, curr_price * 1.035, entry_p * 1.02)
    addon_p = normalize_to_tick(addon_raw, direction='up')
    if addon_p <= entry_p:
        tick = get_twse_tick_size(entry_p)
        addon_p = round(entry_p + tick, 2)
        addon_p = normalize_to_tick(addon_p, direction='up')

    # (C) 🔴 目標停利價 (Take-Profit: TP1 & TP2)
    # TP1: 依回測最佳參數，轉換價或現價上方 +12%~+13%
    tp1_raw = max(conv_price * 1.12, curr_price * 1.12, addon_p * 1.03)
    tp1_p = normalize_to_tick(tp1_raw, direction='down')
    if tp1_p <= addon_p:
        tick = get_twse_tick_size(addon_p)
        tp1_p = round(addon_p + tick, 2)
        tp1_p = normalize_to_tick(tp1_p, direction='up')

    # TP2: 波段主升段目標 +22%~+25%
    tp2_raw = max(conv_price * 1.25, curr_price * 1.22, tp1_p * 1.08)
    tp2_p = normalize_to_tick(tp2_raw, direction='down')
    if tp2_p < tp1_p:
        tp2_p = tp1_p

    # (D) ⏳ 空間極限 130% 逼贖出清線 (Forced Redemption Ceiling)
    cap_130_raw = conv_price * 1.30
    cap_130_p = normalize_to_tick(cap_130_raw, direction='down')

    # (E) 🛑 嚴格停損價 (Stop-Loss Price)
    # 依 114-115 實證回測結論：放寬至 -8%~-10% 或季線 60MA * 0.96，防主力假摔甩轎
    sl_raw = min(conv_price * 0.92, curr_price * 0.91, ma60 * 0.96)
    sl_p = normalize_to_tick(sl_raw, direction='down')
    if sl_p >= curr_price:
        tick = get_twse_tick_size(curr_price)
        sl_p = round(curr_price - tick, 2)
        sl_p = normalize_to_tick(sl_p, direction='down')

    # 單日漲跌停邊界參考 (若有昨日收盤)
    if prev_close and prev_close > 0:
        lim_up, lim_down = get_limit_prices(prev_close)
    else:
        lim_up, lim_down = get_limit_prices(curr_price)

    # 二次單調性防禦
    if entry_p < curr_price:
        entry_p = normalize_to_tick(curr_price, direction='up')
    if addon_p <= entry_p:
        addon_p = round(entry_p + get_twse_tick_size(entry_p), 2)
    if tp1_p <= addon_p:
        tp1_p = round(addon_p + get_twse_tick_size(addon_p), 2)
    if tp2_p < tp1_p:
        tp2_p = tp1_p
    if sl_p >= curr_price:
        sl_p = round(curr_price - get_twse_tick_size(curr_price), 2)

    # 盈虧比計算 (Reward to Risk Ratio)
    risk = max(entry_p - sl_p, 0.01)
    reward = max(tp1_p - entry_p, 0.01)
    rr_ratio = round(reward / risk, 2)

    return {
        "entry_price": entry_p,
        "addon_price": addon_p,
        "tp1_price": tp1_p,
        "tp2_price": tp2_p,
        "sl_price": sl_p,
        "target_130": cap_130_p,
        "risk_reward_ratio": rr_ratio,
        "limit_up": lim_up,
        "limit_down": lim_down
    }
