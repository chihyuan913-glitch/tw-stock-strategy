# -*- coding: utf-8 -*-
"""第一階段儀表板契約。只用暫存檔與注入函式，不選股、不連外網、不推播。"""
import datetime as dt
import json
from pathlib import Path

import dashboard_contract as dc
import market_snapshot


SUNDAY = dt.datetime(2026, 11, 8, 11, 0, tzinfo=dc.TAIPEI)
FRIDAY = dt.date(2026, 11, 6)


def _rows(n, start=FRIDAY):
    rows = []
    for i in range(n):
        day = start - dt.timedelta(days=i % 3)
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)
        rows.append({
            "證券代號": f"{1000 + i}",
            "證券名稱": f"名稱{i}",
            "選股日期": day.isoformat(),
            "收盤價": 10 + i,
        })
    return rows


def test_overview_read_does_not_change_config_including_fallback(tmp_path):
    config = {
        "updated_at": "2026-11-07 07:39:40",
        "market_regime": {
            "key": "SIDEWAYS_CONSOLIDATION",
            "label": "🟡【高檔震盪整理・箱型洗盤】",
            "close": 40000,
            "ma20": 41000,
            "ma60": 39000,
        },
        "global_controls": {"cash_target_pct": 45.0, "mode": "高出低進箱型型"},
    }
    path = tmp_path / "regime_config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()

    def boom():
        raise TimeoutError("離線測試不連線")

    failed = market_snapshot.build_market_snapshot(
        config_path=path,
        holiday_path=tmp_path / "missing-holidays.json",
        now=SUNDAY,
        fetch_twse=boom,
        fetch_yfinance=boom,
        holidays=set(),
    )
    assert path.read_bytes() == before
    assert failed["source"] == "regime_config_snapshot"
    assert failed["hardcoded_fallback"] is False

    empty = tmp_path / "absent.json"
    fallback = market_snapshot.build_market_snapshot(
        config_path=empty,
        holiday_path=tmp_path / "missing-holidays.json",
        now=SUNDAY,
        fetch_twse=boom,
        fetch_yfinance=boom,
        holidays=set(),
    )
    assert not empty.exists()
    assert fallback["source"] == "hardcoded_fallback"
    assert fallback["verified"] is False
    assert fallback["as_of"] is None
    assert fallback["stale"] is False
    assert "46292.93" in fallback["close"] or fallback["close_raw"] == dc.HARDCODED_TWII["close_raw"]


def test_nested_regime_renders_45_percent_box_and_keeps_zero_cash():
    nested = {
        "updated_at": "2026-11-07 07:39:40",
        "market_regime": {
            "key": "SIDEWAYS_CONSOLIDATION",
            "label": "🟡【高檔震盪整理・箱型洗盤】",
        },
        "global_controls": {"cash_target_pct": 45, "mode": "高出低進箱型型"},
    }
    view = dc.flatten_effective_regime(nested)
    assert view["label_display"] == "🟡【高檔震盪整理・箱型洗盤】"
    assert view["cash_target_pct"] == 45
    assert view["cash_display"] == "45%"
    assert "多頭" not in view["label_display"]
    assert view["cash_display"] != "30%"

    zero = dc.flatten_effective_regime({
        "market_regime": {"key": "SIDEWAYS_CONSOLIDATION", "label": "🟡【高檔震盪整理・箱型洗盤】"},
        "global_controls": {"cash_target_pct": 0},
    })
    assert zero["cash_target_pct"] == 0
    assert zero["cash_display"] == "0%"

    missing = dc.flatten_effective_regime({"market_regime": {}, "global_controls": {}})
    assert missing["cash_display"] == "未知"
    assert missing["status"] == "unknown"


def test_batch_dates_stay_visible_when_they_differ():
    status = dc.build_batch_status([
        {"strategy": "01_bollinger_reversal", "dates": ["2026-11-06", "2026-11-04"], "rolling_total": 49},
        {"strategy": "11_revenue_volume_breakout", "dates": ["2026-11-06"], "rolling_total": 8},
        {"strategy": "06_volume_surge", "dates": ["2026-10-24"], "rolling_total": 2},
    ])
    assert status["unity"] == "conflict"
    assert status["status"] == "conflict"
    assert "2026-10-24" in status["dates"]
    assert "2026-11-06" in status["dates"]
    assert "不合併" in status["note"]


def test_count_lenses_do_not_change_selection_rows():
    rows = _rows(8)
    contract = dc.build_count_contract(rows)
    assert contract["rolling_total"] == 8
    assert contract["studio_limit"] == 5
    assert contract["studio_displayed"] == 5
    assert contract["advisor_cap"] == 2
    assert contract["advisor_count"] == 2
    assert "五日滾動 8" in contract["labels"]["rolling_total"]
    assert "Studio 顯示 5／上限 5" == contract["labels"]["studio"]
    assert "T+1 每策略 2／上限 2" == contract["labels"]["advisor"]

    one = dc.build_count_contract(_rows(1))
    assert one["advisor_count"] == 1
    assert one["studio_displayed"] == 1

    none = dc.build_count_contract([])
    assert none["pool_status"] == "empty"
    assert none["advisor_count"] == 0
    assert none["rolling_total"] == 0


def test_error_empty_and_stale_are_distinct_and_weekend_is_not_naive_stale():
    fresh = dc.module_status(count=8, as_of="2026-11-06", now=SUNDAY, holidays=set())
    assert fresh["status"] == "ok"
    assert fresh["stale"] is False
    assert fresh["empty"] is False

    stale = dc.module_status(count=8, as_of="2026-11-05", now=SUNDAY, holidays=set())
    assert stale["status"] == "stale"
    assert stale["stale"] is True
    assert stale["empty"] is False

    empty = dc.module_status(count=0, as_of="2026-11-06", now=SUNDAY, holidays=set())
    assert empty["status"] == "empty"
    assert empty["empty"] is True
    assert empty["error"] is None

    failed = dc.module_status(error="連線失敗", count=0, as_of="2026-11-06", now=SUNDAY)
    assert failed["status"] == "error"
    assert failed["empty"] is False
    assert failed["stale"] is False

    unknown = dc.module_status(count=3, as_of=None, now=SUNDAY)
    assert unknown["status"] == "unknown"
    assert unknown["stale"] is False


def test_illustrative_performance_is_not_marked_verified():
    marker = dc.illustrative_performance()
    assert marker["metrics_status"] == "illustrative_unverified"
    assert marker["verified_performance"] is False
    text = Path("web/index.html").read_text(encoding="utf-8")
    assert "未經回測或實盤驗證" in text
    assert "10/29" not in text
    assert "盤後 21:15 選股" in text
    assert "Promise.allSettled" in text
    assert "quotes=0" in text


def test_legacy_rows_are_unknown_not_backfilled():
    info = dc.legacy_provenance(["證券代號", "選股日期"], [{"證券代號": "1101", "選股日期": "2026-11-06"}])
    assert info["status"] == "unknown"
    assert info["strategy_version"] is None
    assert info["generated_at"] is None
    assert info["rule_snapshot"] is None


def test_deployment_sha_unknown_without_env():
    ident = dc.deployment_identity({})
    assert ident["deployment_sha_status"] == "unknown"
    assert ident["deployment_sha"] is None
    found = dc.deployment_identity({"RENDER_GIT_COMMIT": "abc123"})
    assert found["deployment_sha"] == "abc123"
    assert found["deployment_matches_repo"] is None


def test_mixed_price_and_ma_sources_are_conflict_not_effective():
    view = dc.assemble_market_view(
        twse={"close_raw": 40087.56, "prev_close": 41000, "change": -912.44, "change_pct": -2.23, "date": "20261106", "time": "13:30:00"},
        yfinance_bar=None,
        config={
            "market_regime": {"key": "SIDEWAYS_CONSOLIDATION", "label": "🟡【高檔震盪整理・箱型洗盤】", "ma20": 48170.13, "ma60": 46113.09},
            "global_controls": {"cash_target_pct": 45},
        },
        now=SUNDAY,
        holidays=set(),
    )
    assert view["price_source"] == "twse_mis"
    assert view["ma_source"] == "regime_config_snapshot"
    assert view["quote_inferred_regime"]["status"] == "conflict"
    assert view["effective_regime"]["cash_display"] == "45%"
    assert view["effective_regime"]["key"] == "SIDEWAYS_CONSOLIDATION"
    assert view["stale"] is False
