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
    assert contract["advisor_count"] is None
    assert "五日滾動 8" in contract["labels"]["rolling_total"]
    assert "Studio 顯示 5／上限 5" == contract["labels"]["studio"]
    assert "不由選股池推算" in contract["labels"]["advisor"]
    assert "T+1 每策略 2" not in contract["labels"]["advisor"]

    one = dc.build_count_contract(_rows(1))
    assert one["advisor_count"] is None
    assert one["studio_displayed"] == 1

    none = dc.build_count_contract([])
    assert none["pool_status"] == "empty"
    assert none["advisor_count"] is None
    assert none["rolling_total"] == 0


def _cal_2026():
    closed = {
        dt.date(2026, 2, 12), dt.date(2026, 2, 13), dt.date(2026, 2, 16),
        dt.date(2026, 2, 17), dt.date(2026, 2, 18), dt.date(2026, 2, 19), dt.date(2026, 2, 20),
    }
    return dc.reliable_calendar(closed, [2026])


def test_error_empty_and_stale_are_distinct_and_weekend_is_not_naive_stale():
    calendar = _cal_2026()
    fresh = dc.module_status(count=8, as_of="2026-11-06", now=SUNDAY, calendar=calendar, rhythm="daily_close")
    assert fresh["status"] == "ok"
    assert fresh["stale"] is False
    assert fresh["empty"] is False

    stale = dc.module_status(count=8, as_of="2026-11-05", now=SUNDAY, calendar=calendar, rhythm="daily_close")
    assert stale["status"] == "stale"
    assert stale["stale"] is True
    assert stale["empty"] is False

    empty = dc.module_status(count=0, as_of="2026-11-06", now=SUNDAY, calendar=calendar)
    assert empty["status"] == "empty"
    assert empty["empty"] is True
    assert empty["error"] is None

    failed = dc.module_status(error="連線失敗", count=0, as_of="2026-11-06", now=SUNDAY, calendar=calendar)
    assert failed["status"] == "error"
    assert failed["empty"] is False
    assert failed["stale"] is False

    unknown = dc.module_status(count=3, as_of=None, now=SUNDAY, calendar=calendar)
    assert unknown["status"] == "unknown"
    assert unknown["stale"] is False

    no_calendar = dc.module_status(count=8, as_of="2026-10-01", now=SUNDAY, calendar=dc.unreliable_calendar())
    assert no_calendar["status"] == "unknown"
    assert no_calendar["stale"] is False


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
    assert "極佳風險回報" not in text
    assert "GitHub 雲端排程託管中" not in text
    assert "排程寫在倉庫，此頁未核對是否正在跑" in text


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


def test_same_session_twse_price_and_yfinance_ma_are_not_conflict():
    view = dc.assemble_market_view(
        twse={"close_raw": 40087.56, "prev_close": 41000, "change": -912.44, "change_pct": -2.23, "date": "20261106", "time": "13:30:00"},
        yfinance_bar={"close_raw": 40087.56, "date": "2026-11-06", "ma20": 41000, "ma60": 40000},
        config={"updated_at": "2026-11-07 07:39:40", "market_regime": {"key": "SIDEWAYS_CONSOLIDATION", "label": "箱型"}, "global_controls": {"cash_target_pct": 45}},
        now=SUNDAY,
        calendar=_cal_2026(),
    )
    assert view["price_source"] == "twse_mis"
    assert view["ma_source"] == "yfinance"
    assert view["status"] != "conflict"
    assert view["quote_inferred_regime"]["status"] == "ok"
    assert view["quote_inferred_regime"]["verified"] is True
    assert view["verified"] is True
    assert view["as_of"] == "2026-11-06"
    mismatched = dc.assemble_market_view(
        twse={"close_raw": 40087.56, "prev_close": 41000, "change": -912.44, "change_pct": -2.23, "date": "20261106"},
        yfinance_bar={"date": "2026-11-05", "ma20": 41000, "ma60": 40000},
        now=SUNDAY,
        calendar=_cal_2026(),
    )
    assert mismatched["quote_inferred_regime"]["status"] == "conflict"
    assert mismatched["quote_inferred_regime"]["verified"] is False
    assert mismatched["quote_inferred_regime"]["label_display"] == "未知"


def test_undated_ma_is_unknown_not_conflict_and_config_time_is_not_as_of():
    view = dc.assemble_market_view(
        twse={"close_raw": 40087.56, "prev_close": 41000, "change": -912.44, "change_pct": -2.23, "date": "20261106", "time": "13:30:00"},
        yfinance_bar=None,
        config={
            "updated_at": "2026-11-07 07:39:40",
            "market_regime": {"key": "SIDEWAYS_CONSOLIDATION", "label": "🟡【高檔震盪整理・箱型洗盤】", "ma20": 48170.13, "ma60": 46113.09, "close": 46041.43},
            "global_controls": {"cash_target_pct": 45},
        },
        now=SUNDAY,
        calendar=_cal_2026(),
    )
    assert view["ma_source"] == "regime_config_snapshot"
    assert view["quote_inferred_regime"]["status"] == "unknown"
    assert view["quote_inferred_regime"]["status"] != "conflict"
    assert view["quote_inferred_regime"]["verified"] is False
    assert view["quote_inferred_regime"]["label_display"] == "未知"
    assert view["as_of"] == "2026-11-06"
    assert view["as_of"] != "2026-11-07"
    assert view["effective_regime"]["cash_display"] == "45%"

    config_only = dc.assemble_market_view(
        twse=None,
        yfinance_bar=None,
        config={
            "updated_at": "2026-11-07 07:39:40",
            "market_regime": {"key": "SIDEWAYS_CONSOLIDATION", "label": "箱型", "close": 46041.43, "ma20": 48000, "ma60": 47000},
            "global_controls": {"cash_target_pct": 0},
        },
        now=SUNDAY,
        calendar=_cal_2026(),
    )
    assert config_only["status"] == "config_snapshot"
    assert config_only["as_of"] is None
    assert config_only["change"] is None
    assert config_only["change_pct"] is None
    assert config_only["effective_regime"]["cash_display"] == "0%"


def test_session_phases_holiday_and_missing_calendar():
    calendar = _cal_2026()
    monday_before = dt.datetime(2026, 11, 9, 8, 30, tzinfo=dc.TAIPEI)
    monday_open = dt.datetime(2026, 11, 9, 10, 0, tzinfo=dc.TAIPEI)
    monday_after = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dc.TAIPEI)
    assert dc.classify_session_freshness("2026-11-06", now=monday_before, calendar=calendar, rhythm="intraday")["freshness"] == "ok"
    assert dc.classify_session_freshness("2026-11-06", now=monday_open, calendar=calendar, rhythm="daily_close")["freshness"] == "ok"
    assert dc.classify_session_freshness("2026-11-09", now=monday_open, calendar=calendar, rhythm="intraday")["freshness"] == "ok"
    assert dc.classify_session_freshness("2026-11-06", now=monday_open, calendar=calendar, rhythm="intraday")["freshness"] == "stale"
    assert dc.classify_session_freshness("2026-11-06", now=monday_after, calendar=calendar, rhythm="daily_close")["freshness"] == "stale"
    assert dc.classify_session_freshness("2026-11-09", now=monday_after, calendar=calendar, rhythm="daily_close")["freshness"] == "ok"
    holiday = dt.datetime(2026, 2, 18, 11, 0, tzinfo=dc.TAIPEI)
    assert dc.expected_session(holiday, calendar, rhythm="daily_close")["expected_session"] == "2026-02-11"
    assert dc.classify_session_freshness("2026-02-11", now=holiday, calendar=calendar)["freshness"] == "ok"
    blind = dc.classify_session_freshness("2026-02-11", now=holiday, calendar=dc.unreliable_calendar())
    assert blind["freshness"] == "unknown"
    assert blind["stale"] is False


def test_index_cache_contract_and_advisor_actual_counts():
    old_cache = {"index_close": 23285.42, "change_pct": -1.39, "time": "13:30:00"}
    unknown = dc.interpret_market_index_cache(old_cache)
    assert unknown["source"] == "unknown"
    assert unknown["source"] != "hardcoded_fallback"
    assert unknown["verified"] is False
    placeholder = dc.interpret_market_index_cache({**old_cache, "observation": "builtin_placeholder"})
    assert placeholder["index_change_pct"] is None
    assert placeholder["status"] == "placeholder"
    verified = dc.interpret_market_index_cache({
        "index_close": 40087.56,
        "change_pct": -2.23,
        "source": "twse_mis",
        "as_of_date": "2026-11-06",
        "feed_time": "13:30:00",
        "observation": "quote",
    })
    assert verified["verified"] is True
    assert verified["index_change_pct"] == -2.23
    undated = dc.interpret_market_index_cache({
        "index_close": 40087.56,
        "change_pct": -2.23,
        "source": "twse_mis",
        "observation": "quote",
    })
    assert undated["verified"] is False
    assert undated["source"] == "twse_mis"
    assert undated["index_change_pct"] == -2.23

    assert dc.summarize_advisor_table(None)["status"] == "missing"
    assert dc.summarize_advisor_table([])["status"] == "empty"
    summary = dc.summarize_advisor_table([
        {"symbol": "1101", "source_strategy": "策略11・營收"},
        {"symbol": "1101", "source_strategy": "策略11・營收"},
        {"symbol": "2330", "source_strategy": "策略01・布林"},
    ])
    assert summary["row_count"] == 3
    assert summary["duplicate_rows"] == 1
    label = dc.advisor_label_for_strategy(summary, "11")
    assert "實際 2 列" in label
    assert "重複 1 列" in label
    assert "實際 2／上限 2" not in label
    assert "實際 0 列" in dc.advisor_label_for_strategy(summary, "06")


def test_quotes_default_stays_compatible_and_js_guards_run():
    import ast
    import subprocess
    tree = ast.parse(Path("line_bot_server.py").read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_strategy_data":
            default = node.args.defaults[-1]
            assert isinstance(default, ast.Constant) and default.value is True
            found = True
    assert found
    script = r"""
const g = require('./web/dashboard_guards.js');
const unknown = g.displayChange(null, null);
if (unknown.text !== '漲跌未知') process.exit(2);
const zero = g.displayChange(0, 0);
if (zero.text !== '0.00 (0.00%)') process.exit(3);
const headline = g.quoteHeadline({status:'ok', verified:true, label:'箱型'});
if (headline !== '報價推斷：箱型') process.exit(4);
const hidden = g.quoteHeadline({status:'unknown', verified:false, label:'空頭'});
if (hidden !== '報價推斷：未知') process.exit(5);
"""
    completed = subprocess.run(["node", "-e", script], cwd=Path(".").resolve(), capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_mocked_page_executes_guards(tmp_path):
    import threading
    from http.server import ThreadingHTTPServer
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    import phase1_local_preview as preview

    server = ThreadingHTTPServer(("127.0.0.1", 0), preview.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    driver = webdriver.Chrome(options=options)
    try:
        driver.get(f"http://127.0.0.1:{port}/?tab=strategies")
        driver.implicitly_wait(2)
        import time
        time.sleep(4)
        page = driver.find_element(By.TAG_NAME, "body").text
        assert "漲跌未知" in page
        assert "報價推斷：🟡【高檔震盪整理・箱型洗盤】" in page
        assert "實際 1 列" in page
        assert "T+1 每策略 2／上限 2" not in page
        assert "極佳風險回報" not in page
    finally:
        driver.quit()
        server.shutdown()
