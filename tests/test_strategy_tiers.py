# -*- coding: utf-8 -*-
"""
tests/test_strategy_tiers.py
台股量化策略優化分級 (4-Tier Institutional Matrix) 測試套件
"""
import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
from adaptive_regime_manager import (
    get_strategy_tier_grading,
    REGIME_BULL_MOMENTUM,
    REGIME_BULL_OVERHEAT,
    REGIME_BEAR_DEFENSIVE,
    REGIME_SIDEWAYS,
    STRATEGY_TIER_CATALOG,
    TIER_METADATA
)
import studio_manager
from lab.engine.fusion import ScreenerFusionBridge
from lab.data.loader import MarketDataLoader
from lab.data.storage import MarketStorage

def test_tier_catalog_coverage():
    """檢核 11 檔量化策略均有完整分級與風控配置"""
    assert len(STRATEGY_TIER_CATALOG) == 11
    ids = {s["id"] for s in STRATEGY_TIER_CATALOG}
    expected_ids = {f"{i:02d}" for i in range(1, 12)}
    assert ids == expected_ids
    for s in STRATEGY_TIER_CATALOG:
        assert s["base_tier"] in ("S", "A", "B", "HEDGE")
        assert s["base_capital_pct"] > 0
        assert s["sharpe"] >= 1.50
        assert s["cagr"] >= 25.0

def test_dynamic_tier_allocation_sums_to_100():
    """檢核在各種大盤情境下，策略資金配置權重正規化後總和均為 100%"""
    for regime in (REGIME_BULL_MOMENTUM, REGIME_BULL_OVERHEAT, REGIME_BEAR_DEFENSIVE, REGIME_SIDEWAYS):
        res = get_strategy_tier_grading(regime_key=regime)
        strats = res["strategies"]
        assert len(strats) == 11
        total_weight = sum(s["capital_weight_pct"] for s in strats)
        assert 99.0 <= total_weight <= 101.0

def test_bear_defensive_promotes_hedge_to_s_tier():
    """空頭防禦情境下，做空與斷頭策略 (03, 07) 必須躍升為 S 級避險主攻"""
    res = get_strategy_tier_grading(regime_key=REGIME_BEAR_DEFENSIVE)
    tier_map = {s["id"]: s["dynamic_tier"] for s in res["strategies"]}
    assert tier_map["03"] == "S"
    assert tier_map["07"] == "S"

def test_quarter_end_promotes_group_dressing_to_s_tier():
    """季底或年底作帳時序，集團作帳策略 (08) 必須晉升為 S 級焦點"""
    res = get_strategy_tier_grading(regime_key=REGIME_SIDEWAYS, is_q_end_season=True)
    tier_map = {s["id"]: s["dynamic_tier"] for s in res["strategies"]}
    assert tier_map["08"] == "S"

def test_studio_manager_catalog_tiers():
    """檢核 FinLab Studio Catalog 中所有 11 檔策略的分級屬性"""
    cat = studio_manager.get_all_studio_catalog()
    assert len(cat) == 11
    for c in cat:
        assert "tier" in c
        assert c["tier"] in ("S", "A", "B", "HEDGE")
        assert "tier_badge" in c
        assert "capital_weight_pct" in c
        assert c["capital_weight_pct"] > 0

def test_leaderboard_includes_capital_weights():
    """檢核量化排行榜評比回傳資金配置建議與評等"""
    storage = MarketStorage()
    loader = MarketDataLoader(storage)
    res = ScreenerFusionBridge.generate_strategy_leaderboard(storage, loader, symbols=["2330"])
    lb = res["leaderboard"]
    assert len(lb) >= 11
    for row in lb:
        assert "grade" in row
        assert "capital_weight_pct" in row
        assert row["capital_weight_pct"] > 0
