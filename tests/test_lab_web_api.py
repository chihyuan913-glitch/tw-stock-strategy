"""
單元測試: 台股量化策略實驗室 - Web API 與儀表板路由 (test_lab_web_api.py)
檢驗 /api/lab/strategies、/api/lab/dashboard 與 /api/lab/backtest 契約完整性。
"""

import pytest
from fastapi.testclient import TestClient
from line_bot_server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_lab_strategies_endpoint(client):
    """檢驗策略清單 API"""
    resp = client.get("/api/lab/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    strategies = data["strategies"]
    assert len(strategies) >= 2
    strat_ids = {s["id"] for s in strategies}
    assert "01" in strat_ids
    assert "02" in strat_ids


def test_lab_dashboard_html_endpoint(client):
    """檢驗 Web 儀表板 HTML 回傳"""
    resp = client.get("/api/lab/dashboard")
    assert resp.status_code == 200
    assert "台股量化策略實驗室" in resp.text
    assert "equityChart" in resp.text


def test_lab_backtest_execution_endpoint(client):
    """檢驗回測端點與指標返回"""
    payload = {
        "strategy_id": "02",
        "symbols": ["2330"],
        "initial_cash": 1_000_000.0,
        "commission_discount": 0.6,
        "tax_rate": 0.003,
    }
    resp = client.post("/api/lab/backtest", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "metrics" in data
    assert "equity_curve" in data
    assert "trades" in data

    metrics = data["metrics"]
    assert "sharpe_ratio" in metrics
    assert "max_drawdown_pct" in metrics
    assert "total_return_pct" in metrics
