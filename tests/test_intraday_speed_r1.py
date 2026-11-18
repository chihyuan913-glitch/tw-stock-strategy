# -*- coding: utf-8 -*-
"""
盤中速度第一份 (第 1, 2, 3, 6 項) 離線契約測試。
全程使用 tmp_path 與假時鐘/假推播/假行情，不連網、不寫正式路徑。
"""

from __future__ import annotations

import ast
import datetime
import inspect
import io
import json
import re
import socket
import subprocess
import sys
import urllib.error
from decimal import Decimal
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import intraday_speed
import intraday_task_runner
import line_sender
import market_schedule
import mis_quote
import order_hint
import radar_manager

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))
TZ_UTC = datetime.timezone.utc


def _mock_trading_day(d) -> bool:
    day_date = d.date() if isinstance(d, datetime.datetime) else d
    if day_date.weekday() >= 5:
        return False
    if day_date == datetime.date(2026, 11, 18):
        return False
    return True


def test_push_window_taipei_boundaries(monkeypatch):
    monkeypatch.setattr(intraday_speed, "is_trading_day", _mock_trading_day)
    cases = [
        (datetime.datetime(2026, 11, 16, 8, 57, 59, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 8, 58, 0, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 8, 59, 59, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 9, 0, 0, tzinfo=TZ_TW), True),
        (datetime.datetime(2026, 11, 16, 13, 29, 59, tzinfo=TZ_TW), True),
        (datetime.datetime(2026, 11, 16, 13, 30, 0, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 13, 30, 1, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 15, 27, 6, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 21, 15, 0, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 18, 10, 0, 0, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 21, 10, 0, 0, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 22, 21, 20, 0, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 1, 0, 0, tzinfo=TZ_UTC), True),
        (datetime.datetime(2026, 11, 16, 5, 29, 59, tzinfo=TZ_UTC), True),
        (datetime.datetime(2026, 11, 16, 5, 30, 0, tzinfo=TZ_UTC), False),
        (datetime.datetime(2026, 11, 16, 7, 27, 6, tzinfo=TZ_UTC), False),
        (datetime.datetime(2026, 11, 21, 1, 0, 0, tzinfo=TZ_UTC), False),
        (datetime.datetime(2026, 11, 20, 17, 0, 0, tzinfo=TZ_UTC), False),
    ]
    for dt, expected in cases:
        ok, reason = intraday_speed.is_radar_push_allowed(dt)
        assert ok is expected, f"{dt.isoformat()} expected {expected}, got {ok} ({reason})"


def test_runner_window_boundaries(monkeypatch):
    monkeypatch.setattr(intraday_speed, "is_trading_day", _mock_trading_day)
    cases = [
        (datetime.datetime(2026, 11, 16, 8, 57, 59, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 8, 58, 0, tzinfo=TZ_TW), True),
        (datetime.datetime(2026, 11, 16, 13, 29, 59, tzinfo=TZ_TW), True),
        (datetime.datetime(2026, 11, 16, 13, 30, 0, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 15, 27, 6, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 21, 15, 0, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 21, 10, 0, 0, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 18, 10, 0, 0, tzinfo=TZ_TW), False),
        (datetime.datetime(2026, 11, 16, 0, 58, 0, tzinfo=TZ_UTC), True),
        (datetime.datetime(2026, 11, 16, 0, 57, 59, tzinfo=TZ_UTC), False),
    ]
    for dt, expected in cases:
        ok, reason = intraday_speed.is_runner_window(dt)
        assert ok is expected, f"{dt.isoformat()} expected {expected}, got {ok} ({reason})"


def test_scan_blocked_outside_window_no_fetch_no_push(monkeypatch, tmp_path):
    monkeypatch.setenv("TW_INTRADAY_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setattr(intraday_speed, "is_trading_day", _mock_trading_day)
    monkeypatch.setattr(intraday_speed, "mono_now", lambda: 1000.0)

    fake_watchlist = {
        "2330": {
            "code": "2330",
            "name": "台積電",
            "direction": "LONG",
            "entry_price": 100.0,
            "addon_price": 105.0,
            "tp1_price": 110.0,
            "tp2_price": 120.0,
            "sl_price": 95.0,
            "target_id": "TEST_TARGET",
        }
    }
    fetch_calls = []
    push_calls = []

    class _AlwaysConfirm:
        def check_confirmation(self, *args, **kwargs):
            return True, "confirmed"

    def fake_fetch(codes, **kwargs):
        fetch_calls.append(list(codes))
        return {
            "2330": {
                "code": "2330",
                "name": "台積電",
                "price": 94.0,
                "prev_close": 100.0,
                "price_source": "z",
                "feed_date": "20261116",
                "_fetched_at": 1000.0,
            }
        }

    def fake_send(msg, **kwargs):
        push_calls.append((msg, kwargs))
        return True

    monkeypatch.setattr(radar_manager, "load_watchlist", lambda: json.loads(json.dumps(fake_watchlist)))
    monkeypatch.setattr(radar_manager, "fetch_batch_realtime_quotes", fake_fetch)
    monkeypatch.setattr(radar_manager, "send_to_line", fake_send)
    monkeypatch.setattr(radar_manager, "GLOBAL_TICK_GUARD", _AlwaysConfirm())
    monkeypatch.setattr(radar_manager, "save_watchlist", lambda w: None)
    monkeypatch.setattr(radar_manager, "get_radar_channel", lambda: "")

    blocked_times = [
        datetime.datetime(2026, 11, 16, 8, 59, 59, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 16, 13, 30, 0, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 16, 15, 27, 6, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 16, 21, 15, 0, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 21, 10, 0, 0, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 18, 10, 0, 0, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 16, 5, 30, 0, tzinfo=TZ_UTC),
    ]
    for dt in blocked_times:
        res = radar_manager.scan_and_generate_alerts("TOKEN", "U_TEST", now_tw=dt, advisor={})
        assert res == []
    assert len(fetch_calls) == 0
    assert len(push_calls) == 0

    ok_dt = datetime.datetime(2026, 11, 16, 10, 0, 0, tzinfo=TZ_TW)
    res_ok = radar_manager.scan_and_generate_alerts("TOKEN", "U_TEST", now_tw=ok_dt, advisor={})
    assert len(res_ok) == 1
    assert len(push_calls) == 1
    msg_sent, kw_sent = push_calls[0]
    assert "📝" in msg_sent["_tg_card"]
    assert "停損價" in msg_sent["_tg_card"]
    assert "intraday_ledger_dir" in kw_sent


def test_no_force_or_test_flag_bypass(monkeypatch):
    monkeypatch.setattr(intraday_speed, "is_trading_day", _mock_trading_day)
    sig = inspect.signature(radar_manager.scan_and_generate_alerts)
    assert "force" not in sig.parameters
    assert "force_pass" not in sig.parameters

    monkeypatch.setattr(sys, "argv", ["prog", "--force", "--test-push", "--force-test"])
    res = radar_manager.scan_and_generate_alerts(
        "TOKEN",
        "U_TEST",
        now_tw=datetime.datetime(2026, 11, 16, 13, 30, 5, tzinfo=TZ_TW),
        advisor={},
    )
    assert res == []

    monkeypatch.setenv("TW_RADAR_STRICT_WINDOW", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_FAKE_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "TEST_FAKE_CHAT")
    monkeypatch.delenv("TW_INTRADAY_LEDGER_DIR", raising=False)
    monkeypatch.setattr(line_sender, "load_env", lambda: {})
    monkeypatch.setattr(market_schedule, "is_intraday_push_suppressed", lambda **kw: (False, ""))
    monkeypatch.setattr(market_schedule, "should_suppress_push", lambda **kw: (False, ""))

    tg_calls = []
    monkeypatch.setattr(line_sender, "send_telegram", lambda *a, **kw: tg_calls.append((a, kw)) or True)

    card = line_sender.build_price_alert_card(
        {"code": "2330", "name": "台積電", "sl_price": 95.0},
        {"price": 94.0, "time": "10:00:00"},
        "STOP_LOSS",
        "停損警訊",
        "測試",
        strategy_id="RADAR",
    )

    monkeypatch.setattr(intraday_speed, "taipei_now", lambda: datetime.datetime(2026, 11, 16, 13, 30, 0, tzinfo=TZ_TW))
    assert line_sender.send_to_line(card, target_id="TEST_FAKE", force=True, is_test=True) is False
    assert len(tg_calls) == 0

    monkeypatch.setattr(intraday_speed, "taipei_now", lambda: datetime.datetime(2026, 11, 16, 8, 59, 59, tzinfo=TZ_TW))
    assert line_sender.send_to_line(card, target_id="TEST_FAKE", force=True, is_test=True) is False
    assert len(tg_calls) == 0

    monkeypatch.setattr(intraday_speed, "taipei_now", lambda: datetime.datetime(2026, 11, 16, 10, 0, 0, tzinfo=TZ_TW))
    assert line_sender.send_to_line(card, target_id="TEST_FAKE", force=True, is_test=True) is True
    assert len(tg_calls) == 1

    spawn_calls = []
    for flag in ["--force", "--test-push", "--force-test", "--once"]:
        rc = intraday_task_runner.main([flag], spawn_fn=lambda *a, **kw: spawn_calls.append(a))
        assert rc == 2
    assert len(spawn_calls) == 0


def test_order_price_tick_normalization():
    stock_cases = [
        (9.99, "buy", "2330", "9.99"),
        (9.991, "buy", "2330", "10"),
        (10.02, "sell", "2330", "10"),
        (10.02, "buy", "2330", "10.05"),
        (49.97, "buy", "2330", "50"),
        (49.97, "sell", "2330", "49.95"),
        (50.04, "sell", "2330", "50"),
        (99.95, "buy", "2330", "100"),
        (100.2, "sell", "2330", "100"),
        (100.2, "buy", "2330", "100.5"),
        (499.6, "buy", "2330", "500"),
        (500.4, "sell", "2330", "500"),
        (999.5, "buy", "2330", "1000"),
        (1002, "sell", "2330", "1000"),
        (1002, "buy", "2330", "1005"),
        (49.991, "buy", "0050", "50"),
        (52.02, "sell", "0050", "52"),
        (52.02, "buy", "00878", "52.05"),
        (12.345, "sell", "00878", "12.34"),
        (np.float32(58.6), "sell", "2330", "58.6"),
        (np.float32(24.25), "buy", "2330", "24.25"),
        (np.float32(1440.0), "sell", "2330", "1440"),
    ]
    for raw, side, code, exp in stock_cases:
        dec = order_hint.normalize_price(raw, side, code)
        assert dec is not None
        out = order_hint.format_price(dec)
        assert out == exp, f"{raw} {side} {code} -> {out} != {exp}"
        assert re.match(r"^\d+(\.\d{1,2})?$", out)

    for bad in [None, 0, -1, float("nan"), float("inf"), "-", ""]:
        assert order_hint.normalize_price(bad, "buy", "2330") is None

    ld, lu = order_hint.limit_prices(100, "2330")
    assert order_hint.format_price(ld) == "90"
    assert order_hint.format_price(lu) == "110"

    buy_clamped = min(order_hint.normalize_price(112, "buy", "2330"), lu)
    sell_clamped = max(order_hint.normalize_price(88, "sell", "2330"), ld)
    assert order_hint.format_price(buy_clamped) == "110"
    assert order_hint.format_price(sell_clamped) == "90"


def test_order_hint_content_and_invalid_quote_skip(monkeypatch):
    monkeypatch.delenv("TW_ORDER_RISK_BUDGET_TWD", raising=False)
    monkeypatch.delenv("TW_ORDER_MAX_LOTS", raising=False)
    monkeypatch.delenv("TW_ORDER_MAX_NOTIONAL_TWD", raising=False)

    now_tw = datetime.datetime(2026, 11, 16, 10, 0, 0, tzinfo=TZ_TW)
    mono = 500.0
    q_ok = {
        "code": "2330",
        "price": 58.5,
        "prev_close": 58.0,
        "price_source": "z",
        "feed_date": "20261116",
        "_fetched_at": 495.0,
    }
    item_a = {"code": "2330", "name": "測試股", "entry_price": float(np.float32(58.6)), "sl_price": 55.3}
    hint_a = order_hint.build_order_hint(item_a, q_ok, "ENTRY", False, now_tw=now_tw, mono_now=mono, advisor={})
    assert hint_a is not None
    assert hint_a["direction"] == "多"
    assert hint_a["action"] == "買進"
    assert hint_a["order_price"] == "58.5"
    assert hint_a["stop_text"] == "55.3"
    assert hint_a["lots_text"] == "3 張"

    hint_b = order_hint.build_order_hint(
        item_a,
        q_ok,
        "ENTRY",
        False,
        now_tw=now_tw,
        mono_now=mono,
        advisor={"2330": {"symbol": "2330", "recommended_shares": "2100"}},
    )
    assert hint_b["lots_text"] == "2 張＋零股 100 股（明日建議表）"

    q_high = dict(q_ok, price=1440.0, prev_close=1400.0)
    hint_c = order_hint.build_order_hint(
        {"code": "2330", "sl_price": 1300.0},
        q_high,
        "ENTRY",
        False,
        now_tw=now_tw,
        mono_now=mono,
        advisor={},
    )
    assert hint_c["lots_text"] == "0 張（單張風險 140000 元超過預算 10000 元）"

    q_500 = dict(q_ok, price=500.0, prev_close=500.0)
    hint_d = order_hint.build_order_hint(
        {"code": "2330", "sl_price": 499.0},
        q_500,
        "ENTRY",
        False,
        now_tw=now_tw,
        mono_now=mono,
        advisor={},
    )
    assert hint_d["lots_text"] == "2 張"

    q_short = dict(q_ok, price=101.0, prev_close=100.0)
    hint_e = order_hint.build_order_hint(
        {"code": "2330", "sl_price": 100.0},
        q_short,
        "STOP_LOSS",
        True,
        now_tw=now_tw,
        mono_now=mono,
        advisor={},
    )
    assert hint_e["direction"] == "空"
    assert hint_e["action"] == "停損回補（買進）"
    assert hint_e["lots_text"] == "空單全部"

    for bad_q in [
        dict(q_ok, price_source="y"),
        dict(q_ok, feed_date="20261115"),
        dict(q_ok, _fetched_at=480.0),
        dict(q_ok, price=None),
    ]:
        assert order_hint.build_order_hint(item_a, bad_q, "ENTRY", False, now_tw=now_tw, mono_now=mono, advisor={}) is None

    rt = {"price": 58.5, "high": 58.5, "low": 58.5, "time": "10:00:00"}
    tg_html = line_sender.format_telegram_price_alert_card(item_a, rt, "ENTRY", "買點", "指引", strategy_id="RADAR", order_hint=hint_a)
    plain_txt = line_sender.format_clean_price_alert_text(item_a, rt, "ENTRY", "買點", "指引", strategy_id="RADAR", order_hint=hint_a)
    for txt in (tg_html, plain_txt):
        assert "下單參數" in txt
        assert "2330" in txt
        assert "58.5" in txt
        assert not re.search(r"\d+\.\d{3,}", txt)

    tg_none = line_sender.format_telegram_price_alert_card(item_a, rt, "ENTRY", "買點", "指引", strategy_id="RADAR", order_hint=None)
    plain_none = line_sender.format_clean_price_alert_text(item_a, rt, "ENTRY", "買點", "指引", strategy_id="RADAR", order_hint=None)
    assert "下單參數" not in tg_none
    assert "下單參數" not in plain_none


def test_mis_limiter_backoff_and_batching(monkeypatch):
    fake_t = [100.0]
    monkeypatch.setattr(intraday_speed, "mono_now", lambda: fake_t[0])
    radar_manager._QUOTE_CACHE.clear()
    radar_manager._QUOTE_CACHE_TIME = 0.0

    lim = intraday_speed.MisRateLimiter(3, 5, 10, 120, clock=lambda: fake_t[0])
    assert [lim.acquire() for _ in range(5)] == [True, True, True, False, False]
    fake_t[0] += 5.0
    assert lim.acquire() is True

    class _FakeResp:
        def __init__(self, payload):
            self._data = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._data

    lim2 = intraday_speed.MisRateLimiter(10, 5, 10, 120, clock=lambda: fake_t[0])
    calls = []

    def opener_429(req, timeout=5):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(b""))

    radar_manager.fetch_batch_realtime_quotes(["2330"], max_cache_sec=0, limiter=lim2, opener=opener_429)
    assert len(calls) == 1
    radar_manager.fetch_batch_realtime_quotes(["2330"], max_cache_sec=0, limiter=lim2, opener=opener_429)
    assert len(calls) == 1
    fake_t[0] += 10.0
    radar_manager.fetch_batch_realtime_quotes(["2330"], max_cache_sec=0, limiter=lim2, opener=opener_429)
    assert len(calls) == 2
    fake_t[0] += 19.0
    radar_manager.fetch_batch_realtime_quotes(["2330"], max_cache_sec=0, limiter=lim2, opener=opener_429)
    assert len(calls) == 2
    fake_t[0] += 1.0
    for _ in range(6):
        lim2.report_failure()
    assert lim2.report_failure() == 120.0
    lim2.report_success()
    assert lim2.acquire() is True

    lim3 = intraday_speed.MisRateLimiter(10, 5, 10, 120, clock=lambda: fake_t[0])
    radar_manager.fetch_batch_realtime_quotes(["2330"], max_cache_sec=0, limiter=lim3, opener=lambda r, timeout=5: (_ for _ in ()).throw(socket.timeout("t")))
    assert lim3.acquire() is False

    lim4 = intraday_speed.MisRateLimiter(10, 5, 10, 120, clock=lambda: fake_t[0])
    radar_manager.fetch_batch_realtime_quotes(["2330"], max_cache_sec=0, limiter=lim4, opener=lambda r, timeout=5: (_ for _ in ()).throw(urllib.error.URLError("u")))
    assert lim4.acquire() is False

    urls_40 = []

    def opener_ok(req, timeout=5):
        urls_40.append(req.full_url)
        return _FakeResp({"msgArray": [{"c": "2330", "n": "台積電", "z": "100", "y": "99", "d": "20261116"}]})

    codes_40 = [f"{2000 + i}" for i in range(40)]
    lim_wide = intraday_speed.MisRateLimiter(10, 5, 10, 120, clock=lambda: fake_t[0])
    radar_manager.fetch_batch_realtime_quotes(codes_40, max_cache_sec=0, limiter=lim_wide, opener=opener_ok)
    assert len(urls_40) == 3
    for u in urls_40:
        assert "tse_" in u and "otc_" in u
        assert u.count("tse_") <= 15

    urls_50 = []
    codes_50 = [f"{3000 + i}" for i in range(50)]
    lim_3 = intraday_speed.MisRateLimiter(3, 5, 10, 120, clock=lambda: fake_t[0])
    radar_manager.fetch_batch_realtime_quotes(
        codes_50,
        max_cache_sec=0,
        limiter=lim_3,
        opener=lambda req, timeout=5: urls_50.append(req.full_url) or _FakeResp({"msgArray": []}),
    )
    assert len(urls_50) == 3

    radar_manager._QUOTE_CACHE["2330"] = {
        "code": "2330",
        "price": 100.0,
        "price_source": "z",
        "feed_date": "20261116",
        "_fetched_at": 10.0,
    }
    fake_t[0] = 50.0
    lim_blocked = intraday_speed.MisRateLimiter(1, 5, 10, 120, clock=lambda: fake_t[0])
    lim_blocked.report_failure()
    got = radar_manager.fetch_batch_realtime_quotes(["2330"], max_cache_sec=0, limiter=lim_blocked, opener=opener_ok)
    assert got["2330"]["_fetched_at"] == 10.0
    valid, _ = order_hint.quote_is_valid(got["2330"], datetime.datetime(2026, 11, 16, 10, 0, 0, tzinfo=TZ_TW), fake_t[0])
    assert valid is False


def test_priority_pool_scheduler_intervals(monkeypatch, tmp_path):
    csv_p = tmp_path / "advisor.csv"
    header = "tomorrow_action,symbol,name,source_strategy,verdict,close_price,entry_price,addon_price,tp1_price,tp2_price,stop_loss_price,rr_ratio,expectancy_pct,recommended_shares\n"
    rows = (
        "🟢 優先買進 (STRONG BUY),2330,台積電,01,PASS,100,100,105,110,120,95,2.0,1.5,1000\n"
        "🟢 優先買進 (STRONG BUY),2317,鴻海,02,PASS,100,100,105,110,120,95,2.0,1.5,1000\n"
        "🟡 拉回低接 (BUY ON DIP),2454,聯發科,01,PASS,100,100,105,110,120,95,2.0,1.5,1000\n"
        "🛑 建議剔除 (FILTERED),2603,長榮,01,FAIL,100,100,105,110,120,95,2.0,1.5,1000\n"
        "🔴 建議放空 (SHORT),2609,陽明,03,PASS,100,100,95,90,85,105,2.0,1.5,1000\n"
    )
    csv_p.write_text(header + rows, encoding="utf-8-sig")
    pset = intraday_speed.priority_codes(csv_p)
    assert pset == {"2330", "2317"}

    csv_p.write_text(header + "🟢 優先買進 (STRONG BUY),2454,聯發科,01,PASS,100,100,105,110,120,95,2.0,1.5,1000\n", encoding="utf-8-sig")
    assert intraday_speed.priority_codes(csv_p) == {"2454"}
    assert intraday_speed.priority_codes(tmp_path / "missing.csv") == set()

    clk = [0.0]
    sched = intraday_speed.RadarScheduler(clock=lambda: clk[0])
    all_c = ["2330", "2454", "2603"]
    pri_hits = 0
    norm_ticks = []
    for step in range(13):
        clk[0] = step * 5.0
        due = sched.due_codes(all_c, {"2330"})
        if "2330" in due:
            pri_hits += 1
        if "2454" in due:
            norm_ticks.append(step * 5)
    assert pri_hits == 13
    assert norm_ticks == [0, 30, 60]

    monkeypatch.setattr(line_sender, "load_env", lambda: {})
    monkeypatch.setenv("TW_RADAR_PRIORITY_INTERVAL_SEC", "7")
    monkeypatch.setenv("TW_RADAR_NORMAL_INTERVAL_SEC", "20")
    assert intraday_speed.get_int("TW_RADAR_PRIORITY_INTERVAL_SEC") == 7
    assert intraday_speed.get_int("TW_RADAR_NORMAL_INTERVAL_SEC") == 20

    monkeypatch.setenv("TW_RADAR_PRIORITY_INTERVAL_SEC", "abc")
    assert intraday_speed.get_int("TW_RADAR_PRIORITY_INTERVAL_SEC") == 5
    monkeypatch.setenv("TW_RADAR_PRIORITY_INTERVAL_SEC", "0")
    assert intraday_speed.get_int("TW_RADAR_PRIORITY_INTERVAL_SEC") == 3
    monkeypatch.setenv("TW_RADAR_PRIORITY_INTERVAL_SEC", "999")
    assert intraday_speed.get_int("TW_RADAR_PRIORITY_INTERVAL_SEC") == 60
    monkeypatch.setenv("TW_RADAR_PRIORITY_INTERVAL_SEC", "10")
    monkeypatch.setenv("TW_RADAR_NORMAL_INTERVAL_SEC", "1")
    assert intraday_speed.get_int("TW_RADAR_NORMAL_INTERVAL_SEC") == 10

    monkeypatch.delenv("TW_RADAR_PRIORITY_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("TW_RADAR_NORMAL_INTERVAL_SEC", raising=False)
    assert intraday_speed.worker_sleep_seconds() == 5


def test_push_ledger_cross_process_dedupe(monkeypatch, tmp_path):
    monkeypatch.setenv("TW_INTRADAY_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_FAKE_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "TEST_FAKE_CHAT")
    monkeypatch.delenv("TW_RADAR_STRICT_WINDOW", raising=False)
    monkeypatch.setattr(line_sender, "load_env", lambda: {})
    monkeypatch.setattr(intraday_speed, "is_trading_day", _mock_trading_day)
    monkeypatch.setattr(intraday_speed, "taipei_now", lambda: datetime.datetime(2026, 11, 16, 10, 0, 0, tzinfo=TZ_TW))
    monkeypatch.setattr(market_schedule, "is_intraday_push_suppressed", lambda **kw: (False, ""))
    monkeypatch.setattr(market_schedule, "should_suppress_push", lambda **kw: (False, ""))

    tg_return = [True]
    tg_calls = []
    monkeypatch.setattr(line_sender, "send_telegram", lambda *a, **kw: tg_calls.append((a, kw)) or tg_return[0])

    card_sl = line_sender.build_price_alert_card(
        {"code": "2330", "name": "台積電", "sl_price": 95.0},
        {"price": 94.0, "time": "10:00:00"},
        "STOP_LOSS",
        "停損警訊",
        "測試",
        strategy_id="RADAR",
    )
    assert line_sender.send_to_line(card_sl, target_id="TEST_FAKE") is True
    assert line_sender.send_to_line(card_sl, target_id="TEST_FAKE") is True
    assert len(tg_calls) == 1
    assert len(list((tmp_path / "2026-11-16").glob("*.flag"))) == 1

    card_tp1 = line_sender.build_price_alert_card(
        {"code": "2330", "name": "台積電", "tp1_price": 110.0},
        {"price": 110.0, "time": "10:01:00"},
        "TP1",
        "停利",
        "測試",
        strategy_id="RADAR",
    )
    assert line_sender.send_to_line(card_tp1, target_id="TEST_FAKE") is True
    assert len(tg_calls) == 2

    card_fail = line_sender.build_price_alert_card(
        {"code": "2317", "name": "鴻海", "tp1_price": 110.0},
        {"price": 110.0, "time": "10:02:00"},
        "TP1",
        "停利",
        "測試",
        strategy_id="RADAR",
    )
    tg_return[0] = False
    assert line_sender.send_to_line(card_fail, target_id="TEST_FAKE") is False
    assert not (tmp_path / "2026-11-16" / "2317_TP1.flag").exists()
    tg_return[0] = True
    assert line_sender.send_to_line(card_fail, target_id="TEST_FAKE") is True
    assert len(tg_calls) == 4

    afterhours_msg = "📊【台股盤後量化選股戰報】\n測試內容"
    assert line_sender.send_to_line(afterhours_msg, target_id="TEST_FAKE") is True
    assert line_sender.send_to_line(afterhours_msg, target_id="TEST_FAKE") is True
    assert len(tg_calls) == 6

    monkeypatch.delenv("TW_INTRADAY_LEDGER_DIR", raising=False)
    assert line_sender.send_to_line(card_sl, target_id="TEST_FAKE") is True
    assert line_sender.send_to_line(card_sl, target_id="TEST_FAKE") is True
    assert len(tg_calls) == 8

    sub_code = (
        "import sys; from pathlib import Path; "
        "import intraday_speed; "
        "ok = intraday_speed.claim_push(Path(sys.argv[1]), '2330', 'STOP_LOSS', '2026-11-16'); "
        "print('CLAIMED=' + str(ok))"
    )
    cp = subprocess.run(
        [sys.executable, "-c", sub_code, str(tmp_path)],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert cp.returncode == 0
    assert "CLAIMED=False" in cp.stdout


def test_task_runner_gate_spawn_and_cutoff(monkeypatch, tmp_path):
    monkeypatch.setattr(intraday_speed, "is_trading_day", _mock_trading_day)
    monkeypatch.setattr(line_sender, "load_env", lambda: {})
    monkeypatch.setenv("TW_INTRADAY_LEDGER_DIR", str(tmp_path))
    monkeypatch.delenv("TW_SCANNER_STAGGER_SEC", raising=False)
    monkeypatch.delenv("TW_SCANNER_INTERVAL_SEC", raising=False)

    class _FakeProc:
        def __init__(self, pid):
            self.pid = pid
            self._alive = True

        def poll(self):
            return None if self._alive else 0

    for blocked_dt in [
        datetime.datetime(2026, 11, 16, 8, 57, 59, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 16, 13, 30, 0, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 16, 15, 27, 6, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 16, 21, 15, 0, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 21, 10, 0, 0, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 18, 10, 0, 0, tzinfo=TZ_TW),
        datetime.datetime(2026, 11, 16, 7, 27, 6, tzinfo=TZ_UTC),
    ]:
        spawns = []
        rc = intraday_task_runner.main(
            [],
            now_fn=lambda d=blocked_dt: d,
            sleep_fn=lambda s: None,
            spawn_fn=lambda *a, **kw: spawns.append(a),
            lock_fn=lambda: True,
        )
        assert rc == 0
        assert len(spawns) == 0

    t_b = [datetime.datetime(2026, 11, 16, 8, 58, 0, tzinfo=TZ_TW)]
    spawns_b = []
    sleeps_b = []
    kills_b = []

    def sleep_b(sec):
        sleeps_b.append(sec)
        if len(spawns_b) < 12:
            t_b[0] = t_b[0] + datetime.timedelta(seconds=sec)
        elif t_b[0] < datetime.datetime(2026, 11, 16, 13, 29, 59, tzinfo=TZ_TW):
            t_b[0] = datetime.datetime(2026, 11, 16, 13, 29, 59, tzinfo=TZ_TW)
        else:
            t_b[0] = datetime.datetime(2026, 11, 16, 13, 30, 0, tzinfo=TZ_TW)

    def spawn_b(cmd, cwd=None, env=None):
        p = _FakeProc(1000 + len(spawns_b))
        spawns_b.append((cmd, cwd, env, p.pid))
        return p

    rc_b = intraday_task_runner.main(
        [],
        now_fn=lambda: t_b[0],
        sleep_fn=sleep_b,
        spawn_fn=spawn_b,
        kill_tree_fn=lambda pid: kills_b.append(pid),
        lock_fn=lambda: True,
    )
    assert rc_b == 0
    assert len(spawns_b) == 12
    assert sleeps_b[:11] == [5] * 11
    for cmd, _, env, _ in spawns_b:
        assert cmd[0] == sys.executable
        assert cmd[2:] == ["--interval", "60"]
        assert "--force" not in cmd and "--test-push" not in cmd and "--once" not in cmd
        assert env["TW_RADAR_STRICT_WINDOW"] == "1"
        assert env["TW_INTRADAY_LEDGER_DIR"] == str(tmp_path)
    assert len(kills_b) == 12
    assert set(kills_b) == {p[3] for p in spawns_b}

    t_c = [datetime.datetime(2026, 11, 16, 13, 29, 40, tzinfo=TZ_TW)]
    spawns_c = []
    kills_c = []

    def sleep_c(sec):
        t_c[0] = t_c[0] + datetime.timedelta(seconds=sec)

    rc_c = intraday_task_runner.main(
        [],
        now_fn=lambda: t_c[0],
        sleep_fn=sleep_c,
        spawn_fn=lambda cmd, cwd=None, env=None: spawns_c.append(2000 + len(spawns_c)) or _FakeProc(2000 + len(spawns_c) - 1),
        kill_tree_fn=lambda pid: kills_c.append(pid),
        lock_fn=lambda: True,
    )
    assert rc_c == 0
    assert len(spawns_c) == 4
    assert len(kills_c) == 4

    spawns_d = []
    rc_d = intraday_task_runner.main(
        [],
        now_fn=lambda: datetime.datetime(2026, 11, 16, 8, 58, 0, tzinfo=TZ_TW),
        spawn_fn=lambda *a, **kw: spawns_d.append(a),
        lock_fn=lambda: None,
    )
    assert rc_d == 0
    assert len(spawns_d) == 0


def test_mis_quote_price_source():
    base_keys = {
        "code", "name", "price", "prev_close", "change", "change_pct",
        "open", "high", "low", "volume", "time", "feed_time", "feed_date",
    }
    q1 = mis_quote.parse_mis_quote_item({"c": "2330", "n": "台積電", "z": "100", "y": "99", "d": "20261116"})
    assert q1["price"] == 100.0 and q1["price_source"] == "z"
    assert base_keys.issubset(set(q1.keys()))

    q2 = mis_quote.parse_mis_quote_item({"c": "2330", "n": "台積電", "z": "-", "trade": {"z": "10.5"}, "y": "10", "d": "20261116"})
    assert q2["price"] == 10.5 and q2["price_source"] == "trade_z"

    q3 = mis_quote.parse_mis_quote_item({"c": "2330", "n": "台積電", "z": "-", "y": "99", "d": "20261116"})
    assert q3["price"] == 99.0 and q3["price_source"] == "y"

    q4 = mis_quote.parse_mis_quote_item({"c": "2330", "n": "台積電", "z": "-", "y": "-", "d": "20261116"})
    assert q4["price"] is None and q4["price_source"] is None


def test_worker_static_contract():
    src = (ROOT_DIR / "line_bot_server.py").read_text(encoding="utf-8")
    assert "🔔【台股開盤前夕・盤中到價雷達全自動啟動】" in src
    assert "🏁【今日台股盤中到價雷達監控圓滿結束】" in src

    tree = ast.parse(src)
    worker_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "intraday_radar_worker":
            worker_node = node
            break
    assert worker_node is not None
    worker_src = ast.get_source_segment(src, worker_node)
    assert worker_src is not None
    assert "每 60 秒" not in worker_src

    has_sleep_60 = False
    has_direct_scan = False
    has_to_thread_scan = False
    has_to_thread_allowed = False

    for node in ast.walk(worker_node):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "sleep":
                if isinstance(fn.value, ast.Name) and fn.value.id == "asyncio":
                    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 60:
                        has_sleep_60 = True
            if isinstance(fn, ast.Name) and fn.id == "scan_and_generate_alerts":
                has_direct_scan = True
            if isinstance(fn, ast.Attribute) and fn.attr == "to_thread":
                if node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Name) and arg0.id == "scan_and_generate_alerts":
                        has_to_thread_scan = True
                    if isinstance(arg0, ast.Attribute) and arg0.attr == "is_radar_push_allowed":
                        has_to_thread_allowed = True

    assert not has_sleep_60
    assert not has_direct_scan
    assert has_to_thread_scan
    assert has_to_thread_allowed
