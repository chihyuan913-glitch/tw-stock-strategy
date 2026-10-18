#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
單元測試：全域自適應操盤引擎 (test_adaptive_regime.py)
驗證四大市場情境、風控三鐵律鉗制防呆、動態策略參數映射與報告格式化
"""

import os
import sys
import json
import pytest
from pathlib import Path

# 將專案根目錄加入路徑
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adaptive_regime_manager import (
    REGIME_BULL_MOMENTUM,
    REGIME_BULL_OVERHEAT,
    REGIME_SIDEWAYS,
    REGIME_BEAR_DEFENSIVE,
    MIN_VOLUME_FLOOR,
    MAX_STOP_LOSS_PCT,
    MAX_RECOMMENDATIONS_CAP,
    determine_regime_key,
    build_regime_matrix,
    enforce_safety_guardrails,
    generate_adaptive_regime_config,
    load_adaptive_config,
    get_strategy_adaptive_params,
    format_adaptive_report_section
)

class TestAdaptiveRegimeManager:

    def test_determine_regime_key_overheat(self):
        """測試短線過熱情境識別 (bias > 5%)"""
        market = {"regime": "🔴【短線過熱】", "bias_ma20": 6.2, "trend": "強勢多頭"}
        assert determine_regime_key(market) == REGIME_BULL_OVERHEAT

    def test_determine_regime_key_bull(self):
        """測試良性主升波情境識別"""
        market = {"regime": "🟢【多頭良性主升】", "bias_ma20": 2.5, "trend": "多頭排列"}
        assert determine_regime_key(market) == REGIME_BULL_MOMENTUM

    def test_determine_regime_key_bear(self):
        """測試空頭防禦情境識別"""
        market = {"regime": "🟣【空頭修正】", "bias_ma20": -5.5, "trend": "弱勢破線"}
        assert determine_regime_key(market) == REGIME_BEAR_DEFENSIVE

    def test_determine_regime_key_sideways(self):
        """測試箱型洗盤震盪情境識別"""
        market = {"regime": "🟡【箱型洗盤】", "bias_ma20": -1.5, "trend": "箱型震盪"}
        assert determine_regime_key(market) == REGIME_SIDEWAYS

    def test_safety_guardrails_volume_floor(self):
        """測試風控鐵律一：成交量不得低於 1,000 張"""
        bad_config = {
            "long_strategies": {"min_volume": 400},
            "short_strategies": {"min_volume": 200}
        }
        enforced = enforce_safety_guardrails(bad_config)
        assert enforced["long_strategies"]["min_volume"] == MIN_VOLUME_FLOOR
        assert enforced["short_strategies"]["min_volume"] == MIN_VOLUME_FLOOR

    def test_safety_guardrails_stop_loss_clamp(self):
        """測試風控鐵律二：停損絕對值不可大於 7.0%"""
        bad_config = {
            "long_strategies": {"stop_loss_pct": -12.5},
            "short_strategies": {"stop_loss_pct": 10.0}
        }
        enforced = enforce_safety_guardrails(bad_config)
        assert enforced["long_strategies"]["stop_loss_pct"] == -MAX_STOP_LOSS_PCT
        assert enforced["short_strategies"]["stop_loss_pct"] == MAX_STOP_LOSS_PCT

    def test_safety_guardrails_recommendations_cap(self):
        """測試風控鐵律三：單策略推薦檔數不得超過 10 檔"""
        bad_config = {
            "global_controls": {"max_recommendations_per_strategy": 25}
        }
        enforced = enforce_safety_guardrails(bad_config)
        assert enforced["global_controls"]["max_recommendations_per_strategy"] == MAX_RECOMMENDATIONS_CAP

    def test_build_regime_matrix_sideways(self):
        """測試箱型震盪時多方參數自動收緊"""
        market = {"close": 47111.26, "bias_ma20": -2.10, "ma20": 48121.22, "ma60": 46500.0}
        matrix = build_regime_matrix(REGIME_SIDEWAYS, market)
        
        # 驗證箱型短打參數
        assert matrix["global_controls"]["mode"] == "高出低進箱型型"
        assert matrix["long_strategies"]["tp1_pct"] <= 4.5
        assert matrix["long_strategies"]["bias_max_pct"] <= 4.0
        assert matrix["long_strategies"]["stop_loss_pct"] >= -7.0

    def test_generate_and_load_config_roundtrip(self, tmp_path):
        """測試生成與載入 roundtrip 及容錯"""
        test_file = tmp_path / "test_regime_config.json"
        market = {"regime": "🟢【多頭主升波】", "close": 23500.0, "bias_ma20": 3.2}
        
        cfg = generate_adaptive_regime_config(market, output_path=test_file)
        assert test_file.exists()
        
        loaded = load_adaptive_config(test_file)
        assert loaded["market_regime"]["key"] == REGIME_BULL_MOMENTUM
        assert loaded["long_strategies"]["priority_weight"] == 1.2

    def test_load_adaptive_config_fallback(self, tmp_path):
        """測試檔案不存在時優雅降級為預設保底矩陣"""
        non_existent = tmp_path / "non_existent.json"
        fallback = load_adaptive_config(non_existent)
        assert "global_controls" in fallback
        assert "long_strategies" in fallback
        assert fallback["long_strategies"]["min_volume"] >= 1000

    def test_get_strategy_adaptive_params(self, tmp_path):
        """測試特定策略動態參數查詢"""
        test_file = tmp_path / "test_regime_config.json"
        market = {"regime": "🟡【箱型洗盤】", "close": 47000.0, "bias_ma20": -1.5}
        generate_adaptive_regime_config(market, output_path=test_file)

        # 策略 01 布林反轉
        s01_params = get_strategy_adaptive_params("01", config_path=test_file)
        assert "bb_lower_band_offset_pct" in s01_params
        assert s01_params["active_mode"] == "LONG_REVERSAL"

        # 策略 03 做空避險
        s03_params = get_strategy_adaptive_params("03", config_path=test_file)
        assert s03_params["active_mode"] == "SHORT_BREAKDOWN"
        assert s03_params["stop_loss_pct"] > 0  # 空單停損為正值

        # 策略 08 集團作帳
        s08_params = get_strategy_adaptive_params("08", config_path=test_file)
        assert "group_resonance_threshold" in s08_params

    def test_format_adaptive_report_section(self):
        """測試週報 Markdown 格式渲染"""
        market = {"regime": "🟡【高檔震盪整理・箱型洗盤】", "close": 47111.26, "bias_ma20": -2.10}
        cfg = build_regime_matrix(REGIME_SIDEWAYS, market)
        report_md = format_adaptive_report_section(cfg)
        assert "全域自適應操盤參數矩陣" in report_md
        assert "箱型洗盤" in report_md
        assert "第一停利" in report_md
        assert "嚴格停損" in report_md
