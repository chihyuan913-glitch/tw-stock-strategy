import pytest
from market_pulse import GLOBAL_MARKET_PULSE

def test_market_pulse_score():
    data = GLOBAL_MARKET_PULSE.get_market_pulse_data()
    assert "score" in data
    assert 0.0 <= data["score"] <= 10.0
    assert "signals" in data
    assert len(data["signals"]) == 10
    assert "history" in data
    assert len(data["history"]) > 100

def test_treemap_data():
    data = GLOBAL_MARKET_PULSE.get_treemap_data()
    assert "root" in data
    assert "children" in data["root"]
    assert len(data["root"]["children"]) >= 15
    assert "distribution" in data
    assert len(data["distribution"]) == 13
    assert data["total_stocks"] >= 100
