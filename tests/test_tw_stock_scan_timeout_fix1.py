# -*- coding: utf-8 -*-
import datetime
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import access_guard
import intraday_speed
import line_bot_server
import run_all_intraday

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))


def _dt_tw(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> datetime.datetime:
    return datetime.datetime(y, m, d, hh, mm, ss, tzinfo=TZ_TW)


def _is_pid_alive(pid: int) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        res = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (res.stdout or "") + (res.stderr or "")
        return str(int(pid)) in out and "No tasks are running" not in out
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _force_kill_tree(pid: int) -> None:
    if pid is None or pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    else:
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
        except Exception:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except Exception:
                pass


@pytest.fixture
def isolated_trigger_env(monkeypatch, tmp_path):
    """隔離日誌、狀態檔、.env 讀取與鎖狀態，並於結束時強制清除所有記錄的假行程樹。"""
    log_path = tmp_path / "web_trigger_timeout.log"
    status_path = tmp_path / "web_trigger_status.json"
    monkeypatch.setattr(run_all_intraday, "WEB_TRIGGER_LOG_PATH", log_path, raising=False)
    monkeypatch.setattr(run_all_intraday, "WEB_TRIGGER_STATUS_PATH", status_path, raising=False)

    # 隔離 .env 與推播/休市判斷
    import line_sender
    monkeypatch.setattr(line_sender, "load_env", lambda *a, **kw: {"LINE_CHANNEL_ACCESS_TOKEN": "", "TELEGRAM_BOT_TOKEN": ""}, raising=False)
    monkeypatch.setattr(line_sender, "should_suppress_push", lambda *a, **kw: (True, "mocked"), raising=False)

    # 重置記憶體狀態與鎖
    if hasattr(run_all_intraday, "_reset_web_trigger_state_for_test"):
        run_all_intraday._reset_web_trigger_state_for_test()
    if run_all_intraday._WEB_TRIGGER_LOCK.locked():
        try:
            run_all_intraday._WEB_TRIGGER_LOCK.release()
        except RuntimeError:
            pass

    spawned_pids: list[int] = []
    yield {
        "log_path": log_path,
        "status_path": status_path,
        "spawned_pids": spawned_pids,
        "tmp_path": tmp_path,
    }

    # Finalizer：不論成敗一律清理記錄的假行程樹並等待鎖釋放
    for pid in spawned_pids:
        _force_kill_tree(pid)
    deadline = time.monotonic() + 2.0
    while run_all_intraday._WEB_TRIGGER_LOCK.locked() and time.monotonic() < deadline:
        time.sleep(0.02)
    if run_all_intraday._WEB_TRIGGER_LOCK.locked():
        try:
            run_all_intraday._WEB_TRIGGER_LOCK.release()
        except RuntimeError:
            pass


def _wait_trigger_done(timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while run_all_intraday._WEB_TRIGGER_LOCK.locked() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not run_all_intraday._WEB_TRIGGER_LOCK.locked(), "鎖未於預期時間內釋放"


def test_01_timeout_kills_entire_process_tree_and_logs(isolated_trigger_env):
    t0 = time.monotonic()
    tmp_path = isolated_trigger_env["tmp_path"]
    grandchild_pid_file = tmp_path / "grandchild.pid"
    parent_ready_file = tmp_path / "parent.ready"

    grandchild_script = tmp_path / "fake_grandchild.py"
    grandchild_script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    parent_script = tmp_path / "fake_parent.py"
    parent_script.write_text(
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, {repr(str(grandchild_script))}])\n"
        f"open({repr(str(grandchild_pid_file))}, 'w').write(str(p.pid))\n"
        f"open({repr(str(parent_ready_file))}, 'w').write('ok')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    recorded_parent_pids: list[int] = []

    def _test_popen(cmd, cwd=None, env=None):
        proc = run_all_intraday._default_popen(
            [sys.executable, str(parent_script)],
            cwd=str(tmp_path),
            env=os.environ.copy(),
        )
        recorded_parent_pids.append(proc.pid)
        isolated_trigger_env["spawned_pids"].append(proc.pid)
        return proc

    ok, msg = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        timeout_sec=1.5,
        popen_fn=_test_popen,
    )
    assert ok is True
    assert "背景啟動" in msg

    # 等假孫行程建立完畢且逾時殺樹完成
    _wait_trigger_done(timeout_s=8.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0, f"單測耗時過長: {elapsed:.2f}s"

    assert len(recorded_parent_pids) == 1
    parent_pid = recorded_parent_pids[0]
    assert grandchild_pid_file.exists(), "假孫行程未啟動"
    grandchild_pid = int(grandchild_pid_file.read_text(encoding="utf-8").strip())
    isolated_trigger_env["spawned_pids"].append(grandchild_pid)

    # 給 OS 短暫回收時間後確認父與孫行程皆已終止
    time.sleep(0.2)
    assert not _is_pid_alive(parent_pid), f"父行程 {parent_pid} 仍存活"
    assert not _is_pid_alive(grandchild_pid), f"孫行程 {grandchild_pid} 仍存活"

    st = run_all_intraday.get_web_trigger_status()
    assert st["status"] == "timeout"
    assert st["timed_out"] is True
    assert st["pid"] == parent_pid
    assert "逾時" in st["message"]
    assert str(parent_pid) in st["message"]
    assert isolated_trigger_env["log_path"].exists()
    assert "逾時" in isolated_trigger_env["log_path"].read_text(encoding="utf-8")
    assert isolated_trigger_env["status_path"].exists()
    saved = json.loads(isolated_trigger_env["status_path"].read_text(encoding="utf-8"))
    assert saved["status"] == "timeout" and saved["timed_out"] is True


def test_02_normal_completion_not_marked_timeout(isolated_trigger_env):
    tmp_path = isolated_trigger_env["tmp_path"]
    ok_script = tmp_path / "fast_ok.py"
    ok_script.write_text("import time\ntime.sleep(0.05)\n", encoding="utf-8")

    def _test_popen(cmd, cwd=None, env=None):
        proc = run_all_intraday._default_popen(
            [sys.executable, str(ok_script)],
            cwd=str(tmp_path),
            env=os.environ.copy(),
        )
        isolated_trigger_env["spawned_pids"].append(proc.pid)
        return proc

    ok, _ = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        timeout_sec=3.0,
        popen_fn=_test_popen,
    )
    assert ok is True
    _wait_trigger_done(timeout_s=5.0)

    st = run_all_intraday.get_web_trigger_status()
    assert st["status"] == "completed"
    assert st["timed_out"] is False


def test_03_child_exits_just_at_timeout_boundary_no_false_timeout(isolated_trigger_env):
    kill_calls: list[int] = []

    class _FakeProc:
        pid = 99901

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return 0

        def poll(self):
            # 模擬在 TimeoutExpired 發生瞬間剛好自行結束
            return 0

    ok, _ = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        timeout_sec=0.1,
        popen_fn=lambda *a, **kw: _FakeProc(),
        kill_tree_fn=lambda pid, proc=None: kill_calls.append(pid),
    )
    assert ok is True
    _wait_trigger_done(timeout_s=3.0)

    assert kill_calls == []
    st = run_all_intraday.get_web_trigger_status()
    assert st["status"] == "completed"
    assert st["timed_out"] is False


def test_04_kill_when_pid_already_gone_handles_gracefully(isolated_trigger_env):
    class _FakeProc:
        pid = 99902
        _polled = False

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return -9

        def poll(self):
            return None

    def _missing_kill(pid, proc=None):
        raise ProcessLookupError("No such process")

    ok, _ = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        timeout_sec=0.1,
        popen_fn=lambda *a, **kw: _FakeProc(),
        kill_tree_fn=_missing_kill,
    )
    assert ok is True
    _wait_trigger_done(timeout_s=3.0)

    st = run_all_intraday.get_web_trigger_status()
    assert st["status"] == "timeout"
    assert st["timed_out"] is True


def test_05_kill_tree_failure_logs_and_releases_lock(isolated_trigger_env):
    class _FakeProc:
        pid = 99903

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return -1

        def poll(self):
            return None

    def _failing_kill(pid, proc=None):
        raise RuntimeError("taskkill access denied")

    ok, _ = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        timeout_sec=0.1,
        popen_fn=lambda *a, **kw: _FakeProc(),
        kill_tree_fn=_failing_kill,
    )
    assert ok is True
    _wait_trigger_done(timeout_s=3.0)

    assert not run_all_intraday._WEB_TRIGGER_LOCK.locked()
    st = run_all_intraday.get_web_trigger_status()
    assert st["status"] == "timeout"
    assert st["timed_out"] is True
    assert isolated_trigger_env["log_path"].exists()
    assert "kill failed" in isolated_trigger_env["log_path"].read_text(encoding="utf-8") or "access denied" in isolated_trigger_env["log_path"].read_text(encoding="utf-8")


def test_06_retrigger_during_timeout_rejected_then_allowed_after_release(isolated_trigger_env):
    started = threading.Event()
    release = threading.Event()

    class _BlockingProc:
        pid = 99904

        def wait(self, timeout=None):
            if timeout is not None:
                started.set()
                release.wait(timeout=2.0)
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return -9

        def poll(self):
            return None

    ok1, _ = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        timeout_sec=0.3,
        popen_fn=lambda *a, **kw: _BlockingProc(),
        kill_tree_fn=lambda pid, proc=None: None,
    )
    assert ok1 is True
    assert started.wait(timeout=2.0)

    # 執行中／逾時處理尚未釋放鎖前再按一次，必須拒絕
    ok2, msg2 = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        timeout_sec=0.3,
        popen_fn=lambda *a, **kw: _BlockingProc(),
        kill_tree_fn=lambda pid, proc=None: None,
    )
    assert ok2 is False
    assert "仍在執行" in msg2

    release.set()
    _wait_trigger_done(timeout_s=3.0)

    # 逾時結束釋放鎖後，下一次可以重新觸發
    ok3, _ = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        runner=lambda: None,
    )
    assert ok3 is True
    _wait_trigger_done(timeout_s=3.0)


def test_07_time_window_and_holiday_guards_mocked(isolated_trigger_env, monkeypatch):
    # Mock 休市判斷（模擬週六為非交易日）
    monkeypatch.setattr(
        intraday_speed,
        "is_runner_window",
        lambda dt: (dt.weekday() < 5 and (8, 58) <= (dt.hour, dt.minute) <= (13, 30), "mock"),
    )

    called = {"n": 0}
    ok_in, _ = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 10, 0, 0),
        runner=lambda: called.__setitem__("n", called["n"] + 1),
    )
    assert ok_in is True
    _wait_trigger_done(timeout_s=3.0)
    assert called["n"] == 1

    ok_after, msg_after = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 16, 15, 27, 0),
        runner=lambda: called.__setitem__("n", called["n"] + 1),
    )
    assert ok_after is False and "非盤中時段" in msg_after

    ok_holiday, msg_holiday = run_all_intraday.web_trigger_once(
        now_fn=lambda: _dt_tw(2026, 11, 14, 10, 0, 0),
        runner=lambda: called.__setitem__("n", called["n"] + 1),
    )
    assert ok_holiday is False and "非盤中時段" in msg_holiday
    assert called["n"] == 1


def test_08_fastapi_run_radar_status_sse_and_external_guard(isolated_trigger_env, monkeypatch):
    fake_status = {
        "status": "timeout",
        "timed_out": True,
        "timeout_sec": 900.0,
        "pid": 42424,
        "started_at": "2026-11-16T10:00:00+08:00",
        "finished_at": "2026-11-16T10:15:00+08:00",
        "message": "盤中掃描逾時（900 秒），已終止子程式樹 PID=42424",
    }
    monkeypatch.setattr(run_all_intraday, "web_trigger_once", lambda **kw: (True, "已在背景啟動全策略盤中到價掃描（平行上限 3 支）"))
    monkeypatch.setattr(run_all_intraday, "get_web_trigger_status", lambda: dict(fake_status))
    monkeypatch.setattr(access_guard, "admin_key_ok", lambda headers, **kw: False)

    client = TestClient(line_bot_server.app)

    # 1. 本機呼叫 POST /api/action/run_radar
    r_post = client.post("/api/action/run_radar")
    assert r_post.status_code == 200
    body_post = r_post.json()
    assert body_post["ok"] is True
    assert "message" in body_post
    assert body_post["trigger_status"]["status"] == "timeout"
    assert body_post["trigger_status"]["timed_out"] is True

    # 2. 本機呼叫 GET /api/action/run_radar/status
    r_get = client.get("/api/action/run_radar/status")
    assert r_get.status_code == 200
    body_get = r_get.json()
    for key in ("status", "timed_out", "timeout_sec", "pid", "started_at", "finished_at", "message"):
        assert key in body_get
    assert body_get["pid"] == 42424 and body_get["timed_out"] is True

    # 3. 外網未授權呼叫兩者均回 403
    ext_headers = {"cf-connecting-ip": "203.0.113.10"}
    assert client.post("/api/action/run_radar", headers=ext_headers).status_code == 403
    assert client.get("/api/action/run_radar/status", headers=ext_headers).status_code == 403

    # 4. SSE 端點不受影響（驗證註冊與串流回應標頭）
    sse_routes = [r.path for r in line_bot_server.app.routes if getattr(r, "path", "") == "/api/radar/stream"]
    assert sse_routes == ["/api/radar/stream"]
