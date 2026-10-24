#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 實戰四大防線標準定價引擎 V4.0 全域旗艦版
================================================================================
核心優化升級亮點 (Four-Price Standards 4.0)：
1. 【臺灣證交所法定升降檔位嚴格合規 (TWSE Tick Size Rounding)】：
   - 完整對齊證交所 6 階法定升降單位 (<10: 0.01, 10~50: 0.05, 50~100: 0.10,
     100~500: 0.50, 500~1000: 1.00, >=1000: 5.00)。
   - 具備方向性取整：停損向下取整防虛設毛刺、停利向下取整以利掛單撮合、進場加碼向上取整確認突破。
2. 【真實波動幅度自適應動態調校 (ATR Volatility Scaling)】：
   - 針對高波動標的 (小型飆股) 給予充足呼吸空間防洗盤，低波動標的 (權值金融) 收縮停利追求高勝率。
3. 【大盤位階動態派發深度連動 (Market Regime Adaptation)】：
   - 自動繼承 `regime_config.json` 參數：多頭主升期放大波段目標，震盪箱型期短打拔檔。
4. 【台股 ±10% 漲跌停極限防護 (Limit Up/Down Guard)】：
   - 點位永遠鎖定在昨日收盤價 ±10% 撮合邊界之內，杜絕無法執行的無效價位。
5. 【實戰幾何單調性嚴格單向守護 (Mathematical Monotonicity)】：
   - 多方：LimitDown <= SL < Close <= Entry < Addon < TP1 <= TP2 <= LimitUp
   - 空方：LimitDown <= TP2 <= TP1 < Addon < Entry <= Close < SL <= LimitUp
================================================================================
"""

import os
import sys
import math
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, Union

ROOT_DIR = Path(__file__).resolve().parent


def get_twse_tick_size(price: float) -> float:
    """
    依據臺灣證券交易所法定升降單位規則，精算該股價對應之最小檔位 (Tick Size)
    - 股價 < 10 元：0.01 元
    - 10 <= 股價 < 50 元：0.05 元
    - 50 <= 股價 < 100 元：0.10 元
    - 100 <= 股價 < 500 元：0.50 元
    - 500 <= 股價 < 1000 元：1.00 元
    - 股價 >= 1000 元：5.00 元
    """
    if price < 0.01:
        return 0.01
    elif price < 10.0:
        return 0.01
    elif price < 50.0:
        return 0.05
    elif price < 100.0:
        return 0.10
    elif price < 500.0:
        return 0.50
    elif price < 1000.0:
        return 1.00
    else:
        return 5.00


def normalize_to_tick(price: float, direction: str = 'nearest') -> float:
    """
    將計算出之浮點價格嚴格合規取整至臺灣證交所法定檔位 (Tick Size)。
    :param price: 原始計算價格
    :param direction: 取整方向 ('nearest' 四捨五入, 'up' 無條件進位至下一檔, 'down' 無條件捨去至上一檔)
    :return: 完美合規之台股下單價格
    """
    if price <= 0.0:
        return 0.0
    
    tick = get_twse_tick_size(price)
    
    if direction == 'up':
        steps = math.ceil(round(price / tick, 6))
    elif direction == 'down':
        steps = math.floor(round(price / tick, 6))
    else:
        steps = round(price / tick)
        
    res = round(steps * tick, 2)
    # 若因進退檔剛好跨越門檻，以新門檻重新檢查一次邊界
    new_tick = get_twse_tick_size(res)
    if new_tick != tick:
        res = round(round(res / new_tick) * new_tick, 2)
        
    return res


def get_limit_prices(prev_close: float) -> Tuple[float, float]:
    """
    精算當日台股法定漲跌停價 (Limit Up / Limit Down, ±10%)
    依證交所規則：
    - 漲停價：昨日收盤 * 1.10，向下取至合規檔位 (不超過 10%)
    - 跌停價：昨日收盤 * 0.90，向上取至合規檔位 (不超過 -10%)
    """
    if prev_close <= 0:
        return 0.0, 0.0
    
    raw_up = prev_close * 1.10
    raw_down = prev_close * 0.90
    
    limit_up = normalize_to_tick(raw_up, direction='down')
    limit_down = normalize_to_tick(raw_down, direction='up')
    
    return limit_up, limit_down


def calculate_four_prices_long(
    curr_price: float,
    prev_close: Optional[float] = None,
    high: Optional[float] = None,
    low: Optional[float] = None,
    ma20: Optional[float] = None,
    lower_band: Optional[float] = None,
    atr: Optional[float] = None,
    regime_params: Optional[Dict[str, Any]] = None,
    custom_anchors: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    【多方標準四大防線定價引擎 V4.0】
    幾何單調性約束：LimitDown <= SL < Close <= Entry < Addon < TP1 <= TP2 <= LimitUp
    """
    if curr_price <= 0:
        raise ValueError("curr_price 必須為正數")

    p_close = prev_close if (prev_close and prev_close > 0) else curr_price
    p_high = high if (high and high >= curr_price) else curr_price
    p_low = low if (low and low <= curr_price) else curr_price
    limit_up, limit_down = get_limit_prices(p_close)

    # 1. 大盤自適應參數調校 (預設標準多頭值)
    rp = regime_params or {}
    adaptive_tp1 = float(rp.get("tp1_pct", 5.0)) / 100.0
    adaptive_tp2 = float(rp.get("tp2_pct", 10.0)) / 100.0
    adaptive_sl = abs(float(rp.get("stop_loss_pct", -4.0))) / 100.0
    bias_max = float(rp.get("bias_max_pct", 4.0)) / 100.0

    # 2. ATR 波動率自適應因子
    atr_val = atr if (atr and atr > 0) else (p_high - p_low)
    atr_pct = (atr_val / curr_price) if curr_price > 0 else 0.03
    volatility_factor = max(0.8, min(1.4, atr_pct / 0.035))  # 以 3.5% 震幅為基準係數 1.0

    # --------------------------------------------------------------------------
    # (A) 🟢 建議進場價 (Entry Price) - 買進防追高上限
    # --------------------------------------------------------------------------
    # 容許在現價上緣 1.0% ~ 2.0% 範圍內掛單進場，超過此上限嚴禁追高
    entry_buffer = min(bias_max * 0.4, 0.015 * volatility_factor)
    raw_entry = curr_price * (1.0 + entry_buffer)
    if custom_anchors and "entry" in custom_anchors:
        raw_entry = custom_anchors["entry"]

    # --------------------------------------------------------------------------
    # (B) 🛑 嚴格停損價 (Stop-Loss Price) - 風控防守撤退線
    # --------------------------------------------------------------------------
    # 結構優先：K 棒最低價 99 折、布林下軌 96 折、成本最大防守閥 -5% (結合波動度)
    sl_candidates = [curr_price * (1.0 - max(0.035, adaptive_sl * volatility_factor))]
    if p_low and p_low > 0:
        sl_candidates.append(p_low * 0.99)
    if lower_band and lower_band > 0:
        sl_candidates.append(lower_band * 0.96)
    if custom_anchors and "sl" in custom_anchors:
        sl_candidates = [custom_anchors["sl"]]

    raw_sl = max(sl_candidates)
    # 最大硬性安全閥限制 (嚴禁停損大於 -7.0%)
    hard_sl_floor = curr_price * 0.93
    raw_sl = max(raw_sl, hard_sl_floor)

    # --------------------------------------------------------------------------
    # (C) 🔴 目標停利價 (Take-Profit: TP1 & TP2) - 分批獲利滿足區
    # --------------------------------------------------------------------------
    # TP1: 首波反壓或動態自適應目標 (約 +4% ~ +7%)
    raw_tp1 = curr_price * (1.0 + max(0.035, adaptive_tp1 * volatility_factor))
    if ma20 and ma20 > curr_price:
        # 若現價在月線之下 (超跌反彈)，TP1 設為反彈半中軌
        raw_tp1 = curr_price + (ma20 - curr_price) * 0.50
    if custom_anchors and "tp1" in custom_anchors:
        raw_tp1 = custom_anchors["tp1"]

    # TP2: 波段主升等幅滿足區 (約 +8% ~ +15%)
    raw_tp2 = curr_price * (1.0 + max(0.07, adaptive_tp2 * volatility_factor))
    if ma20 and ma20 > curr_price:
        raw_tp2 = ma20  # 月線反壓滿足
    if custom_anchors and "tp2" in custom_anchors:
        raw_tp2 = custom_anchors["tp2"]

    # --------------------------------------------------------------------------
    # (D) 🔵 動能加碼價 (Add-on Price) - 右側突破確認點
    # --------------------------------------------------------------------------
    # 鎖定在進場價與 TP1 之間的 50% ~ 60% 區間，或突破當日最高價
    breakout_price = p_high * 1.005
    addon_ceiling = curr_price + (raw_tp1 - curr_price) * 0.60
    raw_addon = max(breakout_price, curr_price * 1.015)
    raw_addon = min(raw_addon, addon_ceiling)
    if custom_anchors and "addon" in custom_anchors:
        raw_addon = custom_anchors["addon"]

    # --------------------------------------------------------------------------
    # (E) 幾何單調性與台股漲跌停板極限約束 (Monotonicity & Limit Enforcement)
    # --------------------------------------------------------------------------
    # 1. 停損嚴格小於現價
    if raw_sl >= curr_price:
        raw_sl = curr_price * 0.96
    
    # 2. 進場價不小於現價，且小於加碼價
    raw_entry = max(curr_price, raw_entry)

    # 3. 加碼價至少高於進場價一個安全級距
    if raw_addon <= raw_entry:
        raw_addon = raw_entry * 1.01

    # 4. TP1 至少高於加碼價
    if raw_tp1 <= raw_addon:
        raw_tp1 = raw_addon * 1.015

    # 5. TP2 不低於 TP1
    if raw_tp2 < raw_tp1:
        raw_tp2 = raw_tp1 * 1.04

    # 6. 漲跌停極限約束
    if limit_up > 0:
        raw_tp2 = min(raw_tp2, limit_up)
        raw_tp1 = min(raw_tp1, limit_up)
        raw_addon = min(raw_addon, limit_up)
        raw_entry = min(raw_entry, limit_up)
    if limit_down > 0:
        raw_sl = max(raw_sl, limit_down)

    # --------------------------------------------------------------------------
    # (F) 臺灣證交所法定檔位方向性合規取整 (Directional Tick Normalization)
    # --------------------------------------------------------------------------
    sl_price = normalize_to_tick(raw_sl, direction='down')
    entry_price = normalize_to_tick(raw_entry, direction='up')
    addon_price = normalize_to_tick(raw_addon, direction='up')
    tp1_price = normalize_to_tick(raw_tp1, direction='down')
    tp2_price = normalize_to_tick(raw_tp2, direction='down')

    # 二次單調性防禦 (避免因 Tick 取整導致相鄰等值)
    if entry_price < curr_price:
        entry_price = normalize_to_tick(curr_price, direction='up')
    if addon_price <= entry_price:
        tick_step = get_twse_tick_size(entry_price)
        addon_price = round(entry_price + tick_step, 2)
    if tp1_price <= addon_price:
        tick_step = get_twse_tick_size(addon_price)
        tp1_price = round(addon_price + tick_step, 2)
    if tp2_price < tp1_price:
        tp2_price = tp1_price
    if sl_price >= curr_price:
        tick_step = get_twse_tick_size(curr_price)
        sl_price = round(curr_price - tick_step, 2)

    # 計算盈虧比 (Reward to Risk Ratio)
    potential_risk = entry_price - sl_price
    potential_reward = tp1_price - entry_price
    rr_ratio = round(potential_reward / potential_risk, 2) if potential_risk > 0 else 2.0

    return {
        "direction": "LONG",
        "current_price": curr_price,
        "entry_price": entry_price,
        "addon_price": addon_price,
        "tp1_price": tp1_price,
        "tp2_price": tp2_price,
        "sl_price": sl_price,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "risk_reward_ratio": rr_ratio,
        "volatility_factor": round(volatility_factor, 2),
        "guidance": {
            "entry_desc": f"建議買進上限 {entry_price} 元 (現價 {curr_price} 元以內皆為甜蜜買點，超過嚴禁追價)",
            "addon_desc": f"突破 {addon_price} 元帶量確認續強，啟動第 2 階段順勢加碼",
            "tp1_desc": f"目標一 {tp1_price} 元分批拔檔 50% 並啟動保本防守",
            "tp2_desc": f"目標二 {tp2_price} 元波段主升段等幅滿足全數落袋",
            "sl_desc": f"跌破 {sl_price} 元無條件停損離場，嚴守風控底線"
        }
    }


def calculate_four_prices_short(
    curr_price: float,
    prev_close: Optional[float] = None,
    high: Optional[float] = None,
    low: Optional[float] = None,
    ma20: Optional[float] = None,
    upper_band: Optional[float] = None,
    atr: Optional[float] = None,
    regime_params: Optional[Dict[str, Any]] = None,
    custom_anchors: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    【空方避險四大防線定價引擎 V4.0】
    幾何單調性約束：LimitDown <= TP2 <= TP1 < Addon < Entry <= Close < SL <= LimitUp
    """
    if curr_price <= 0:
        raise ValueError("curr_price 必須為正數")

    p_close = prev_close if (prev_close and prev_close > 0) else curr_price
    p_high = high if (high and high >= curr_price) else curr_price
    p_low = low if (low and low <= curr_price) else curr_price
    limit_up, limit_down = get_limit_prices(p_close)

    rp = regime_params or {}
    adaptive_tp1 = float(rp.get("tp1_pct", 5.0)) / 100.0
    adaptive_sl = abs(float(rp.get("stop_loss_pct", 4.0))) / 100.0

    atr_val = atr if (atr and atr > 0) else (p_high - p_low)
    atr_pct = (atr_val / curr_price) if curr_price > 0 else 0.03
    volatility_factor = max(0.8, min(1.4, atr_pct / 0.035))

    # 1. 🟢 放空進場上限 (Entry Price)
    raw_entry = curr_price * (1.0 - 0.015 * volatility_factor)
    if custom_anchors and "entry" in custom_anchors:
        raw_entry = custom_anchors["entry"]

    # 2. 🛑 回補停損價 (Stop-Loss Price) - 向上突破防守線
    sl_candidates = [curr_price * (1.0 + max(0.035, adaptive_sl * volatility_factor))]
    if p_high and p_high > 0:
        sl_candidates.append(p_high * 1.01)
    if upper_band and upper_band > 0:
        sl_candidates.append(upper_band * 1.02)
    if custom_anchors and "sl" in custom_anchors:
        sl_candidates = [custom_anchors["sl"]]
    raw_sl = min(sl_candidates)

    # 3. 🔴 向下停利回補目標 (TP1 & TP2)
    raw_tp1 = curr_price * (1.0 - max(0.04, adaptive_tp1 * volatility_factor))
    raw_tp2 = curr_price * (1.0 - max(0.08, adaptive_tp1 * 1.8 * volatility_factor))
    if custom_anchors and "tp1" in custom_anchors:
        raw_tp1 = custom_anchors["tp1"]
    if custom_anchors and "tp2" in custom_anchors:
        raw_tp2 = custom_anchors["tp2"]

    # 4. 🔵 加空價位 (Add-on Price) - 摜破前低續跌點
    raw_addon = curr_price - (curr_price - raw_tp1) * 0.50
    if p_low and p_low < curr_price:
        raw_addon = min(raw_addon, p_low * 0.995)
    if custom_anchors and "addon" in custom_anchors:
        raw_addon = custom_anchors["addon"]

    # 單調性修正
    if raw_sl <= curr_price:
        raw_sl = curr_price * 1.04
    raw_entry = min(curr_price, raw_entry)
    if raw_addon >= raw_entry:
        raw_addon = raw_entry * 0.99
    if raw_tp1 >= raw_addon:
        raw_tp1 = raw_addon * 0.985
    if raw_tp2 > raw_tp1:
        raw_tp2 = raw_tp1 * 0.96

    if limit_up > 0:
        raw_sl = min(raw_sl, limit_up)
    if limit_down > 0:
        raw_tp2 = max(raw_tp2, limit_down)
        raw_tp1 = max(raw_tp1, limit_down)
        raw_addon = max(raw_addon, limit_down)
        raw_entry = max(raw_entry, limit_down)

    sl_price = normalize_to_tick(raw_sl, direction='up')
    entry_price = normalize_to_tick(raw_entry, direction='down')
    addon_price = normalize_to_tick(raw_addon, direction='down')
    tp1_price = normalize_to_tick(raw_tp1, direction='up')
    tp2_price = normalize_to_tick(raw_tp2, direction='up')

    if entry_price > curr_price:
        entry_price = normalize_to_tick(curr_price, direction='down')
    if addon_price >= entry_price:
        tick_step = get_twse_tick_size(entry_price)
        addon_price = round(entry_price - tick_step, 2)
    if tp1_price >= addon_price:
        tick_step = get_twse_tick_size(addon_price)
        tp1_price = round(addon_price - tick_step, 2)
    if tp2_price > tp1_price:
        tp2_price = tp1_price
    if sl_price <= curr_price:
        tick_step = get_twse_tick_size(curr_price)
        sl_price = round(curr_price + tick_step, 2)

    potential_risk = sl_price - entry_price
    potential_reward = entry_price - tp1_price
    rr_ratio = round(potential_reward / potential_risk, 2) if potential_risk > 0 else 2.0

    return {
        "direction": "SHORT",
        "current_price": curr_price,
        "entry_price": entry_price,
        "addon_price": addon_price,
        "tp1_price": tp1_price,
        "tp2_price": tp2_price,
        "sl_price": sl_price,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "risk_reward_ratio": rr_ratio,
        "volatility_factor": round(volatility_factor, 2),
        "guidance": {
            "entry_desc": f"建議放空上限 {entry_price} 元 (現價 {curr_price} 元，反彈不過此上限為黃金空點)",
            "addon_desc": f"摜破 {addon_price} 元確認弱勢續殺，啟動第 2 階段順勢加空",
            "tp1_desc": f"回補目標一 {tp1_price} 元分批回補 50% 鎖定利潤",
            "tp2_desc": f"回補目標二 {tp2_price} 元主跌段等幅滿足全數回補",
            "sl_desc": f"突破 {sl_price} 元無條件空單停損回補，嚴控風險"
        }
    }


def apply_four_prices_to_record(
    record: Dict[str, Any],
    is_short: bool = False,
    regime_params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    【V4.0 全域通用標準化定價適配器】
    接收任意策略產出之標的 dict，自動以合規 Tick Size、ATR 波動度、
    台股漲跌停防護及數學單調性守護進行校準，並標準化注入實戰四大防線價位與指引。
    """
    out = dict(record)
    
    # 提取現價與相關參考數據
    def _get_val(*keys, default=0.0):
        for k in keys:
            if k in out and out[k] is not None:
                try:
                    v = float(str(out[k]).replace(',', '').strip())
                    if v > 0:
                        return v
                except (ValueError, TypeError):
                    pass
        return default

    curr_price = _get_val("收盤價", "即時現價", "現價", "Close", "close", "最新現價", "期貨收盤價")
    if curr_price <= 0:
        return out

    prev_close = _get_val("昨收", "昨日收盤", "prev_close", default=curr_price)
    high_val = _get_val("最高價", "high", "High", default=curr_price)
    low_val = _get_val("最低價", "low", "Low", default=curr_price)
    ma20_val = _get_val("布林中軌", "20MA", "ma20", "MA20", default=0.0) or None
    lb_val = _get_val("布林下軌", "lower_band", default=0.0) or None
    ub_val = _get_val("布林上軌", "upper_band", default=0.0) or None

    # 自訂既有錨點（若原本已有計算特定價位，作為參考校準基準）
    custom_anchors = {}
    old_entry = _get_val("建議進場價", "進場價位", "第一次進場價位", "進場買進上限", "多單進場", "空單進場")
    old_addon = _get_val("動能加碼價", "加碼價位", "第二次進場價位", "續強加碼價", "加空價位")
    old_tp1 = _get_val("停利TP1", "目標停利TP1", "第一次停利價位", "TP1")
    old_tp2 = _get_val("停利TP2", "目標停利TP2", "第二次停利價位", "TP2")
    old_sl = _get_val("嚴格停損價", "停損價位", "停損防守", "停損回補")

    if old_entry > 0:
        custom_anchors["entry"] = old_entry
    if old_addon > 0:
        custom_anchors["addon"] = old_addon
    if old_tp1 > 0:
        custom_anchors["tp1"] = old_tp1
    if old_tp2 > 0:
        custom_anchors["tp2"] = old_tp2
    if old_sl > 0:
        custom_anchors["sl"] = old_sl

    if is_short:
        calc_res = calculate_four_prices_short(
            curr_price=curr_price,
            prev_close=prev_close,
            high=high_val,
            low=low_val,
            ma20=ma20_val,
            upper_band=ub_val,
            regime_params=regime_params,
            custom_anchors=custom_anchors if custom_anchors else None
        )
    else:
        calc_res = calculate_four_prices_long(
            curr_price=curr_price,
            prev_close=prev_close,
            high=high_val,
            low=low_val,
            ma20=ma20_val,
            lower_band=lb_val,
            regime_params=regime_params,
            custom_anchors=custom_anchors if custom_anchors else None
        )

    # 統一回寫全策略標準欄位
    out["建議進場價"] = calc_res["entry_price"]
    out["動能加碼價"] = calc_res["addon_price"]
    out["停利TP1"] = calc_res["tp1_price"]
    out["停利TP2"] = calc_res["tp2_price"]
    out["嚴格停損價"] = calc_res["sl_price"]
    out["漲停價"] = calc_res["limit_up"]
    out["跌停價"] = calc_res["limit_down"]
    out["盈虧比"] = calc_res["risk_reward_ratio"]

    # 連動字卡多階梯欄位
    if "第一次進場價位" not in out or not out["第一次進場價位"]:
        out["第一次進場價位"] = calc_res["entry_price"]
    if "第一次停利價位" not in out or not out["第一次停利價位"]:
        out["第一次停利價位"] = calc_res["tp1_price"]
    if "第二次進場價位" not in out or not out["第二次進場價位"]:
        out["第二次進場價位"] = calc_res["addon_price"]
    if "第二次停利價位" not in out or not out["第二次停利價位"]:
        out["第二次停利價位"] = calc_res["tp2_price"]

    # 附帶操盤指南錦囊
    out["操盤錦囊"] = calc_res["guidance"]

    return out

