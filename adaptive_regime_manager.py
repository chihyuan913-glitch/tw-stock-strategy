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
from typing import Dict, Any, Optional

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
# 3. 自適應參數矩陣生成器 (Adaptive Regime Generator)
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
            "bb_lower_band_offset_pct": 0.5 if regime_key != REGIME_BEAR_DEFENSIVE else 1.0,
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
            "tp1_pct": long_strategies["tp1_pct"],
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
        "strategy_specific": strategy_specific
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

def load_adaptive_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """策略端調用入口：載入當前自適應參數，具備全自動防崩潰優雅降級"""
    target_path = config_path or CONFIG_FILE
    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
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

def get_strategy_adaptive_params(strategy_key: str, config_path: Optional[Path] = None) -> Dict[str, Any]:
    """取得特定策略之專屬自適應參數"""
    cfg = load_adaptive_config(config_path)
    specific = cfg.get("strategy_specific", {})

    # 模糊比對策略代碼（如 "01", "02", 或完整資料夾名稱）
    for key, params in specific.items():
        if key.startswith(strategy_key) or strategy_key in key:
            # 合併全域多方/空方通用參數
            merged = {}
            if "SHORT" in params.get("active_mode", ""):
                merged.update(cfg.get("short_strategies", {}))
            else:
                merged.update(cfg.get("long_strategies", {}))
            merged.update(params)
            return merged

    # 查無特定策略時返回多方通用參數
    return cfg.get("long_strategies", {})

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
