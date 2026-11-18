# -*- coding: utf-8 -*-
import ast
import asyncio
import datetime
import email.message
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import access_guard
import cmoney_sync
import intraday_child_bootstrap
import intraday_speed
import line_bot_server
import line_sender
import market_schedule
import mis_shared_gate
import radar_sse
import run_all_intraday

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))
TZ_UTC = datetime.timezone.utc


def _dt_tw(y, m, d, hh=0, mm=0, ss=0):
    return datetime.datetime(y, m, d, hh, mm, ss, tzinfo=TZ_TW)


@pytest.fixture(autouse=True)
def _isolate_batch2a_env(monkeypatch, tmp_path):
    raw_now = os.environ.get("TW_BATCH2A_TEST_NOW", "").strip()
    if raw_now:
        default_now = datetime.datetime.fromisoformat(raw_now).astimezone(TZ_TW)
    else:
        default_now = _dt_tw(2026, 11, 16, 10, 0, 0)

    ledger_dir = tmp_path / "ledger"
    gate_dir = tmp_path / "mis_gate"
    fake_env_file = tmp_path / "isolated.env"
    fake_env_file.write_text("TELEGRAM_BOT_TOKEN=SENTINEL_TG_TOK\n", encoding="utf-8")

    fake_env_map = {
        "TELEGRAM_BOT_TOKEN": "SENTINEL_TG_TOK",
        "TELEGRAM_CHAT_ID": "SENTINEL_TG_CHAT",
        "LINE_CHANNEL_ACCESS_TOKEN": "SENTINEL_LINE_TOK",
        "LINE_CHANNEL_SECRET": "SENTINEL_LINE_SEC",
        "LINE_USER_ID": "SENTINEL_LINE_UID",
        "CMONEY_ACCOUNT": "SENTINEL_CM_ACC",
        "CMONEY_PASSWORD": "SENTINEL_CM_PW",
        "CMONEY_AUTH_TOKEN": "SENTINEL_CM_TOK",
    }

    monkeypatch.setenv("TW_INTRADAY_LEDGER_DIR", str(ledger_dir))
    monkeypatch.setenv("TW_MIS_SHARED_DIR", str(gate_dir))
    monkeypatch.setattr(intraday_speed, "ledger_dir", lambda: ledger_dir)
    monkeypatch.setattr(mis_shared_gate, "DEFAULT_SHARED_GATE_DIR", gate_dir)
    monkeypatch.setattr(mis_shared_gate, "shared_gate_dir", lambda: gate_dir)

    def _fake_is_trading_day(dt_or_d=None):
        if dt_or_d is None:
            d = intraday_speed.taipei_now().date()
        elif isinstance(dt_or_d, datetime.datetime):
            d = intraday_speed.to_taipei(dt_or_d).date()
        else:
            d = dt_or_d
        if d == datetime.date(2026, 11, 18):
            return False
        return d.weekday() < 5

    monkeypatch.setattr(intraday_speed, "taipei_now", lambda: default_now)
    monkeypatch.setattr(intraday_speed, "is_trading_day", _fake_is_trading_day)
    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: default_now)
    monkeypatch.setattr(market_schedule, "should_suppress_push", lambda *a, **kw: (False, ""))
    monkeypatch.setattr(market_schedule, "is_intraday_push_suppressed", lambda *a, **kw: (False, ""))

    monkeypatch.setattr(line_sender, "ENV_PATH", fake_env_file)
    monkeypatch.setattr(line_sender, "load_env", lambda: dict(fake_env_map))
    monkeypatch.setattr(line_sender, "send_telegram", lambda *a, **kw: False)
    monkeypatch.setattr(line_sender, "send_discord_webhook", lambda *a, **kw: False)

    monkeypatch.setattr(line_bot_server, "ENV_PATH", fake_env_file)
    monkeypatch.setattr(line_bot_server, "load_env", lambda: dict(fake_env_map))
    monkeypatch.setattr(line_bot_server, "ENV", dict(fake_env_map))
    monkeypatch.setattr(line_bot_server, "CHANNEL_ACCESS_TOKEN", "SENTINEL_LINE_TOK")
    monkeypatch.setattr(line_bot_server, "LINE_CHANNEL_SECRET", "SENTINEL_LINE_SEC")

    monkeypatch.setattr(cmoney_sync, "ENV_PATH", fake_env_file)
    monkeypatch.setattr(cmoney_sync, "load_env", lambda: dict(fake_env_map))


class _FakeProc:
    def __init__(self, pid: int, finish_at: float, rc: int = 0, clock_fn=None):
        self.pid = pid
        self._finish_at = finish_at
        self._rc = rc
        self._clock_fn = clock_fn
        self.returncode = None

    def poll(self):
        if self._clock_fn() >= self._finish_at:
            self.returncode = self._rc
            return self._rc
        return None

    def kill(self):
        self.returncode = -9


def test_once_parallel_bounded_concurrency(monkeypatch, tmp_path):
    curr = {"t": 0.0}
    base_dt = _dt_tw(2026, 11, 16, 9, 5, 0)
    now_fn = lambda: base_dt + datetime.timedelta(seconds=curr["t"])

    active_Procs = []
    max_alive = {"v": 0}
    launched_cmds = []
    launched_envs = []
    sleep_args = []

    def _count_alive():
        return sum(1 for p in active_Procs if p.poll() is None)

    def _sleep(sec):
        sleep_args.append(sec)
        curr["t"] += float(sec)
        alive = _count_alive()
        if alive > max_alive["v"]:
            max_alive["v"] = alive

    def _popen(cmd, cwd=None, env=None):
        pid = 1000 + len(launched_cmds)
        p = _FakeProc(pid, curr["t"] + 12.0, rc=0, clock_fn=lambda: curr["t"])
        active_Procs.append(p)
        launched_cmds.append(list(cmd))
        launched_envs.append(dict(env or {}))
        alive = _count_alive()
        if alive > max_alive["v"]:
            max_alive["v"] = alive
        return p

    summary = run_all_intraday.run_once_parallel(
        run_all_intraday.INTRADAY_STRATEGIES,
        now_fn=now_fn,
        popen_fn=_popen,
        sleep_fn=_sleep,
        kill_tree_fn=lambda pid: None,
        concurrency=3,
        stagger=5,
        child_timeout=180,
    )
    assert summary["started"] == 12
    assert summary["succeeded"] == 12
    assert max_alive["v"] <= 3
    assert sleep_args.count(5) >= 11 or sleep_args.count(5.0) >= 11

    for cmd, env in zip(launched_cmds, launched_envs):
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("intraday_child_bootstrap.py")
        assert cmd[2].endswith("intraday_scanner.py")
        assert cmd[3:] == ["--once"]
        assert "--force" not in cmd and "--test-push" not in cmd
        assert env.get("TW_RADAR_STRICT_WINDOW") == "1"
        assert str(tmp_path) in env.get("TW_INTRADAY_LEDGER_DIR", "")
        assert str(tmp_path) in env.get("TW_MIS_SHARED_DIR", "")


def test_once_window_gate_and_delayed_starts(monkeypatch, tmp_path):
    blocked_times = [
        _dt_tw(2026, 11, 16, 8, 57, 59),
        _dt_tw(2026, 11, 16, 13, 30, 0),
        _dt_tw(2026, 11, 16, 13, 31, 0),
        _dt_tw(2026, 11, 16, 15, 27, 6),
        _dt_tw(2026, 11, 16, 21, 15, 0),
        _dt_tw(2026, 11, 21, 10, 0, 0),
        _dt_tw(2026, 11, 18, 10, 0, 0),
        datetime.datetime(2026, 11, 16, 7, 27, 6, tzinfo=TZ_UTC),
    ]
    for bt in blocked_times:
        calls = []
        res = run_all_intraday.run_once_parallel(
            run_all_intraday.INTRADAY_STRATEGIES,
            now_fn=lambda dt=bt: dt,
            popen_fn=lambda *a, **kw: calls.append(1),
            sleep_fn=lambda s: None,
        )
        assert res["started"] == 0 and len(calls) == 0

    for start_base in [
        _dt_tw(2026, 11, 16, 13, 29, 45),
        datetime.datetime(2026, 11, 16, 5, 29, 45, tzinfo=TZ_UTC),
    ]:
        clk = {"t": 0.0}
        launch_secs = []
        killed_pids = []

        def _now(b=start_base, c=clk):
            return b + datetime.timedelta(seconds=c["t"])

        def _sleep(sec, c=clk):
            c["t"] += float(sec)

        def _popen(cmd, cwd=None, env=None, c=clk, ls=launch_secs):
            pid = 2000 + len(ls)
            ls.append(int(round(c["t"])))
            return _FakeProc(pid, 999999.0, rc=0, clock_fn=lambda: c["t"])

        res = run_all_intraday.run_once_parallel(
            run_all_intraday.INTRADAY_STRATEGIES,
            now_fn=_now,
            popen_fn=_popen,
            sleep_fn=_sleep,
            kill_tree_fn=lambda pid, kp=killed_pids: kp.append(pid),
            concurrency=3,
            stagger=5,
            child_timeout=180,
        )
        assert res["started"] == 3
        assert launch_secs == [0, 5, 10]
        assert killed_pids == [2000, 2001, 2002]


def test_once_force_no_bypass_timeout_and_failure_isolation(monkeypatch, tmp_path):
    t_late = _dt_tw(2026, 11, 16, 15, 27, 0)
    monkeypatch.setattr(intraday_speed, "taipei_now", lambda: t_late)
    monkeypatch.setattr(market_schedule, "check_market_open_or_exit", lambda **kw: True)
    monkeypatch.setattr(market_schedule, "is_intraday_window", lambda *a, **kw: (True, "ok"))

    late_launches = []
    monkeypatch.setattr(
        run_all_intraday,
        "run_once_parallel",
        lambda targets: run_all_intraday.run_once_parallel.__wrapped__(targets)
        if hasattr(run_all_intraday.run_once_parallel, "__wrapped__")
        else None,
    )
    # Restore real run_once_parallel and intercept Popen
    import importlib
    importlib.reload(run_all_intraday)
    monkeypatch.setattr(intraday_speed, "taipei_now", lambda: t_late)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: late_launches.append(1))
    monkeypatch.setattr(sys, "argv", ["run_all_intraday.py", "--once", "--force"])
    run_all_intraday.main()
    assert len(late_launches) == 0

    clk = {"t": 0.0}
    base_10 = _dt_tw(2026, 11, 16, 10, 0, 0)
    idx_counter = {"n": 0}
    killed = []

    def _popen_mix(cmd, cwd=None, env=None):
        idx = idx_counter["n"]
        idx_counter["n"] += 1
        pid = 3000 + idx
        if idx == 0:
            return _FakeProc(pid, clk["t"] + 999.0, rc=0, clock_fn=lambda: clk["t"])
        if idx == 1:
            return _FakeProc(pid, clk["t"] + 2.0, rc=1, clock_fn=lambda: clk["t"])
        if idx == 2:
            raise OSError("spawn failed")
        return _FakeProc(pid, clk["t"] + 2.0, rc=0, clock_fn=lambda: clk["t"])

    summary = run_all_intraday.run_once_parallel(
        run_all_intraday.INTRADAY_STRATEGIES,
        now_fn=lambda: base_10 + datetime.timedelta(seconds=clk["t"]),
        popen_fn=_popen_mix,
        sleep_fn=lambda s: clk.__setitem__("t", clk["t"] + float(s)),
        kill_tree_fn=lambda pid: killed.append(pid),
        concurrency=3,
        stagger=1,
        child_timeout=30,
    )
    assert summary["succeeded"] == 9
    assert summary["failed"] == 2
    assert summary["timed_out"] == 1
    assert killed == [3000]


def test_web_trigger_once_gate_and_single_flight(monkeypatch, tmp_path):
    called = {"n": 0}
    ok_late, msg_late = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 15, 27, 0),
        runner=lambda: called.__setitem__("n", called["n"] + 1),
    )
    assert ok_late is False and "非盤中時段" in msg_late and called["n"] == 0

    evt_start = threading.Event()
    evt_release = threading.Event()

    def _slow_runner():
        called["n"] += 1
        evt_start.set()
        evt_release.wait(timeout=5.0)

    ok1, _ = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        runner=_slow_runner,
    )
    assert ok1 is True
    assert evt_start.wait(timeout=2.0)

    ok2, msg2 = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        runner=_slow_runner,
    )
    assert ok2 is False and "仍在執行" in msg2

    evt_release.set()
    deadline = time.monotonic() + 2.0
    while run_all_intraday._WEB_TRIGGER_LOCK.locked() and time.monotonic() < deadline:
        time.sleep(0.02)

    done_err = threading.Event()

    def _err_runner():
        try:
            raise RuntimeError("boom")
        finally:
            done_err.set()

    ok3, _ = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        runner=_err_runner,
    )
    assert ok3 is True
    assert done_err.wait(timeout=2.0)
    deadline = time.monotonic() + 2.0
    while run_all_intraday._WEB_TRIGGER_LOCK.locked() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not run_all_intraday._WEB_TRIGGER_LOCK.locked()

    src = (ROOT_DIR / "line_bot_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found_web_trigger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "web_trigger_once":
                found_web_trigger = True
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "web_trigger_once":
                found_web_trigger = True
    assert found_web_trigger is True
    assert 'run_all_intraday.py"), "--once"' not in src


def test_shared_gate_cross_process_limit_and_backoff(monkeypatch, tmp_path):
    gdir = tmp_path / "gate_cross"
    clk = {"t": 1000.0}
    gate = mis_shared_gate.SharedMisGate(
        gdir, max_requests=2, window_sec=5, backoff_base=10, backoff_max=120, clock=lambda: clk["t"]
    )
    assert gate.acquire() is True
    assert gate.acquire() is True
    assert gate.acquire() is False
    assert gate.acquire() is False

    code_snippet = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import mis_shared_gate; "
        "g = mis_shared_gate.SharedMisGate(sys.argv[2], max_requests=2, window_sec=5, clock=lambda: float(sys.argv[3])); "
        "print('ACQ=' + str(g.acquire()))"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code_snippet, str(ROOT_DIR), str(gdir), "1001.0"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert "ACQ=False" in proc.stdout

    clk["t"] = 1006.0
    assert gate.acquire() is True

    d1 = gate.report_failure()
    assert d1 == 10.0
    proc2 = subprocess.run(
        [sys.executable, "-B", "-c", code_snippet, str(ROOT_DIR), str(gdir), "1010.0"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert "ACQ=False" in proc2.stdout

    assert gate.report_failure() == 20.0
    assert gate.report_failure() == 40.0
    assert gate.report_failure() == 80.0
    assert gate.report_failure() == 120.0
    assert gate.report_failure() == 120.0

    gate.report_success()
    clk["t"] = 1200.0
    assert gate.acquire() is True

    (gdir / "state.json").write_text("{corrupt_json", encoding="utf-8")
    assert gate.acquire() is True


def test_urlopen_patch_cache_and_passthrough(monkeypatch, tmp_path):
    monkeypatch.setenv("TW_MIS_SHARED_CACHE_SEC", "3")
    monkeypatch.setenv("TW_MIS_SHARED_MAX_WAIT_SEC", "0")
    clk = {"t": 2000.0}
    gdir = tmp_path / "gate_cache"
    gate = mis_shared_gate.SharedMisGate(gdir, max_requests=5, window_sec=5, clock=lambda: clk["t"])

    calls = {"mis": 0, "other": 0}

    def _fake_opener(url_or_req, *a, **kw):
        u = url_or_req.full_url if hasattr(url_or_req, "full_url") else str(url_or_req)
        if "mis.twse.com.tw" in u:
            calls["mis"] += 1
            payload = {
                "msgArray": [
                    {"ex": "tse", "ch": "2330.tw", "z": "1000"},
                    {"ex": "otc", "ch": "2330.tw", "z": "1000"},
                ],
                "rtcode": "0000",
                "rtmessage": "OK",
            }
            return mis_shared_gate._BytesResponse(json.dumps(payload).encode("utf-8"), 200)
        calls["other"] += 1
        return mis_shared_gate._BytesResponse(b"PASSTHROUGH", 200)

    orig_u = urllib.request.urlopen
    restore = mis_shared_gate.install_urlopen_patch(
        gate, opener=_fake_opener, sleep=lambda s: clk.__setitem__("t", clk["t"] + s), now=lambda: clk["t"]
    )
    try:
        url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw|otc_2330.tw&json=1"
        r1 = json.loads(urllib.request.urlopen(url).read().decode("utf-8"))
        clk["t"] += 1.0
        r2 = json.loads(urllib.request.urlopen(url).read().decode("utf-8"))
        assert calls["mis"] == 1
        assert r1["msgArray"] == r2["msgArray"]

        clk["t"] += 3.5
        urllib.request.urlopen(url).read()
        assert calls["mis"] == 2

        other_res = urllib.request.urlopen("https://example.invalid/x").read()
        assert other_res == b"PASSTHROUGH" and calls["other"] == 1

        gate_tight = mis_shared_gate.SharedMisGate(tmp_path / "tight", max_requests=1, window_sec=10, clock=lambda: clk["t"])
        assert gate_tight.acquire() is True
    finally:
        restore()
    assert urllib.request.urlopen is orig_u

    restore2 = mis_shared_gate.install_urlopen_patch(
        gate_tight, opener=_fake_opener, sleep=lambda s: None, now=lambda: clk["t"]
    )
    try:
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen("https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2317.tw")
        assert calls["mis"] == 2
    finally:
        restore2()
    assert urllib.request.urlopen is orig_u


def test_shared_gate_no_more_429(monkeypatch, tmp_path):
    monkeypatch.setenv("TW_MIS_SHARED_MAX_WAIT_SEC", "0")
    clk = {"t": 3000.0}
    gate = mis_shared_gate.SharedMisGate(
        tmp_path / "gate_429", max_requests=5, window_sec=5, backoff_base=10, backoff_max=120, clock=lambda: clk["t"]
    )
    calls = {"n": 0}

    def _opener_429(url_or_req, *a, **kw):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp", 429, "Too Many Requests", email.message.Message(), io.BytesIO(b"")
        )

    restore = mis_shared_gate.install_urlopen_patch(gate, opener=_opener_429, sleep=lambda s: None, now=lambda: clk["t"])
    try:
        for i in range(12):
            with pytest.raises(urllib.error.HTTPError if i == 0 else urllib.error.URLError):
                urllib.request.urlopen(f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{2300+i}.tw")
        assert calls["n"] == 1
    finally:
        restore()

    for exc_to_raise in [socket.timeout("timed out"), urllib.error.URLError("net down")]:
        g2 = mis_shared_gate.SharedMisGate(tmp_path / f"g_{type(exc_to_raise).__name__}", max_requests=5, clock=lambda: clk["t"])
        r2 = mis_shared_gate.install_urlopen_patch(
            g2, opener=lambda *a, exc=exc_to_raise, **kw: (_ for _ in ()).throw(exc), sleep=lambda s: None, now=lambda: clk["t"]
        )
        try:
            with pytest.raises(Exception):
                urllib.request.urlopen("https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_1101.tw")
            assert g2.acquire() is False
        finally:
            r2()


def test_child_bootstrap_rules(monkeypatch, tmp_path):
    strat_root = tmp_path / "strategies"
    s_dir = strat_root / "xx"
    s_dir.mkdir(parents=True)
    out_file = tmp_path / "boot_out.txt"
    good_script = s_dir / "intraday_scanner.py"
    good_script.write_text(
        "import json, sys, urllib.request\n"
        "from pathlib import Path\n"
        "if '--raise-err' in sys.argv:\n"
        "    raise RuntimeError('scanner fail')\n"
        "resp = urllib.request.urlopen('https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw')\n"
        "data = json.loads(resp.read().decode('utf-8'))\n"
        f"Path({str(out_file)!r}).write_text(data['rtcode'], encoding='utf-8')\n",
        encoding="utf-8",
    )

    opener_calls = {"n": 0}

    def _opener(url_or_req, *a, **kw):
        opener_calls["n"] += 1
        return mis_shared_gate._BytesResponse(b'{"msgArray":[],"rtcode":"0000","rtmessage":"OK"}', 200)

    orig_u = urllib.request.urlopen
    gate = mis_shared_gate.SharedMisGate(tmp_path / "boot_gate", max_requests=5)
    rc0 = intraday_child_bootstrap.main(
        [str(good_script), "--once"], allowed_root=strat_root, gate=gate, opener=_opener
    )
    assert rc0 == 0 and opener_calls["n"] == 1 and out_file.read_text(encoding="utf-8") == "0000"
    assert urllib.request.urlopen is orig_u

    outside_script = tmp_path / "intraday_scanner.py"
    outside_script.write_text("raise SystemExit(0)\n", encoding="utf-8")
    assert intraday_child_bootstrap.main([str(outside_script)], allowed_root=strat_root, gate=gate, opener=_opener) == 2
    assert urllib.request.urlopen is orig_u

    wrong_name = s_dir / "other.py"
    wrong_name.write_text("raise SystemExit(0)\n", encoding="utf-8")
    assert intraday_child_bootstrap.main([str(wrong_name)], allowed_root=strat_root, gate=gate, opener=_opener) == 2
    assert urllib.request.urlopen is orig_u

    for bad_flag in ("--force", "--test-push", "--force-test"):
        out_file.unlink(missing_ok=True)
        rc_f = intraday_child_bootstrap.main(
            [str(good_script), bad_flag], allowed_root=strat_root, gate=gate, opener=_opener
        )
        assert rc_f == 2 and not out_file.exists()
        assert urllib.request.urlopen is orig_u

    rc_err = intraday_child_bootstrap.main(
        [str(good_script), "--raise-err"], allowed_root=strat_root, gate=gate, opener=_opener
    )
    assert rc_err == 1
    assert urllib.request.urlopen is orig_u


def test_sse_emits_settled_ledger_events_in_window(monkeypatch, tmp_path):
    ldir = tmp_path / "sse_ledger"
    day_dir = ldir / "2026-11-16"
    day_dir.mkdir(parents=True)
    (day_dir / "2317_TP1.flag").write_text("1", encoding="utf-8")

    mono_t = {"v": 0.0}
    step = {"n": 0}

    def _sleep(sec):
        step["n"] += 1
        if step["n"] == 1:
            (day_dir / "2330_STOP_LOSS.flag").write_text("1", encoding="utf-8")
            (day_dir / "2454_TP2.flag").write_text("1", encoding="utf-8")
            (day_dir / "ABC_X.flag").write_text("1", encoding="utf-8")
            (day_dir / "2330_bad-name.flag").write_text("1", encoding="utf-8")
            (day_dir / "6488_LEVEL_1_SURGE.flag").write_text("1", encoding="utf-8")
            mono_t["v"] += 3.0
        elif step["n"] == 2:
            (day_dir / "2454_TP2.flag").unlink(missing_ok=True)
            mono_t["v"] += 10.0
        else:
            mono_t["v"] += 15.0

    hub = radar_sse.RadarSseHub(
        ledger_dir_fn=lambda: ldir,
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0) + datetime.timedelta(seconds=mono_t["v"]),
        window_fn=lambda now: True,
        poll_sec=2,
        settle_sec=12,
        heartbeat_sec=15,
    )
    assert hub.try_register() is True

    async def _collect():
        chunks = []
        async for ch in hub.stream(lambda: step["n"] >= 3, sleep=_sleep, mono=lambda: mono_t["v"]):
            chunks.append(ch)
        return "".join(chunks)

    full_out = asyncio.run(_collect())
    assert full_out.startswith("retry: 5000\n\n")
    assert ": ping\n\n" in full_out
    assert "2317" not in full_out
    assert "2454" not in full_out
    assert "ABC" not in full_out
    assert "bad-name" not in full_out

    alert_lines = [
        ln[len("data: "):]
        for block in full_out.split("\n\n")
        if "event: radar_alert" in block
        for ln in block.splitlines()
        if ln.startswith("data: ")
    ]
    assert len(alert_lines) == 2
    objs = [json.loads(x) for x in alert_lines]
    for obj in objs:
        assert set(obj.keys()) == {"code", "event", "ts"}
    by_code = {o["code"]: o["event"] for o in objs}
    assert by_code == {"2330": "STOP_LOSS", "6488": "LEVEL_1_SURGE"}

    for forbidden in ("token", "chat_id", "user", ".env", "SENTINEL", str(tmp_path)):
        assert forbidden not in full_out


def test_sse_silent_outside_window_and_delayed(monkeypatch, tmp_path):
    closed_moments = [
        _dt_tw(2026, 11, 16, 8, 59, 59),
        _dt_tw(2026, 11, 16, 13, 30, 0),
        _dt_tw(2026, 11, 16, 15, 27, 6),
        _dt_tw(2026, 11, 16, 21, 15, 0),
        _dt_tw(2026, 11, 21, 10, 0, 0),
        _dt_tw(2026, 11, 18, 10, 0, 0),
        datetime.datetime(2026, 11, 16, 5, 30, 0, tzinfo=TZ_UTC),
    ]
    for idx, dt_val in enumerate(closed_moments):
        ldir = tmp_path / f"closed_{idx}"
        mono_t = {"v": 0.0}
        st = {"n": 0}
        day_s = intraday_speed.to_taipei(dt_val).strftime("%Y-%m-%d")
        (ldir / day_s).mkdir(parents=True, exist_ok=True)

        def _slp(_s, d=ldir / day_s, m=mono_t, k=st):
            k["n"] += 1
            if k["n"] == 1:
                (d / "2330_ENTRY.flag").write_text("1", encoding="utf-8")
            m["v"] += 15.0

        hub = radar_sse.RadarSseHub(
            ledger_dir_fn=lambda d=ldir: d,
            now_fn=lambda dt=dt_val: dt,
            window_fn=lambda now: intraday_speed.is_radar_push_allowed(now)[0],
            poll_sec=2,
            settle_sec=12,
        )
        hub.try_register()

        async def _run_one(h=hub, k=st, s=_slp, m=mono_t):
            out = []
            async for ch in h.stream(lambda: k["n"] >= 2, sleep=s, mono=lambda: m["v"]):
                out.append(ch)
            return "".join(out)

        txt = asyncio.run(_run_one())
        assert "event: radar_alert" not in txt
        assert '{"window":"closed"}' in txt

    # Boundary 13:29:58 -> 13:30:20 and rollover 11-16 -> 11-17
    b_dir = tmp_path / "boundary"
    (b_dir / "2026-11-16").mkdir(parents=True)
    (b_dir / "2026-11-17").mkdir(parents=True)
    (b_dir / "2026-11-17" / "9999_ENTRY.flag").write_text("1", encoding="utf-8")

    b_mono = {"v": 0.0}
    b_dt = {"v": _dt_tw(2026, 11, 16, 13, 29, 58)}
    b_step = {"n": 0}

    def _b_sleep(_s):
        b_step["n"] += 1
        if b_step["n"] == 1:
            (b_dir / "2026-11-16" / "2330_TP1.flag").write_text("1", encoding="utf-8")
        elif b_step["n"] == 2:
            b_mono["v"] = 4.0
            b_dt["v"] = _dt_tw(2026, 11, 16, 13, 30, 2)
            (b_dir / "2026-11-16" / "2317_TP1.flag").write_text("1", encoding="utf-8")
        elif b_step["n"] == 3:
            b_mono["v"] = 15.0
            b_dt["v"] = _dt_tw(2026, 11, 16, 13, 30, 15)
        elif b_step["n"] == 4:
            b_mono["v"] = 30.0
            b_dt["v"] = _dt_tw(2026, 11, 17, 9, 5, 0)

    b_hub = radar_sse.RadarSseHub(
        ledger_dir_fn=lambda: b_dir,
        now_fn=lambda: b_dt["v"],
        window_fn=lambda now: intraday_speed.is_radar_push_allowed(now)[0],
        poll_sec=2,
        settle_sec=12,
    )
    b_hub.try_register()

    async def _run_b():
        out = []
        async for ch in b_hub.stream(lambda: b_step["n"] >= 4, sleep=_b_sleep, mono=lambda: b_mono["v"]):
            out.append(ch)
        return "".join(out)

    b_txt = asyncio.run(_run_b())
    assert '"code":"2330"' in b_txt
    assert '"code":"2317"' not in b_txt
    assert '"code":"9999"' not in b_txt
    assert '{"window":"closed"}' in b_txt


def test_sse_capacity_external_cap_and_lifetime(monkeypatch, tmp_path):
    hub = radar_sse.RadarSseHub(
        ledger_dir_fn=lambda: tmp_path,
        max_clients=3,
        max_external=1,
        poll_sec=2,
        max_lifetime_sec=60,
    )
    assert hub.try_register(external=True) is True
    assert hub.try_register(external=True) is False
    assert hub.try_register(external=False) is True
    assert hub.try_register(external=False) is True
    assert hub.try_register(external=False) is False

    hub.unregister(external=True)
    assert hub.try_register(external=True) is True
    hub.unregister(external=True)
    hub.unregister(external=False)
    hub.unregister(external=False)
    assert hub.active_clients == 0 and hub.active_external == 0

    hub_zero = radar_sse.RadarSseHub(ledger_dir_fn=lambda: tmp_path, max_clients=3, max_external=0)
    assert hub_zero.try_register(external=True) is False

    # Lifetime expiry returns counts
    m_clk = {"v": 0.0}
    assert hub.try_register(external=True) is True

    async def _run_life():
        async for _ in hub.stream(
            lambda: False,
            external=True,
            sleep=lambda s: m_clk.__setitem__("v", m_clk["v"] + 70.0),
            mono=lambda: m_clk["v"],
        ):
            pass

    asyncio.run(_run_life())
    assert hub.active_clients == 0 and hub.active_external == 0

    # Exception during stream returns counts
    assert hub.try_register(external=True) is True

    async def _run_exc():
        async for _ in hub.stream(
            lambda: False,
            external=True,
            sleep=lambda s: (_ for _ in ()).throw(RuntimeError("stream boom")),
            mono=lambda: 0.0,
        ):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(_run_exc())
    assert hub.active_clients == 0 and hub.active_external == 0


def test_sse_endpoint_security_regression(monkeypatch, tmp_path):
    class _FiniteHub:
        def __init__(self, busy=False):
            self.busy = busy

        def try_register(self, external=False):
            return not self.busy

        async def stream(self, is_disconnected, *, external=False):
            yield "retry: 5000\n\n"
            yield 'event: status\ndata: {"window":"open"}\n\n'
            yield ": ping\n\n"

    monkeypatch.setattr(line_bot_server, "_get_radar_sse_hub", lambda: _FiniteHub(busy=False))
    client = TestClient(line_bot_server.app)
    r_loc = client.get("/api/radar/stream")
    assert r_loc.status_code == 200
    assert r_loc.headers.get("content-type", "").startswith("text/event-stream")
    assert "SENTINEL" not in r_loc.text

    ext_client = TestClient(line_bot_server.app, base_url="http://stock.chihyuan.date")
    ext_hdrs = {"CF-Connecting-IP": "203.0.113.5"}
    r_ext = ext_client.get("/api/radar/stream", headers=ext_hdrs)
    assert r_ext.status_code == 200
    assert r_ext.headers.get("content-type", "").startswith("text/event-stream")
    for ln in [x for x in r_ext.text.splitlines() if x.strip()]:
        assert ln.startswith(("retry:", "event:", "data:", ": ping", "id:"))

    monkeypatch.setattr(line_bot_server, "_get_radar_sse_hub", lambda: _FiniteHub(busy=True))
    r_busy = client.get("/api/radar/stream")
    assert r_busy.status_code == 503
    assert r_busy.json() == {"status": "busy"}

    r_logs = ext_client.get("/logs", headers=ext_hdrs)
    assert r_logs.status_code == 403
    assert r_logs.json() == {"status": "forbidden", "message": "外網唯讀：此操作僅限本機執行"}

    trig_calls = []
    monkeypatch.setattr(run_all_intraday, "web_trigger_once", lambda **kw: trig_calls.append(1) or (True, "ok"))
    r_act = ext_client.post("/api/action/run_radar", headers=ext_hdrs)
    assert r_act.status_code == 403 and len(trig_calls) == 0

    assert access_guard.LOCAL_ONLY_GET_PATHS == {"/logs"}
    src = (ROOT_DIR / "line_bot_server.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in src
    assert '@app.get("/api/radar/stream")' in src
    assert "status_code=503" in src
    stream_fn_part = src.split('@app.get("/api/radar/stream")', 1)[1].split("@app.", 1)[0]
    assert "query_params" not in stream_fn_part


def test_index_html_sse_contract():
    html = (ROOT_DIR / "web" / "index.html").read_text(encoding="utf-8")
    assert "new EventSource('/api/radar/stream')" in html
    assert 'id="radarSseStatus"' in html
    assert 'id="radarSseLast"' in html
    assert "textContent" in html
    assert "document.hidden" in html
    assert "60" in html and "20" in html

    assert "}, 20000);" in html
    poll_segment = html.split("}, 20000);", 1)[0][-300:]
    assert "loadRadarWatchlist()" in poll_segment

    sse_segment = html.split("let radarEventSource = null;", 1)[1].split("function renderRadarCards", 1)[0]
    assert "innerHTML" not in sse_segment

    assert "08_group_window_dressing" in html
    assert "subbtn-09" in html
    assert "09_treasury_stock_buyback" in html


def test_push_guards_matrix_and_no_repo_writes(monkeypatch, tmp_path):
    def _snapshot_repo():
        snap = {}
        patterns = [
            *((ROOT_DIR / "cache").rglob("*") if (ROOT_DIR / "cache").exists() else []),
            *ROOT_DIR.glob("strategies/*/result.*"),
            *ROOT_DIR.glob("lab/data/*.csv"),
        ]
        for p in patterns:
            if p.is_file():
                rel = str(p.relative_to(ROOT_DIR))
                st = p.stat()
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                snap[rel] = (h, st.st_mtime_ns)
        return snap

    before_snap = _snapshot_repo()

    strat_dir = tmp_path / "strategies" / "01_test"
    strat_dir.mkdir(parents=True)
    scanner_py = strat_dir / "intraday_scanner.py"
    scanner_py.write_text(
        "import line_sender\n"
        "msg = {'_stock_code': '2330', '_alert_type': 'TP1', '_clean_text': '【盤中雷達・到價警訊】2330', 'altText': '【盤中雷達・到價警訊】2330'}\n"
        "line_sender.send_to_line(msg, task_type='盤中到價')\n",
        encoding="utf-8",
    )

    tg_calls = []
    monkeypatch.setattr(line_sender, "send_telegram", lambda *a, **kw: tg_calls.append(1) or True)
    monkeypatch.setattr(line_sender, "send_discord_webhook", lambda *a, **kw: False)
    monkeypatch.setenv("TW_RADAR_STRICT_WINDOW", "1")

    cases = [
        (_dt_tw(2026, 11, 16, 10, 0, 0), False, 1),
        (_dt_tw(2026, 11, 16, 21, 15, 0), True, 0),
        (_dt_tw(2026, 11, 18, 10, 0, 0), True, 0),
    ]
    for dt_val, sup_bool, exp_tg in cases:
        tg_calls.clear()
        case_ledger = tmp_path / f"ledger_{dt_val.strftime('%Y%m%d_%H%M')}"
        monkeypatch.setenv("TW_INTRADAY_LEDGER_DIR", str(case_ledger))
        monkeypatch.setattr(intraday_speed, "ledger_dir", lambda d=case_ledger: d)
        monkeypatch.setattr(intraday_speed, "taipei_now", lambda dt=dt_val: dt)
        monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda dt=dt_val: dt)
        monkeypatch.setattr(market_schedule, "should_suppress_push", lambda *a, b=sup_bool, **kw: (b, "攔截" if b else ""))
        monkeypatch.setattr(market_schedule, "is_intraday_push_suppressed", lambda *a, b=sup_bool, **kw: (b, "攔截" if b else ""))

        if exp_tg == 1:
            hub = radar_sse.RadarSseHub(
                ledger_dir_fn=lambda d=case_ledger: d,
                now_fn=lambda dt=dt_val: dt,
                window_fn=lambda now: True,
                poll_sec=1,
                settle_sec=0,
            )
            hub.try_register()
            ticks = {"n": 0}

            def _slp(_s):
                ticks["n"] += 1
                if ticks["n"] == 1:
                    rc_in = intraday_child_bootstrap.main([str(scanner_py)], allowed_root=tmp_path / "strategies")
                    assert rc_in == 0

            async def _collect_sse():
                chunks = []
                async for ch in hub.stream(lambda: ticks["n"] >= 1, sleep=_slp, mono=lambda: float(ticks["n"])):
                    chunks.append(ch)
                return "".join(chunks)

            sse_out = asyncio.run(_collect_sse())
            assert '"code":"2330"' in sse_out and '"event":"TP1"' in sse_out
        else:
            rc = intraday_child_bootstrap.main([str(scanner_py)], allowed_root=tmp_path / "strategies")
            assert rc == 0

        assert len(tg_calls) == exp_tg

        day_folder = case_ledger / dt_val.strftime("%Y-%m-%d")
        flags = list(day_folder.glob("*.flag")) if day_folder.exists() else []
        if exp_tg == 1:
            assert len(flags) == 1 and flags[0].name == "2330_TP1.flag"
        else:
            assert len(flags) == 0
            r_once = run_all_intraday.run_once_parallel(
                run_all_intraday.INTRADAY_STRATEGIES,
                now_fn=lambda dt=dt_val: dt,
                popen_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not start")),
                sleep_fn=lambda s: None,
            )
            assert r_once["started"] == 0

    after_snap = _snapshot_repo()
    assert before_snap == after_snap
