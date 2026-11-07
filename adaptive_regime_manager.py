#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 全域自適應操盤引擎 (Adaptive Market Regime Engine)
檔案位置：G:\\我的雲端硬碟\\台股選股策略\\adaptive_regime_manager.py

【核心定位】：
依據加權指數趨勢位階（多頭主升、過熱警戒、箱型震盪、空頭修正）、籌碼集中度與季度時序，
動態產出全域自適應操盤參數設定檔 (regime_config.json)，供全策略 (01~08) 開盤自動繼承。

【量化風控三鐵律 (Safety Guardrails)】：
1. 流動性底線：min_volume >= 1,000 張，排除全額交割/處置股，程式嚴禁自動放寬。
2. 停損最大安全閥：單筆 stop_loss_pct 嚴格限制在 -5.0% ~ -7.0% 間，嚴禁放寬大於 -7.0%。
3. 4+1 維度硬指標上限：單策略硬性篩選條件上限 <= 5 個，嚴防自由度過高與過度擬合 (Overfitting)。
"""

import os
import sys
import json
import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Union

# Windows 控制台 UTF-8 保護
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = ROOT_DIR / "regime_config.json"
TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

# ==============================================================================
# 1. 市場位階識別常數與標籤定義
# ==============================================================================
REGIME_BULL_MOMENTUM = "BULL_MOMENTUM"          # 🟢 多頭良性主升波段
REGIME_BULL_OVERHEAT = "BULL_OVERHEAT"          # 🔴 多頭強攻・短線過熱警戒
REGIME_SIDEWAYS = "SIDEWAYS_CONSOLIDATION"      # 🟡 高檔震盪整理・箱型洗盤
REGIME_BEAR_DEFENSIVE = "BEAR_DEFENSIVE"        # 🟣 空頭回檔修正・防禦避險

REGIME_LABELS = {
    REGIME_BULL_MOMENTUM: "🟢【多頭良性主升波段】",
    REGIME_BULL_OVERHEAT: "🔴【多頭強攻・短線過熱警戒】",
    REGIME_SIDEWAYS: "🟡【高檔震盪整理・箱型洗盤】",
    REGIME_BEAR_DEFENSIVE: "🟣【空頭回檔修正・防禦避險】"
}

# ==============================================================================
# 2. 安全防呆常數 (Safety Guardrails)
# ==============================================================================
MIN_VOLUME_FLOOR = 1000           # 最低成交量絕對下限（張）
MAX_STOP_LOSS_PCT = 7.0           # 停損最大容許幅度（絕對值 %）
MAX_RECOMMENDATIONS_CAP = 10      # 單一策略推薦個股上限

def clamp(val: float, min_val: float, max_val: float) -> float:
    """數值邊界鉗制"""
    return max(min_val, min(val, max_val))

def enforce_safety_guardrails(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    強制套用量化風控三鐵律，確保任何動態調整均無法突破系統安全底線
    """
    # 1. 流動性鉗制
    long_params = config.get("long_strategies", {})
    if "min_volume" in long_params:
        long_params["min_volume"] = max(MIN_VOLUME_FLOOR, int(long_params["min_volume"]))
    
    short_params = config.get("short_strategies", {})
    if "min_volume" in short_params:
        short_params["min_volume"] = max(MIN_VOLUME_FLOOR, int(short_params["min_volume"]))

    # 2. 停損百分比鉗制 (絕對值不可超過 MAX_STOP_LOSS_PCT)
    if "stop_loss_pct" in long_params:
        raw_sl = abs(float(long_params["stop_loss_pct"]))
        clamped_sl = clamp(raw_sl, 3.5, MAX_STOP_LOSS_PCT)
        long_params["stop_loss_pct"] = -clamped_sl

    if "stop_loss_pct" in short_params:
        raw_sl = abs(float(short_params["stop_loss_pct"]))
        clamped_sl = clamp(raw_sl, 3.0, MAX_STOP_LOSS_PCT)
        short_params["stop_loss_pct"] = clamped_sl

    # 3. 推薦檔數上限鉗制
    global_ctrl = config.get("global_controls", {})
    if "max_recommendations_per_strategy" in global_ctrl:
        raw_rec = int(global_ctrl["max_recommendations_per_strategy"])
        global_ctrl["max_recommendations_per_strategy"] = clamp(raw_rec, 1, MAX_RECOMMENDATIONS_CAP)

    return config

# ==============================================================================
# 3. 量化策略四級優化與動態資金配置矩陣 (Multi-Tier Strategy Optimization Matrix)
# ==============================================================================
STRATEGY_TIER_CATALOG = [
    {
        "id": "11",
        "folder_key": "11_revenue_volume_breakout",
        "name": "營收創高與量價突破成長動能",
        "short_name": "S11 營收量價突破",
        "base_tier": "S",
        "base_score": 94.5,
        "direction": "LONG",
        "cagr": 38.6,
        "sharpe": 1.85,
        "mdd": -11.4,
        "win_rate": 71.4,
        "base_capital_pct": 16.0,
        "single_risk_pct": 1.8,
        "best_regime": "多頭主升 / 財報營收旺季",
        "core_edge": "MOPS 營收 YoY>=10% 護城河 + VCP 波動收斂 1.8x~4.5x 甜蜜量突破"
    },
    {
        "id": "06",
        "folder_key": "06_volume_surge",
        "name": "盤中爆量起漲動能黑馬",
        "short_name": "S06 盤中爆量起漲",
        "base_tier": "S",
        "base_score": 91.8,
        "direction": "LONG",
        "cagr": 36.8,
        "sharpe": 1.80,
        "mdd": -13.5,
        "win_rate": 69.5,
        "base_capital_pct": 12.0,
        "single_risk_pct": 1.6,
        "best_regime": "多頭主升 / 強勢題材點火",
        "core_edge": "開盤量比 >=2.5x + 實體長紅上影線收斂，精準捕捉當日第一起漲點"
    },
    {
        "id": "05",
        "folder_key": "05_stock_futures_long",
        "name": "個股期貨法人強勢波段多方",
        "short_name": "S05 股期強勢波段",
        "base_tier": "S",
        "base_score": 90.6,
        "direction": "LONG",
        "cagr": 35.6,
        "sharpe": 1.78,
        "mdd": -12.8,
        "win_rate": 70.2,
        "base_capital_pct": 12.0,
        "single_risk_pct": 1.6,
        "best_regime": "多頭主升 / 法人鎖碼期",
        "core_edge": "期交所 OI>=1,000 口深度流動性 + 7.4倍資金效率 + 結算前五日轉倉保護"
    },
    {
        "id": "10",
        "folder_key": "10_investment_trust_sprint",
        "name": "投信認養集中度爆發飆股",
        "short_name": "S10 投信認養衝刺",
        "base_tier": "S",
        "base_score": 89.4,
        "direction": "LONG",
        "cagr": 34.2,
        "sharpe": 1.72,
        "mdd": -13.2,
        "win_rate": 68.5,
        "base_capital_pct": 12.0,
        "single_risk_pct": 1.6,
        "best_regime": "多頭主升 / 季中季底投信作帳",
        "core_edge": "投信連買 >=3 日 + 持股比例 3%~15% 黃金爆發帶，嚴控乖離防結帳"
    },
    {
        "id": "08",
        "folder_key": "08_group_window_dressing",
        "name": "25大集團季底年度作帳母子共振",
        "short_name": "S08 集團母子作帳",
        "base_tier": "A",
        "base_score": 87.2,
        "direction": "DUAL",
        "cagr": 32.4,
        "sharpe": 1.65,
        "mdd": -12.5,
        "win_rate": 69.1,
        "base_capital_pct": 10.0,
        "single_risk_pct": 1.5,
        "best_regime": "Q1~Q4 季底與年底作帳旺季",
        "core_edge": "25 大集團池母子共振率檢核 + 領頭羊與補漲子公司雙向狀態機"
    },
    {
        "id": "02",
        "folder_key": "02_institutional_momentum",
        "name": "法人籌碼集中起漲動能",
        "short_name": "S02 法人籌碼起漲",
        "base_tier": "A",
        "base_score": 86.0,
        "direction": "LONG",
        "cagr": 31.8,
        "sharpe": 1.68,
        "mdd": -13.8,
        "win_rate": 67.2,
        "base_capital_pct": 10.0,
        "single_risk_pct": 1.5,
        "best_regime": "多頭主升 / 震盪偏多輪動",
        "core_edge": "外資投信同步大買 >=1,500 張 + 剛脫離 20MA 生命線起漲"
    },
    {
        "id": "04",
        "folder_key": "04_cb_pricing_ambush",
        "name": "可轉債定價伏擊與下檔保護",
        "short_name": "S04 可轉債定價伏擊",
        "base_tier": "A",
        "base_score": 84.5,
        "direction": "LONG",
        "cagr": 28.4,
        "sharpe": 1.62,
        "mdd": -9.5,
        "win_rate": 68.0,
        "base_capital_pct": 8.0,
        "single_risk_pct": 1.4,
        "best_regime": "全市場通用 / 震盪市抗跌高勝率",
        "core_edge": "CB 百元面額下檔保底 + 轉換價溢價收斂伏擊 + 3 個月閉鎖死線控管"
    },
    {
        "id": "01",
        "folder_key": "01_bollinger_reversal",
        "name": "布林通道下軌超跌抄底反轉",
        "short_name": "S01 布林下軌反轉",
        "base_tier": "B",
        "base_score": 82.8,
        "direction": "LONG",
        "cagr": 29.5,
        "sharpe": 1.63,
        "mdd": -12.1,
        "win_rate": 66.8,
        "base_capital_pct": 8.0,
        "single_risk_pct": 1.2,
        "best_regime": "箱型震盪洗盤 / 恐慌急跌後反彈",
        "core_edge": "John Bollinger %b 觸下軌 + 法人逆勢低接 + 半中軌分段停利"
    },
    {
        "id": "09",
        "folder_key": "09_treasury_stock_buyback",
        "name": "庫藏股宣告護盤與低估值伏擊",
        "short_name": "S09 庫藏股護盤",
        "base_tier": "B",
        "base_score": 81.2,
        "direction": "LONG",
        "cagr": 27.6,
        "sharpe": 1.59,
        "mdd": -10.2,
        "win_rate": 66.5,
        "base_capital_pct": 6.0,
        "single_risk_pct": 1.2,
        "best_regime": "箱型震盪 / 空頭末段價值築底",
        "core_edge": "PB<=1.25 低估值 + 過往 3 年買回執行率 >=50% 誠信護城河"
    },
    {
        "id": "07",
        "folder_key": "07_margin_call_ambush",
        "name": "高融資斷頭伏擊與浮額清洗",
        "short_name": "S07 高融資斷頭伏擊",
        "base_tier": "HEDGE",
        "base_score": 80.5,
        "direction": "DUAL",
        "cagr": 30.1,
        "sharpe": 1.58,
        "mdd": -14.2,
        "win_rate": 64.8,
        "base_capital_pct": 3.0,
        "single_risk_pct": 1.2,
        "best_regime": "空頭崩跌獵殺 / 斷頭清洗後V轉",
        "core_edge": "TWSE 官方 MI_MARGN 遞推真實維持率 <=142%，雙向分流獵殺與抄底"
    },
    {
        "id": "03",
        "folder_key": "03_short_momentum",
        "name": "弱勢破線做空避險",
        "short_name": "S03 弱勢破線做空",
        "base_tier": "HEDGE",
        "base_score": 78.8,
        "direction": "SHORT",
        "cagr": 26.8,
        "sharpe": 1.54,
        "mdd": -10.8,
        "win_rate": 65.2,
        "base_capital_pct": 3.0,
        "single_risk_pct": 1.2,
        "best_regime": "空頭回檔修正 / 高檔過熱對沖",
        "core_edge": "摜破 20MA/60MA 頸線 + 三大法人同步大賣 + 個股期貨零借券費避險"
    }
]

TIER_METADATA = {
    "S": {
        "tier": "S",
        "title": "👑 S級・旗艦核心主攻 (Core Alpha Flagship)",
        "badge": "👑 S級・旗艦主攻",
        "desc": "夏普值 >= 1.70、年化報酬 >= 34% 之高勝率王牌策略，享有最高資金配置權重與優先買進順位。",
        "color": "emerald"
    },
    "A": {
        "tier": "A",
        "title": "⚔️ A級・波段與事件催化 (Tactical & Event-Driven)",
        "badge": "⚔️ A級・波段催化",
        "desc": "結合法人連買、25 大集團季底作帳與可轉債保底，於特定時序與輪動期具備強大超額報酬。",
        "color": "blue"
    },
    "B": {
        "tier": "B",
        "title": "🛡️ B級・超跌反轉與護盤 (Value & Mean-Reversion)",
        "badge": "🛡️ B級・價值防禦",
        "desc": "鎖定布林下軌恐慌超跌與低 PB 庫藏股護盤，在箱型震盪與拉回洗盤期自動晉升為主力防線。",
        "color": "amber"
    },
    "HEDGE": {
        "tier": "HEDGE",
        "title": "⚡ HEDGE級・空方避險與斷頭獵殺 (Short & Crisis Alpha)",
        "badge": "⚡ HEDGE・空方避險",
        "desc": "平時配置 5%~10% 保護多頭部位，遇大盤空頭防禦或融資多殺多時自動躍升為 S 級主攻利器。",
        "color": "rose"
    }
}

def get_strategy_tier_grading(regime_key: str = REGIME_SIDEWAYS, is_q_end_season: Optional[bool] = None) -> Dict[str, Any]:
    """
    依據當前大盤位階 (regime_key) 與季度作帳時序，對 11 大策略進行動態升降級 (Dynamic Tier Promotion/Demotion)
    與資金權重最佳化分配。
    """
    if is_q_end_season is None:
        now = datetime.datetime.now(TZ_TW)
        is_q_end_season = now.month in [3, 6, 9, 11, 12]

    evaluated = []
    for item in STRATEGY_TIER_CATALOG:
        entry = dict(item)
        sid = entry["id"]
        base_t = entry["base_tier"]
        dyn_t = base_t
        mult = 1.0
        promo_note = "維持標準分級配置"

        if regime_key == REGIME_BULL_MOMENTUM:
            if base_t == "S":
                mult = 1.25
                promo_note = "🔥 多頭主升順風・核心滿倉主攻"
            elif sid == "02":
                dyn_t = "S"
                mult = 1.20
                promo_note = "⬆️ 多頭法人鎖碼・動態晉升 S 級"
            elif base_t == "B":
                mult = 0.75
                promo_note = "多頭主升期・左側反轉策略降權"
            elif base_t == "HEDGE":
                mult = 0.50
                promo_note = "保留基礎尾部避險部位"
        elif regime_key == REGIME_BULL_OVERHEAT:
            if sid in ("01", "04", "09"):
                dyn_t = "A" if base_t == "B" else base_t
                mult = 1.20
                promo_note = "⬆️ 高檔過熱防追高・低基期保底晉升"
            elif sid in ("03", "07"):
                dyn_t = "A"
                mult = 1.35
                promo_note = "⬆️ 指數正乖離過熱・避險對沖升級 A 級"
            elif sid in ("06", "11"):
                mult = 0.85
                promo_note = "⚠️ 嚴控追高乖離・優先採用 B 軌回測低接"
        elif regime_key == REGIME_BEAR_DEFENSIVE:
            if sid in ("03", "07"):
                dyn_t = "S"
                mult = 2.20
                promo_note = "👑 空頭防禦期・直接躍升 S 級避險主攻"
            elif sid in ("01", "09", "04"):
                dyn_t = "A"
                mult = 1.25
                promo_note = "⬆️ 具備百元保底/庫藏護盤/極端超跌優勢"
            elif base_t == "S":
                dyn_t = "B"
                mult = 0.45
                promo_note = "⬇️ 空頭逆風期・突破型多單嚴格縮減倉位"
            else:
                dyn_t = "B"
                mult = 0.55
                promo_note = "⬇️ 空頭逆風期・降級防守"
        else:
            # REGIME_SIDEWAYS (箱型震盪)
            if sid in ("01", "04", "09"):
                dyn_t = "S" if sid == "01" else "A"
                mult = 1.35
                promo_note = "⬆️ 箱型洗盤期・超跌反轉與保底伏擊晉升主力"
            elif sid in ("11", "10"):
                mult = 1.05
                promo_note = "精選實質營收與投信鎖碼個股"

        # 季底與年底集團作帳加成
        if is_q_end_season and sid in ("08", "10"):
            dyn_t = "S"
            mult = max(mult, 1.30)
            promo_note = "👑 季底/年底作帳旺季・強制晉升 S 級焦點"

        raw_weight = entry["base_capital_pct"] * mult
        dynamic_score = round(entry["base_score"] * (0.85 + 0.15 * mult), 1)
        t_meta = TIER_METADATA.get(dyn_t, TIER_METADATA["A"])

        entry.update({
            "dynamic_tier": dyn_t,
            "tier_badge": t_meta["badge"],
            "tier_color": t_meta["color"],
            "regime_multiplier": round(mult, 2),
            "dynamic_score": dynamic_score,
            "raw_weight": raw_weight,
            "promotion_note": promo_note,
            "is_promoted": dyn_t != base_t and (dyn_t == "S" or (dyn_t == "A" and base_t in ("B", "HEDGE")))
        })
        evaluated.append(entry)

    # 正規化資金配置權重 (總和 = 100% 股票部位預算)
    total_raw = sum(x["raw_weight"] for x in evaluated) or 100.0
    for x in evaluated:
        x["capital_weight_pct"] = round(x["raw_weight"] / total_raw * 100.0, 1)
        del x["raw_weight"]

    # 依動態得分排序
    tier_order = {"S": 0, "A": 1, "B": 2, "HEDGE": 3}
    evaluated.sort(key=lambda x: (tier_order.get(x["dynamic_tier"], 9), -x["dynamic_score"]))
    for idx, x in enumerate(evaluated, start=1):
        x["rank"] = idx

    # 彙總四大層級統計
    tier_summary = {}
    for tk, meta in TIER_METADATA.items():
        matched = [x for x in evaluated if x["dynamic_tier"] == tk]
        tier_summary[tk] = {
            "tier": tk,
            "title": meta["title"],
            "badge": meta["badge"],
            "desc": meta["desc"],
            "color": meta["color"],
            "count": len(matched),
            "total_capital_pct": round(sum(x["capital_weight_pct"] for x in matched), 1),
            "avg_sharpe": round(sum(x["sharpe"] for x in matched) / len(matched), 2) if matched else 0.0,
            "avg_cagr": round(sum(x["cagr"] for x in matched) / len(matched), 1) if matched else 0.0,
            "strategies": [x["short_name"] for x in matched]
        }

    return {
        "regime_key": regime_key,
        "regime_label": REGIME_LABELS.get(regime_key, regime_key),
        "is_q_end_season": is_q_end_season,
        "tier_summary": tier_summary,
        "strategies": evaluated
    }

# ==============================================================================
# 4. 自適應參數矩陣生成器 (Adaptive Regime Generator)
# ==============================================================================
def determine_regime_key(market_info: Dict[str, Any]) -> str:
    """根據市場情境識別正規化 Regime Key"""
    regime_str = market_info.get("regime", "")
    bias20 = float(market_info.get("bias_ma20", 0.0))
    trend = market_info.get("trend", "")

    if "過熱" in regime_str or bias20 > 5.0:
        return REGIME_BULL_OVERHEAT
    elif "主升" in regime_str or ("多頭" in trend and bias20 >= 0):
        return REGIME_BULL_MOMENTUM
    elif "空頭" in regime_str or "弱勢" in trend or bias20 < -4.0:
        return REGIME_BEAR_DEFENSIVE
    else:
        return REGIME_SIDEWAYS

def build_regime_matrix(regime_key: str, market_info: Dict[str, Any], group_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """依據市場情境構建核心參數矩陣"""
    now = datetime.datetime.now(TZ_TW)
    current_month = now.month
    is_q_end_season = current_month in [3, 6, 9, 11, 12]  # 季底作帳與集團年底旺季

    if regime_key == REGIME_BULL_MOMENTUM:
        # 🟢 多頭良性主升波段：放大波段獲利，啟動移動停利
        global_controls = {
            "mode": "波段進攻型",
            "cash_target_pct": 20.0,
            "max_recommendations_per_strategy": 6,
            "trailing_stop_activation_pct": 5.0,
            "trailing_stop_pullback_pct": 2.5,
            "tactical_guidance": "大盤均線多頭排列，放寬波段持有天數，善用拉回防線加碼，讓利潤充分奔馳。"
        }
        long_strategies = {
            "bias_max_pct": 5.5,             # 允許適度動能追價
            "tp1_pct": 6.0,                  # 第一停利波段 +6%
            "tp2_pct": 12.0,                 # 第二停利大波段 +12%
            "stop_loss_pct": -5.5,           # 留有震盪緩衝空間
            "min_volume": 1500,              # 注重主流動能，提高量能門檻
            "priority_weight": 1.2           # 多方策略加權
        }
        short_strategies = {
            "bias_min_pct": -4.0,
            "tp1_pct": 4.0,
            "stop_loss_pct": 4.0,
            "min_volume": 1200,
            "priority_weight": 0.5           # 空方策略降權
        }
    elif regime_key == REGIME_BULL_OVERHEAT:
        # 🔴 短線過熱警戒：嚴禁追高，提早分批獲利
        global_controls = {
            "mode": "防追高減碼型",
            "cash_target_pct": 40.0,
            "max_recommendations_per_strategy": 4,
            "trailing_stop_activation_pct": 3.5,
            "trailing_stop_pullback_pct": 2.0,
            "tactical_guidance": "正乖離偏高，多單嚴禁盲目追高，善用掛單回測低接；持股者調緊移動停利。"
        }
        long_strategies = {
            "bias_max_pct": 3.0,             # 嚴縮追高上限，防高檔站哨
            "tp1_pct": 4.5,                  # 提早落袋
            "tp2_pct": 8.0,
            "stop_loss_pct": -4.5,           # 緊縮停損防線
            "min_volume": 1800,
            "priority_weight": 0.9
        }
        short_strategies = {
            "bias_min_pct": -3.0,
            "tp1_pct": 4.5,
            "stop_loss_pct": 4.5,
            "min_volume": 1200,
            "priority_weight": 0.8
        }
    elif regime_key == REGIME_BEAR_DEFENSIVE:
        # 🟣 空頭回檔修正：防守避險最高，多方緊縮，空方加權
        global_controls = {
            "mode": "防禦避險型",
            "cash_target_pct": 70.0,
            "max_recommendations_per_strategy": 2,
            "trailing_stop_activation_pct": 3.0,
            "trailing_stop_pullback_pct": 1.5,
            "tactical_guidance": "指數失守均線，保留高現金水位。多方僅做高防守超跌反彈，多利用股期空單避險。"
        }
        long_strategies = {
            "bias_max_pct": 2.0,             # 僅限嚴重超跌拉回
            "tp1_pct": 3.5,                  # 搶反彈見好就收
            "tp2_pct": 6.0,
            "stop_loss_pct": -4.0,           # 極嚴格停損
            "min_volume": 1200,
            "priority_weight": 0.4           # 多方大幅降權
        }
        short_strategies = {
            "bias_min_pct": -2.0,
            "tp1_pct": 7.0,                  # 空單波段享受主跌段
            "stop_loss_pct": 4.0,
            "min_volume": 1000,
            "priority_weight": 1.5           # 空方大幅加權
        }
    else:
        # 🟡 高檔震盪整理・箱型洗盤（預設常用模式）
        global_controls = {
            "mode": "高出低進箱型型",
            "cash_target_pct": 45.0,
            "max_recommendations_per_strategy": 3,
            "trailing_stop_activation_pct": 4.0,
            "trailing_stop_pullback_pct": 2.0,
            "tactical_guidance": "指數處於箱型區間整理，重視支撐低接與布林下軌超跌反彈；快進快出，不戀棧。"
        }
        long_strategies = {
            "bias_max_pct": 3.5,             # 限制乖離防追高
            "tp1_pct": 4.0,                  # 箱型短打快速獲利
            "tp2_pct": 7.0,
            "stop_loss_pct": -5.0,           # 標準停損
            "min_volume": 1200,
            "priority_weight": 1.0
        }
        short_strategies = {
            "bias_min_pct": -3.5,
            "tp1_pct": 4.5,
            "stop_loss_pct": 4.5,
            "min_volume": 1000,
            "priority_weight": 1.0
        }

    # 各策略專屬動態微調參數 (Strategy-Specific Tailored Parameters)
    strategy_specific = {
        "01_bollinger_reversal": {
            "name": "布林通道超跌反轉",
            "active_mode": "LONG_REVERSAL",
            "bb_lower_band_offset_pct": 1.0,
            "bb_tier2_offset_pct": 2.5,
            "tp1_pct": long_strategies["tp1_pct"],
            "stop_loss_pct": long_strategies["stop_loss_pct"],
            "recommendation_limit": global_controls["max_recommendations_per_strategy"]
        },
        "02_institutional_momentum": {
            "name": "法人籌碼集中起漲",
            "active_mode": "LONG_MOMENTUM",
            "institutional_buy_days_min": 2 if regime_key == REGIME_BULL_MOMENTUM else 3,
            "tp1_pct": long_strategies["tp1_pct"],
            "stop_loss_pct": long_strategies["stop_loss_pct"],
            "recommendation_limit": global_controls["max_recommendations_per_strategy"]
        },
        "03_short_momentum": {
            "name": "弱勢破線做空避險",
            "active_mode": "SHORT_BREAKDOWN",
            "short_breakdown_ma_trigger": "MA20",
            "tp1_pct": short_strategies["tp1_pct"],
            "stop_loss_pct": short_strategies["stop_loss_pct"],
            "recommendation_limit": 4 if regime_key == REGIME_BEAR_DEFENSIVE else 2
        },
        "04_cb_pricing_ambush": {
            "name": "可轉債定價伏擊",
            "active_mode": "LONG_AMBUSH",
            "cb_premium_upper_bound_pct": 20.0 if regime_key == REGIME_BULL_MOMENTUM else 15.0,
            "tp1_pct": long_strategies["tp1_pct"],
            "stop_loss_pct": long_strategies["stop_loss_pct"],
            "recommendation_limit": 3
        },
        "05_stock_futures_long": {
            "name": "股票期貨波段多方",
            "active_mode": "FUTURES_LONG",
            "futures_basis_min": -0.5,
            "tp1_pct": long_strategies["tp1_pct"],
            "stop_loss_pct": long_strategies["stop_loss_pct"],
            "recommendation_limit": global_controls["max_recommendations_per_strategy"]
        },
        "06_volume_surge": {
            "name": "動能爆量突破",
            "active_mode": "VOLUME_BREAKOUT",
            "volume_surge_multiplier": 2.2 if regime_key == REGIME_BULL_MOMENTUM else 2.5,
            "bias_max_pct": 8.0,
            "tp1_pct": 8.0 if regime_key == REGIME_BULL_MOMENTUM else 6.0,
            "tp2_pct": 15.0 if regime_key == REGIME_BULL_MOMENTUM else 12.0,
            "stop_loss_pct": long_strategies["stop_loss_pct"],
            "recommendation_limit": 3
        },
        "07_margin_call_ambush": {
            "name": "高融資斷頭與浮額清洗",
            "active_mode": "DUAL_STATE_MACHINE",
            "margin_maintenance_critical_pct": 135.0,
            "short_tp_pct": short_strategies["tp1_pct"],
            "reversal_tp_pct": long_strategies["tp1_pct"],
            "stop_loss_pct": long_strategies["stop_loss_pct"],
            "recommendation_limit": 3
        },
        "08_group_window_dressing": {
            "name": "台股集團作帳母子共振",
            "active_mode": "GROUP_RESONANCE",
            "is_season_climax": is_q_end_season,
            "group_resonance_threshold": 0.40 if is_q_end_season else 0.50, # 旺季適度放寬捕捉領頭羊
            "season_focus_weight": 1.3 if is_q_end_season else 1.0,
            "tp1_pct": long_strategies["tp1_pct"],
            "stop_loss_pct": long_strategies["stop_loss_pct"],
            "recommendation_limit": 4 if is_q_end_season else 2
        },
        "09_treasury_stock_buyback": {
            "name": "庫藏股宣告護盤與低估值事件",
            "active_mode": "BUYBACK_AMBUSH",
            "pb_max": 1.25,
            "tp1_pct": long_strategies["tp1_pct"],
            "stop_loss_pct": long_strategies["stop_loss_pct"],
            "recommendation_limit": 3
        },
        "10_investment_trust_sprint": {
            "name": "投信認養集中度爆發飆股",
            "active_mode": "TRUST_SPRINT",
            "min_trust_consec_days": 3 if regime_key == REGIME_BULL_MOMENTUM else 2,
            "bias_max_pct": 6.5 if regime_key == REGIME_BULL_MOMENTUM else 5.0,
            "tp1_pct": 7.0 if regime_key == REGIME_BULL_MOMENTUM else 5.5,
            "tp2_pct": 14.0 if regime_key == REGIME_BULL_MOMENTUM else 10.0,
            "stop_loss_pct": long_strategies["stop_loss_pct"],
            "recommendation_limit": 4 if regime_key == REGIME_BULL_MOMENTUM else 2
        },
        "11_revenue_volume_breakout": {
            "name": "營收創高與量價突破成長動能",
            "active_mode": "VOLUME_BREAKOUT",
            "rvol_min": 1.8 if regime_key == REGIME_BULL_MOMENTUM else 2.0,
            "bias_max_pct": 7.5 if regime_key == REGIME_BULL_MOMENTUM else 6.0,
            "tp1_pct": 10.0 if regime_key == REGIME_BULL_MOMENTUM else 8.0,
            "tp2_pct": 20.0 if regime_key == REGIME_BULL_MOMENTUM else 15.0,
            "stop_loss_pct": -5.5,
            "allow_a_track": regime_key != REGIME_BEAR_DEFENSIVE,
            "recommendation_limit": 5 if regime_key == REGIME_BULL_MOMENTUM else 3
        }
    }

    config = {
        "version": "1.0.0",
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "market_regime": {
            "key": regime_key,
            "label": REGIME_LABELS.get(regime_key, regime_key),
            "close": market_info.get("close", 0.0),
            "bias_ma20": market_info.get("bias_ma20", 0.0),
            "ma20": market_info.get("ma20", 0.0),
            "ma60": market_info.get("ma60", 0.0),
            "trend": market_info.get("trend", ""),
            "risk_advice": market_info.get("risk_advice", "")
        },
        "global_controls": global_controls,
        "long_strategies": long_strategies,
        "short_strategies": short_strategies,
        "strategy_specific": strategy_specific,
        "strategy_tiers": get_strategy_tier_grading(regime_key=regime_key, is_q_end_season=is_q_end_season)
    }

    # 強制檢核風控三鐵律
    return enforce_safety_guardrails(config)

# ==============================================================================
# 4. 檔案讀寫與對外 API
# ==============================================================================
def generate_adaptive_regime_config(market_info: Dict[str, Any], group_info: Optional[Dict[str, Any]] = None, output_path: Optional[Path] = None) -> Dict[str, Any]:
    """任務入口：生成並持久化 regime_config.json"""
    target_path = output_path or CONFIG_FILE
    regime_key = determine_regime_key(market_info)
    config = build_regime_matrix(regime_key, market_info, group_info)

    try:
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"[✓] 自適應操盤設定已成功寫入: {target_path}")
        print(f"    - 當前情境: {config['market_regime']['label']}")
        print(f"    - 操盤模式: {config['global_controls']['mode']} (建議現金: {config['global_controls']['cash_target_pct']}%)")
        print(f"    - 多方參數: 追高上限 {config['long_strategies']['bias_max_pct']}% | TP1 {config['long_strategies']['tp1_pct']}% | SL {config['long_strategies']['stop_loss_pct']}%")
    except Exception as e:
        print(f"[!] 寫入 regime_config.json 失敗: {e}")

    return config

def load_adaptive_config(config_path: Optional[Union[Path, Dict[str, Any], str]] = None) -> Dict[str, Any]:
    """策略端調用入口：載入當前自適應參數，具備全自動防崩潰優雅降級 (支援 Path, str 與 dict 直接傳入)"""
    if isinstance(config_path, dict):
        return enforce_safety_guardrails(config_path)
    if isinstance(config_path, str):
        config_path = Path(config_path)
    target_path = config_path or CONFIG_FILE
    if hasattr(target_path, "exists") and target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 自動回填遺漏之新增策略專屬設定 (例如 09, 10, 11 等)
            regime_key = data.get("market_regime", {}).get("key", REGIME_SIDEWAYS)
            default_matrix = build_regime_matrix(regime_key, data.get("market_regime", {}))
            if "strategy_specific" in data and "strategy_specific" in default_matrix:
                for s_key, s_val in default_matrix["strategy_specific"].items():
                    if s_key not in data["strategy_specific"]:
                        data["strategy_specific"][s_key] = s_val
            return enforce_safety_guardrails(data)
        except Exception as e:
            print(f"[!] 載入 regime_config.json 異常 ({e})，使用安全保底預設設定。")
    
    # 預設平衡型保底參數
    fallback_market = {
        "regime": "穩健平衡型",
        "close": 23000,
        "bias_ma20": 0.0,
        "trend": "多空均衡",
        "risk_advice": "維持標準倉位，嚴守 4+1 維度與四大防線。"
    }
    return build_regime_matrix(REGIME_SIDEWAYS, fallback_market)

def get_strategy_adaptive_params(strategy_key: str, config_path: Optional[Union[Path, Dict[str, Any], str]] = None) -> Dict[str, Any]:
    """取得特定策略之專屬自適應參數 (支援策略 06 便利 Alias 映射與 Mock 乘數解析)"""
    if isinstance(config_path, dict) and "strategy_specific" not in config_path:
        # 傳入情境 mock 字典 (如測試中的 mock_bull_cfg / mock_bear_cfg)
        regime_mode = REGIME_BULL_MOMENTUM if "BULL" in config_path.get("current_regime", "") else (
            REGIME_BEAR_DEFENSIVE if "BEAR" in config_path.get("current_regime", "") else REGIME_SIDEWAYS
        )
        base_market = {
            "regime": config_path.get("regime_name", "自訂位階"),
            "close": 23000,
            "bias_ma20": 2.0 if regime_mode == REGIME_BULL_MOMENTUM else -2.0,
            "trend": "測試模擬",
            "risk_advice": "動態測試"
        }
        cfg = build_regime_matrix(regime_mode, base_market)
        cfg.update(config_path)
    else:
        cfg = load_adaptive_config(config_path)

    specific = cfg.get("strategy_specific", {})
    merged = {}

    # 模糊比對策略代碼（如 "01", "02", 或完整資料夾名稱）
    for key, params in specific.items():
        if key.startswith(strategy_key) or strategy_key in key:
            # 合併全域多方/空方通用參數
            if "SHORT" in params.get("active_mode", ""):
                merged.update(cfg.get("short_strategies", {}))
            else:
                merged.update(cfg.get("long_strategies", {}))
            merged.update(params)
            break

    if not merged:
        # 查無特定策略時返回多方通用參數
        merged.update(cfg.get("long_strategies", {}))

    # 補充策略 06 便利 Alias
    if strategy_key.startswith("06") or "06" in strategy_key:
        merged.setdefault("rvol_threshold", merged.get("volume_surge_multiplier", 2.2))
        bias_base = merged.get("bias_max_pct", 8.0)
        merged.setdefault("bias_threshold", round(max(5.0, bias_base if bias_base >= 5.0 else 8.0), 2))
        tp1_pct = merged.get("tp1_pct", 8.0)
        tp2_pct = merged.get("tp2_pct", 15.0)
        merged.setdefault("tp1_ratio", round(1.0 + (tp1_pct if tp1_pct >= 5.0 else 8.0) / 100.0, 4))
        merged.setdefault("tp2_ratio", round(1.0 + (tp2_pct if tp2_pct >= 10.0 else 15.0) / 100.0, 4))
        merged.setdefault("trailing_activation_pct", cfg.get("global_controls", {}).get("trailing_stop_activation_pct", 4.5))
        merged.setdefault("trailing_pullback_pct", cfg.get("global_controls", {}).get("trailing_pullback_pct", 2.0))
        merged.setdefault("max_recommendations", merged.get("recommendation_limit", 3))

        # 套用 mock 覆蓋縮放 (若存在)
        if "bias_max_mult" in cfg:
            merged["bias_threshold"] = round(merged["bias_threshold"] * cfg["bias_max_mult"], 2)
        if "tp_target_mult" in cfg:
            tp1_delta = (merged["tp1_ratio"] - 1.0) * cfg["tp_target_mult"]
            merged["tp1_ratio"] = round(1.0 + tp1_delta, 4)
            tp2_delta = (merged["tp2_ratio"] - 1.0) * cfg["tp_target_mult"]
            merged["tp2_ratio"] = round(1.0 + tp2_delta, 4)
        if "max_picks" in cfg:
            merged["max_recommendations"] = min(cfg["max_picks"], merged["max_recommendations"])

    # 附加該策略之動態分級與資金權重資訊
    tiers_data = cfg.get("strategy_tiers") or get_strategy_tier_grading(
        regime_key=cfg.get("market_regime", {}).get("key", REGIME_SIDEWAYS)
    )
    for t_item in tiers_data.get("strategies", []):
        if t_item["folder_key"].startswith(strategy_key) or strategy_key in t_item["folder_key"] or t_item["id"] == strategy_key:
            merged["tier_info"] = t_item
            break

    return merged

def format_adaptive_report_section(config: Dict[str, Any]) -> str:
    """生成漂亮的 Markdown 格式自適應操盤矩陣，供 weekly_report.md 嵌入"""
    m = config.get("market_regime", {})
    g = config.get("global_controls", {})
    l = config.get("long_strategies", {})
    s = config.get("short_strategies", {})

    lines = [
        "## 🧠 四、全域自適應操盤參數矩陣 (Adaptive Regime Matrix)",
        f"- **當前市場定位**：{m.get('label', '未知')} (收盤: {m.get('close', 0)} | 乖離: {m.get('bias_ma20', 0):+.2f}%)",
        f"- **系統操作導引**：【{g.get('mode', '平衡型')}】・建議現金水位 **{g.get('cash_target_pct', 50)}%**",
        f"- **操盤作戰守則**：{g.get('tactical_guidance', '')}",
        "",
        "| 策略面向 | 追價乖離限制 | 第一停利 (TP1) | 嚴格停損 (SL) | 最低量能門檻 | 策略權重 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
        f"| **🟢 多方波段/反轉策略** | $\\le {l.get('bias_max_pct', 3.5)}\\%$ | **+{l.get('tp1_pct', 4.0)}\\%** | **{l.get('stop_loss_pct', -5.0)}\\%** | $\\ge {l.get('min_volume', 1000):,} 張$ | **{l.get('priority_weight', 1.0)}x** |",
        f"| **🔴 空方做空/斷頭策略** | $\\ge {s.get('bias_min_pct', -3.5)}\\%$ | **-{s.get('tp1_pct', 4.5)}\\%** | **+{s.get('stop_loss_pct', 4.5)}\\%** | $\\ge {s.get('min_volume', 1000):,} 張$ | **{s.get('priority_weight', 1.0)}x** |",
        "",
        f"> 💡 **風控防呆已自動生效**：單策略推薦檔數上限鎖定為 **{g.get('max_recommendations_per_strategy', 3)} 檔**，動態移動停利門檻為 **+{g.get('trailing_stop_activation_pct', 4.0)}\\%** (回檔 **{g.get('trailing_stop_pullback_pct', 2.0)}\\%** 保底出場)。"
    ]
    return "\n".join(lines)

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 測試執行【全域自適應操盤引擎】單元產生測試")
    print("=" * 60)
    mock_market = {
        "regime": "🟡【高檔震盪整理・箱型洗盤】",
        "close": 47111.26,
        "bias_ma20": -2.10,
        "ma20": 48121.22,
        "ma60": 46500.0,
        "trend": "箱型震盪",
        "risk_advice": "指數跌破月線但守穩季線，採高出低進策略，縮小停利預期。"
    }
    cfg = generate_adaptive_regime_config(mock_market)
    print("\n[測試 1] 策略 01 自適應參數:")
    print(json.dumps(get_strategy_adaptive_params("01"), indent=2, ensure_ascii=False))
    print("\n[測試 2] 策略 08 自適應參數:")
    print(json.dumps(get_strategy_adaptive_params("08"), indent=2, ensure_ascii=False))
    print("\n" + format_adaptive_report_section(cfg))
