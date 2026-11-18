# -*- coding: utf-8 -*-
import ast
import datetime
import hashlib
import importlib.util
import io
import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

import cmoney_sync
import four_price_engine
import line_bot_server
import line_sender
import market_schedule
import rolling_retention_manager
import run_all
import trade_history_manager

ROOT_DIR = Path(__file__).resolve().parent.parent
TZ_TW = datetime.timezone(datetime.timedelta(hours=8))


def _load_mod(rel_path: str, mod_name: str):
    p = ROOT_DIR / rel_path
    spec = importlib.util.spec_from_file_location(mod_name, str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeTradeHistory:
    def __init__(self):
        self.records = []

    def record_closed_trade(self, row):
        self.records.append(dict(row))


@pytest.fixture(autouse=True)
def _isolate_env_network_and_history(monkeypatch, tmp_path):
    missing_env = tmp_path / ".missing.env"
    monkeypatch.setattr(cmoney_sync, "ENV_PATH", missing_env)
    monkeypatch.setattr(cmoney_sync, "load_env", lambda: {})
    monkeypatch.setattr(line_sender, "ENV_PATH", missing_env)
    monkeypatch.setattr(line_sender, "load_env", lambda: {})
    monkeypatch.setattr(line_bot_server, "ENV_PATH", missing_env)
    monkeypatch.setattr(line_bot_server, "load_env", lambda: {})
    monkeypatch.setattr(line_bot_server, "ENV", {})
    monkeypatch.setattr(line_bot_server, "CHANNEL_ACCESS_TOKEN", "")
    monkeypatch.setattr(line_bot_server, "LINE_CHANNEL_SECRET", "")

    monkeypatch.setattr(market_schedule, "should_suppress_push", lambda *a, **kw: (False, ""))
    monkeypatch.setattr(market_schedule, "is_intraday_push_suppressed", lambda *a, **kw: (False, ""))

    fake_hist = _FakeTradeHistory()
    monkeypatch.setattr(trade_history_manager, "GLOBAL_TRADE_HISTORY", fake_hist)
    monkeypatch.setattr(rolling_retention_manager, "GLOBAL_TRADE_HISTORY", fake_hist)

    def _block(*a, **kw):
        raise AssertionError("禁止在測試中發起真實網路或子程序呼叫")

    monkeypatch.setattr(subprocess, "run", _block)
    monkeypatch.setattr(subprocess, "Popen", _block)
    monkeypatch.setattr(urllib.request, "urlopen", _block)
    monkeypatch.setattr(line_sender, "send_to_line", lambda *a, **kw: True)
    return fake_hist


def _make_calendar(closed_dates=None, start_d="2026-10-01", end_d="2026-12-31"):
    return {
        "reliable": True,
        "coverage_start": start_d,
        "coverage_end": end_d,
        "covered_from": datetime.date.fromisoformat(start_d),
        "covered_to": datetime.date.fromisoformat(end_d),
        "closed": [datetime.date.fromisoformat(d) for d in (closed_dates or [])],
        "explicit_open": [],
    }


def test_strategy03_empty_writes_today_files_no_push(monkeypatch, tmp_path):
    s03 = _load_mod("strategies/03_short_momentum/screener.py", "s03_screener_test")
    cal = _make_calendar()
    monkeypatch.setattr(rolling_retention_manager, "_get_cached_calendar", lambda: cal)
    monkeypatch.setattr(s03, "screen_short_stocks", lambda **kw: [])
    push_calls = {"n": 0}
    monkeypatch.setattr(s03, "send_to_line", lambda *a, **kw: push_calls.__setitem__("n", push_calls["n"] + 1))

    csv_p = tmp_path / "result.csv"
    md_p = tmp_path / "result.md"
    pd.DataFrame([
        {"證券代號": "2317", "證券名稱": "鴻海", "選股日期": "2026-11-12"},
        {"證券代號": "2454", "證券名稱": "聯發科", "選股日期": "2026-11-05"},
    ]).to_csv(csv_p, index=False, encoding="utf-8-sig")

    monkeypatch.setattr(sys, "argv", ["screener.py", "--export", str(csv_p), "--line", "--date", "20261116"])
    s03.main()

    assert push_calls["n"] == 0
    df_out = pd.read_csv(csv_p, encoding="utf-8-sig")
    assert len(df_out.columns) > 0
    assert "2026-11-12" in set(df_out["選股日期"].astype(str))
    assert "2026-11-05" not in set(df_out["選股日期"].astype(str))
    md_txt = md_p.read_text(encoding="utf-8")
    assert "今日無標的" in md_txt
    assert "2026-11-16" in md_txt

    # Header-only csv
    pd.DataFrame(columns=list(df_out.columns)).to_csv(csv_p, index=False, encoding="utf-8-sig")
    monkeypatch.setattr(sys, "argv", ["screener.py", "--export", str(csv_p), "--date", "20261116"])
    s03.main()
    df_out2 = pd.read_csv(csv_p, encoding="utf-8-sig")
    assert len(df_out2.columns) > 0 and len(df_out2) == 0


def test_dashboard_date_prefers_newer_md_and_scanner_no_demo(monkeypatch, tmp_path):
    md_p = tmp_path / "result.md"
    md_p.write_text("# 戰報\n- 交易日期：`2026-11-16`\n", encoding="utf-8")

    df_old = pd.DataFrame([{"證券代號": "2330", "選股日期": "2026-11-11"}])
    assert line_bot_server.resolve_report_date(df_old, md_p) == "2026-11-16"

    df_new = pd.DataFrame([{"證券代號": "2330", "選股日期": "2026-11-18"}])
    assert line_bot_server.resolve_report_date(df_new, md_p) == "2026-11-18"

    df_empty = pd.DataFrame(columns=["證券代號", "選股日期"])
    assert line_bot_server.resolve_report_date(df_empty, md_p) == "2026-11-16"

    import yfinance as yf
    yf_calls = {"n": 0}
    monkeypatch.setattr(yf, "download", lambda *a, **kw: yf_calls.__setitem__("n", yf_calls["n"] + 1))

    header_csv = tmp_path / "empty_result.csv"
    pd.DataFrame(columns=["證券代號", "證券名稱", "收盤價"]).to_csv(header_csv, index=False, encoding="utf-8-sig")

    scanners = [
        "strategies/01_bollinger_reversal/intraday_scanner.py",
        "strategies/02_institutional_momentum/intraday_scanner.py",
        "strategies/03_short_momentum/intraday_scanner.py",
        "strategies/05_stock_futures_long/intraday_scanner.py",
        "strategies/07_margin_call_ambush/intraday_scanner.py",
    ]
    for idx, rel in enumerate(scanners):
        mod = _load_mod(rel, f"scanner_test_{idx}")
        monkeypatch.setattr(mod, "CSV_FILE", str(header_csv))
        res = mod.load_targets()
        assert res == {}
    assert yf_calls["n"] == 0


def test_retention_trading_days_weekend_and_holiday(monkeypatch):
    cal = _make_calendar(closed_dates=["2026-11-18"])
    monkeypatch.setattr(rolling_retention_manager, "_get_cached_calendar", lambda: cal)

    assert rolling_retention_manager.retention_cutoff("2026-11-16", 5, calendar=cal) == datetime.date(2026, 11, 10)
    assert rolling_retention_manager.retention_cutoff("2026-11-20", 5, calendar=cal) == datetime.date(2026, 11, 13)
    assert rolling_retention_manager.retention_cutoff("2026-11-21", 5, calendar=cal) == datetime.date(2026, 11, 13)
    assert rolling_retention_manager.retention_cutoff("2026-11-23", 5, calendar=cal) == datetime.date(2026, 11, 16)

    # Untrusted/empty calendar falls back to Mon-Fri
    assert rolling_retention_manager.is_trading_day_for_retention("2026-11-16", calendar={}) is True
    assert rolling_retention_manager.is_trading_day_for_retention("2026-11-15", calendar={}) is False

    # Env override & clamping
    monkeypatch.setenv("TW_RETENTION_TRADING_DAYS", "0")
    assert rolling_retention_manager.effective_retention_days(5) == 1
    monkeypatch.setenv("TW_RETENTION_TRADING_DAYS", "999")
    assert rolling_retention_manager.effective_retention_days(5) == 60
    monkeypatch.setenv("TW_RETENTION_TRADING_DAYS", "abc")
    assert rolling_retention_manager.effective_retention_days(7) == 7
    monkeypatch.delenv("TW_RETENTION_TRADING_DAYS", raising=False)
    assert rolling_retention_manager.effective_retention_days(None) == 5


def test_retention_strategy06_stale_purge_and_header(monkeypatch, tmp_path, _isolate_env_network_and_history):
    cal = _make_calendar()
    monkeypatch.setattr(rolling_retention_manager, "_get_cached_calendar", lambda: cal)
    monkeypatch.setattr(rolling_retention_manager, "get_current_stock_price", lambda *a, **kw: 100.0)
    monkeypatch.setattr(rolling_retention_manager, "evaluate_removal_trigger", lambda *a, **kw: (False, "", 0.0))

    csv_p = tmp_path / "s06_result.csv"
    stale_df = pd.DataFrame([
        {"證券代號": "2330", "證券名稱": "台積電", "選股日期": "2026-10-24", "收盤價": 900},
        {"證券代號": "2317", "證券名稱": "鴻海", "選股日期": "2026-10-24", "收盤價": 200},
    ])
    stale_df.to_csv(csv_p, index=False, encoding="utf-8-sig")

    out_df = rolling_retention_manager.save_rolling_result_csv(
        str(csv_p), pd.DataFrame(columns=["證券代號", "證券名稱", "收盤價"]), today_date_str="2026-11-16", max_days=5
    )
    assert len(out_df) == 0 and len(out_df.columns) > 0
    assert len(_isolate_env_network_and_history.records) == 2
    assert all("交易日週期屆滿" in r.get("exit_reason", "") for r in _isolate_env_network_and_history.records)
    reloaded = pd.read_csv(csv_p, encoding="utf-8-sig")
    assert len(reloaded) == 0 and "證券代號" in reloaded.columns

    # Keep 4 recent days (11-10..11-13) on 2026-11-16 -> T+4..T+1
    recent_df = pd.DataFrame([
        {"證券代號": "2330", "證券名稱": "台積電", "選股日期": "2026-11-10", "收盤價": 100},
        {"證券代號": "2317", "證券名稱": "鴻海", "選股日期": "2026-11-11", "收盤價": 100},
        {"證券代號": "2454", "證券名稱": "聯發科", "選股日期": "2026-11-12", "收盤價": 100},
        {"證券代號": "2308", "證券名稱": "台達電", "選股日期": "2026-11-13", "收盤價": 100},
    ])
    kept = rolling_retention_manager.merge_rolling_records(
        recent_df, pd.DataFrame(), today_date_str="2026-11-16", max_days=5, strategy_id="06"
    )
    assert len(kept) == 4
    badges = dict(zip(kept["選股日期"], kept["入選天數"]))
    assert badges["2026-11-10"].startswith("T+4")
    assert badges["2026-11-11"].startswith("T+3")
    assert badges["2026-11-12"].startswith("T+2")
    assert badges["2026-11-13"].startswith("T+1")


def test_retention_existing_semantics_and_callers(monkeypatch):
    cal = _make_calendar()
    monkeypatch.setattr(rolling_retention_manager, "_get_cached_calendar", lambda: cal)
    monkeypatch.setattr(rolling_retention_manager, "get_current_stock_price", lambda *a, **kw: 100.0)
    monkeypatch.setattr(rolling_retention_manager, "evaluate_removal_trigger", lambda *a, **kw: (False, "", 0.0))

    days = ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]
    df = pd.DataFrame()
    for idx, d in enumerate(days):
        new_r = pd.DataFrame([{"證券代號": f"110{idx}", "證券名稱": f"股{idx}", "選股日期": d, "收盤價": 50}])
        df = rolling_retention_manager.merge_rolling_records(df, new_r, today_date_str=d, max_days=5, strategy_id="01")
    assert len(df) == 5
    assert "2026-10-15" not in set(df["選股日期"])

    # PENDING_REMOVAL kept same day, purged next day
    pend = pd.DataFrame([{"證券代號": "9999", "證券名稱": "測試", "選股日期": "2026-10-20", "生命週期狀態": "PENDING_REMOVAL", "剔除觸發日期": "2026-10-21", "收盤價": 50}])
    d1 = rolling_retention_manager.merge_rolling_records(pend, pd.DataFrame(), today_date_str="2026-10-21", max_days=5)
    assert len(d1) == 1
    d2 = rolling_retention_manager.merge_rolling_records(d1, pd.DataFrame(), today_date_str="2026-10-22", max_days=5)
    assert len(d2) == 0

    for sp in sorted((ROOT_DIR / "strategies").glob("*/screener.py")):
        tree = ast.parse(sp.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "save_rolling_result_csv":
                for kw in node.keywords:
                    if kw.arg == "max_days":
                        assert isinstance(kw.value, ast.Constant) and kw.value.value == 5


def test_cmoney_nonblocking_timeout_404_and_no_secrets(monkeypatch, tmp_path, capsys):
    st_path = tmp_path / "cmoney_status.json"

    def _slow(**kw):
        time.sleep(3.0)
        return {"cloud_sync_success": True}

    t0 = time.monotonic()
    r_to = run_all.run_cmoney_sync_nonblocking(timeout=1, sync_fn=_slow, status_path=st_path)
    elapsed = time.monotonic() - t0
    assert r_to["status"] == "timeout"
    assert elapsed < 2.5
    assert "CMoney 同步失敗" in capsys.readouterr().out

    def _err404(**kw):
        raise urllib.error.HTTPError("https://example.invalid", 404, "Not Found", {}, io.BytesIO(b""))

    r_404 = run_all.run_cmoney_sync_nonblocking(timeout=5, sync_fn=_err404, status_path=st_path)
    assert r_404["status"] == "failed" and r_404["reason"] == "HTTPError"

    r_ok = run_all.run_cmoney_sync_nonblocking(timeout=5, sync_fn=lambda **kw: {"cloud_sync_success": True}, status_path=st_path)
    assert r_ok["status"] == "ok"

    r_none = run_all.run_cmoney_sync_nonblocking(timeout=5, sync_fn=lambda **kw: None, status_path=st_path)
    assert r_none["status"] == "done"

    st_txt = st_path.read_text(encoding="utf-8")
    assert "SENTINEL" not in st_txt

    # run_all.main() with CMoney exception
    import market_analyst
    monkeypatch.setattr(market_schedule, "check_market_open_or_exit", lambda *a, **kw: True)
    monkeypatch.setattr(market_schedule, "verify_twse_data_settlement", lambda *a, **kw: None)
    monkeypatch.setattr(market_analyst, "run_market_analysis", lambda *a, **kw: None)
    monkeypatch.setattr(market_schedule, "is_daily_screener_window", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(run_all, "run_cmoney_sync_nonblocking", lambda *a, **kw: {"status": "failed", "reason": "HTTPError"})
    monkeypatch.setattr(run_all, "run_strategy", lambda strat, line=False, force=False: {"id": strat["id"], "name": strat["name"], "returncode": 0, "duration": 0.1, "stderr": ""})
    monkeypatch.setattr(sys, "argv", ["run_all.py"])
    assert run_all.main() == 0

    monkeypatch.setattr(run_all, "run_strategy", lambda strat, line=False, force=False: {"id": strat["id"], "name": strat["name"], "returncode": 1, "duration": 0.1, "stderr": "err"})
    assert run_all.main() == 1

    # Fake credentials 404 login -> no SENTINEL in stdout
    monkeypatch.setenv("CMONEY_ACCOUNT", "acc9SENTINEL")
    monkeypatch.setenv("CMONEY_PASSWORD", "pw9SENTINEL")
    monkeypatch.delenv("CMONEY_AUTH_TOKEN", raising=False)

    class _Resp404:
        status = 404
        def read(self): return b""

    class _Opener:
        def open(self, req, timeout=10): return _Resp404()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **kw: _Opener())
    c = cmoney_sync.CMoneyWatchlistClient()
    ok, _ = c.authenticate()
    assert ok is False
    assert "SENTINEL" not in capsys.readouterr().out


def test_cmoney_failure_appended_to_afterhours_line_body(monkeypatch, tmp_path):
    import market_analyst
    monkeypatch.setattr(market_schedule, "check_market_open_or_exit", lambda *a, **kw: True)
    monkeypatch.setattr(market_schedule, "verify_twse_data_settlement", lambda *a, **kw: None)
    monkeypatch.setattr(market_analyst, "run_market_analysis", lambda *a, **kw: None)
    monkeypatch.setattr(market_schedule, "is_daily_screener_window", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(run_all, "run_strategy", lambda strat, line=False, force=False: {"id": strat["id"], "name": strat["name"], "returncode": 0, "duration": 0.1, "stderr": ""})
    monkeypatch.setattr(run_all, "ROOT_DIR", tmp_path)

    import lab.engine.fusion as fusion_mod
    monkeypatch.setattr(fusion_mod.ScreenerFusionBridge, "generate_next_day_advisor_report", staticmethod(lambda **kw: {"csv_content": "a,b\n1,2\n", "total_evaluated": 1, "strong_buy_count": 1}))
    monkeypatch.setattr(fusion_mod.ScreenerFusionBridge, "format_advisor_line_text", staticmethod(lambda rep: "明日買賣建議正文"))

    sent_msgs = []
    monkeypatch.setattr(line_sender, "send_to_line", lambda msg, **kw: sent_msgs.append(msg) or True)

    monkeypatch.setattr(run_all, "run_cmoney_sync_nonblocking", lambda *a, **kw: {"status": "failed", "reason": "HTTPError"})
    monkeypatch.setattr(sys, "argv", ["run_all.py", "--line"])
    assert run_all.main() == 0
    assert len(sent_msgs) == 1 and "CMoney 同步失敗" in sent_msgs[0]

    sent_msgs.clear()
    monkeypatch.setattr(run_all, "run_cmoney_sync_nonblocking", lambda *a, **kw: {"status": "ok", "reason": "ok"})
    assert run_all.main() == 0
    assert len(sent_msgs) == 1 and "CMoney 同步失敗" not in sent_msgs[0]

    sent_msgs.clear()
    monkeypatch.setattr(sys, "argv", ["run_all.py"])
    assert run_all.main() == 0
    assert len(sent_msgs) == 0


def test_normalize_to_tick_nan_inf_and_strategy09_skips_one(monkeypatch):
    assert four_price_engine.normalize_to_tick(float("nan"), "up") == 0.0
    assert four_price_engine.normalize_to_tick(float("inf"), "down") == 0.0
    assert four_price_engine.normalize_to_tick(58.60, "nearest") == 58.6
    assert four_price_engine.normalize_to_tick(100.0, "up") == 100.0

    s09 = _load_mod("strategies/09_treasury_stock_buyback/screener.py", "s09_screener_test")
    pool = [
        {"code": "9991", "name": "異常股", "announcement_date": "2026-11-15", "price_min": 40, "price_max": 60, "planned_shares": 10000, "capital_ratio_pct": 2.0},
        {"code": "9992", "name": "正常股", "announcement_date": "2026-11-15", "price_min": 40, "price_max": 60, "planned_shares": 10000, "capital_ratio_pct": 2.0},
    ]

    def _fake_mkt(code, *a, **kw):
        if code == "9991":
            return {"close": float("nan"), "volume": 2000, "volume_5ma": 2000, "pb_ratio": 1.0, "ma20": 50.0, "book_value": 50.0, "high": float("nan"), "low": float("nan"), "institutional_net": 100, "change": 0.0, "pct_change": 0.0, "ma60": 50.0, "open": 50.0, "is_disposal": False, "is_full_cash": False}
        return {"close": 45.0, "volume": 2000, "volume_5ma": 2000, "pb_ratio": 1.0, "ma20": 44.0, "book_value": 45.0, "high": 46.0, "low": 44.0, "institutional_net": 100, "change": 0.5, "pct_change": 1.1, "ma60": 43.0, "open": 44.5, "is_disposal": False, "is_full_cash": False}

    monkeypatch.setattr(s09, "fetch_stock_market_data", _fake_mkt)
    monkeypatch.setattr(s09, "evaluate_overnight_broker_risk", lambda *a, **kw: {"is_overnight_risk": False})
    recs, _tier = s09.apply_treasury_screening(pool, {}, [], target_date_str="2026-11-16")
    codes = [r["證券代號"] for r in recs]
    assert "9991" not in codes and "9992" in codes


def test_failed_strategy_does_not_overwrite_good_results(monkeypatch, tmp_path):
    s09 = _load_mod("strategies/09_treasury_stock_buyback/screener.py", "s09_screener_fail_test")
    csv_p = tmp_path / "result.csv"
    md_p = tmp_path / "result.md"
    csv_p.write_text("證券代號,證券名稱\n2330,台積電\n", encoding="utf-8-sig")
    md_p.write_text("# 有效戰報\n", encoding="utf-8")
    h_csv = hashlib.sha256(csv_p.read_bytes()).hexdigest()
    h_md = hashlib.sha256(md_p.read_bytes()).hexdigest()

    monkeypatch.setattr(s09, "CSV_FILE", str(csv_p))
    monkeypatch.setattr(s09, "MD_FILE", str(md_p))
    monkeypatch.setattr(s09, "load_treasury_pool", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fatal_before_write")))

    with pytest.raises(RuntimeError):
        s09.run_screener(export_csv=True, send_line=False)

    assert hashlib.sha256(csv_p.read_bytes()).hexdigest() == h_csv
    assert hashlib.sha256(md_p.read_bytes()).hexdigest() == h_md


def test_workflow_daily_screener_git_sync_always_in_window():
    # Note: YAML changes require git push to take effect on GitHub Actions.
    yml = (ROOT_DIR / ".github/workflows/cloud_daily_screener.yml").read_text(encoding="utf-8")
    lines = yml.splitlines()
    sync_idx = next(i for i, l in enumerate(lines) if "自動同步最新選股報表與明日買賣建議回 Git 倉庫" in l)
    sync_if = lines[sync_idx + 1].strip()
    assert "always()" in sync_if and "steps.check_window.outputs.in_window == 'true'" in sync_if

    art_idx = next(i for i, l in enumerate(lines) if "封存選股報表 Artifact" in l)
    art_if = lines[art_idx + 1].strip()
    assert "always()" in art_if and "steps.check_window.outputs.in_window == 'true'" in art_if


def test_etf_auto_window_bounds_and_delay_times():
    s12 = _load_mod("strategies/12_etf_event_radar/screener.py", "s12_screener_win_test")
    assert s12.is_etf_premarket_window(datetime.datetime(2026, 11, 16, 8, 20, 0, tzinfo=TZ_TW))[0] is True
    assert s12.is_etf_premarket_window(datetime.datetime(2026, 11, 16, 8, 59, 59, tzinfo=TZ_TW))[0] is True
    assert s12.is_etf_premarket_window(datetime.datetime(2026, 11, 16, 8, 19, 59, tzinfo=TZ_TW))[0] is False
    assert s12.is_etf_premarket_window(datetime.datetime(2026, 11, 16, 9, 0, 0, tzinfo=TZ_TW))[0] is False
    assert s12.is_etf_premarket_window(datetime.datetime(2026, 11, 16, 4, 50, 0, tzinfo=TZ_TW))[0] is False
    assert s12.is_etf_premarket_window(datetime.datetime(2026, 11, 16, 21, 15, 0, tzinfo=TZ_TW))[0] is False
    assert s12.is_etf_premarket_window(datetime.datetime(2026, 11, 21, 8, 30, 0, tzinfo=TZ_TW))[0] is False

    utc = datetime.timezone.utc
    assert s12.is_etf_premarket_window(datetime.datetime(2026, 11, 13, 20, 50, 0, tzinfo=utc))[0] is False
    assert s12.is_etf_premarket_window(datetime.datetime(2026, 11, 16, 0, 25, 0, tzinfo=utc))[0] is True

    etf_yml = (ROOT_DIR / ".github/workflows/cloud_etf_radar.yml").read_text(encoding="utf-8")
    assert "0820" in etf_yml and "0859" in etf_yml
    assert '            if [ "$TW_TIME" -lt 0900 ]; then' not in etf_yml


def test_etf_force_and_run_all_cannot_bypass_premarket_window(monkeypatch, tmp_path):
    s12 = _load_mod("strategies/12_etf_event_radar/screener.py", "s12_screener_force_test")
    monkeypatch.setattr(s12, "PREMARKET_FILE", str(tmp_path / "premarket_candidates.json"))
    monkeypatch.setattr(s12, "load_etf_universe", lambda: {"0050": {"code": "0050", "name": "台灣50", "anchor": "SPY"}})
    monkeypatch.setattr(s12, "fetch_overseas_benchmarks", lambda **kw: {"SPY": {"chg_pct": 2.0}})
    monkeypatch.setattr(s12, "fetch_batch_quotes", lambda items: {"0050": {"yesterday_close": 100.0, "price": 100.0}})
    monkeypatch.setattr(s12, "calculate_dynamic_theo_chg", lambda item, bm: (2.0, "SPY +2%"))

    sent = {"n": 0}
    monkeypatch.setattr(s12, "send_to_line", lambda *a, **kw: sent.__setitem__("n", sent["n"] + 1) or True)

    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: datetime.datetime(2026, 11, 16, 21, 15, 0, tzinfo=TZ_TW))
    cands = s12.run_screener(send_line=True, export_path=str(tmp_path / "result.csv"))
    assert len(cands) == 1 and sent["n"] == 0

    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: datetime.datetime(2026, 11, 16, 4, 50, 0, tzinfo=TZ_TW))
    s12.run_screener(send_line=True, export_path=str(tmp_path / "result.csv"))
    assert sent["n"] == 0

    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: datetime.datetime(2026, 11, 16, 8, 25, 0, tzinfo=TZ_TW))
    s12.run_screener(send_line=True, export_path=str(tmp_path / "result.csv"))
    assert sent["n"] == 1


def test_etf_manual_premarket_default_restricted():
    etf_yml = (ROOT_DIR / ".github/workflows/cloud_etf_radar.yml").read_text(encoding="utf-8")
    idx = etf_yml.index('if [ "$STAGE" = "premarket" ]; then')
    elif_idx = etf_yml.index('elif [ "$STAGE" = "confirm" ]; then')
    block = etf_yml[idx:elif_idx]
    assert "0820" in block and "0859" in block and "exit 0" in block


def test_etf_title_uses_taipei_clock(monkeypatch, tmp_path):
    s12 = _load_mod("strategies/12_etf_event_radar/screener.py", "s12_screener_clock_test")
    monkeypatch.setattr(s12, "PREMARKET_FILE", str(tmp_path / "premarket_candidates.json"))
    monkeypatch.setattr(s12, "load_etf_universe", lambda: {"0050": {"code": "0050", "name": "台灣50", "anchor": "SPY"}})
    monkeypatch.setattr(s12, "fetch_overseas_benchmarks", lambda **kw: {"SPY": {"chg_pct": 2.0}})
    monkeypatch.setattr(s12, "fetch_batch_quotes", lambda items: {"0050": {"yesterday_close": 100.0, "price": 100.0}})
    monkeypatch.setattr(s12, "calculate_dynamic_theo_chg", lambda item, bm: (2.0, "SPY +2%"))

    msgs = []
    monkeypatch.setattr(s12, "send_to_line", lambda msg, **kw: msgs.append(msg) or True)
    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: datetime.datetime(2026, 11, 16, 8, 25, 0, tzinfo=TZ_TW))

    s12.run_screener(send_line=True, export_path=str(tmp_path / "result.csv"))
    assert len(msgs) == 1
    assert "08:25" in msgs[0]
