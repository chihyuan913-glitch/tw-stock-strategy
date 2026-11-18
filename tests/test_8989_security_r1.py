# -*- coding: utf-8 -*-
import ast
import base64
import hashlib
import hmac
import io
import json
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import access_guard
import cmoney_sync
import line_bot_server
import line_sender
import radar_manager

ORIG_SEND_TO_LINE = line_sender.send_to_line

FAKE_TOKEN = "QzT9tokSENTINEL0123456789abcdef0123456789"
FAKE_SECRET = "Lsec7SENTINELabcdef0123456789abcd"
FAKE_ADMIN_KEY = "Adm1nKeySENTINEL0123456789abcdef0123"
FAKE_ACCOUNT = "acc9SENTINEL"
FAKE_PASSWORD = "pw9SENTINEL"
FAKE_CHAT_ID = "-1009876SENTINEL"
FAKE_LINE_ID = "U0123456789abcdef0123456789abcdef"

EXT_BASE = "http://stock.chihyuan.date"
LOC_BASE = "http://127.0.0.1:8989"
EXT_HEADERS = {"CF-Connecting-IP": "203.0.113.9", "Cf-Ray": "8f00test-TPE"}
FORBIDDEN_JSON = {"status": "forbidden", "message": "外網唯讀：此操作僅限本機執行"}


def _sign_line(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")


def _assert_no_secret_fragments(text: str, values) -> None:
    assert "SENTINEL" not in text
    for val in values:
        assert val not in text
        if len(val) >= 4:
            assert val[:4] not in text


@pytest.fixture(autouse=True)
def _block_network_and_subproc(monkeypatch, tmp_path):
    def _fail(*args, **kwargs):
        raise AssertionError("禁止在測試中發起真實網路、推播或子程序呼叫")

    monkeypatch.setattr(subprocess, "run", _fail)
    monkeypatch.setattr(subprocess, "Popen", _fail)
    monkeypatch.setattr(line_sender, "send_to_line", _fail)
    monkeypatch.setattr(urllib.request, "urlopen", _fail)

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


def test_is_external_matrix():
    for client, host in [
        (("127.0.0.1", 12345), b"127.0.0.1:8989"),
        (("::1", 12345), b"localhost:8989"),
        (("testclient", 50000), b"testserver"),
        (("127.0.0.1", 12345), b"[::1]:8989"),
    ]:
        scope = {"type": "http", "client": client, "headers": [(b"host", host)]}
        assert access_guard.is_external(scope) is False

    for hdr in access_guard.PROXY_HEADERS:
        scope = {
            "type": "http",
            "client": ("127.0.0.1", 12345),
            "headers": [(b"host", b"127.0.0.1:8989"), (hdr.encode("ascii"), b"1.2.3.4")],
        }
        assert access_guard.is_external(scope) is True

    assert access_guard.is_external({
        "type": "http",
        "client": ("127.0.0.1", 12345),
        "headers": [(b"host", b"stock.chihyuan.date")],
    }) is True

    for ip in ("10.1.2.3", "192.168.1.5", "203.0.113.9"):
        assert access_guard.is_external({
            "type": "http",
            "client": (ip, 12345),
            "headers": [(b"host", b"127.0.0.1:8989")],
        }) is True

    assert access_guard.is_external({
        "type": "http",
        "client": None,
        "headers": [(b"host", b"127.0.0.1:8989")],
    }) is True

    assert access_guard.is_external({
        "type": "http",
        "client": ("127.0.0.1", 12345),
        "headers": [(b"host", b"127.0.0.1:8989"), (b"CF-Connecting-IP", b"203.0.113.9")],
    }) is True


def test_external_writes_blocked_full_inventory(monkeypatch, tmp_path):
    calls = {"n": 0}

    def _bump(*args, **kwargs):
        calls["n"] += 1
        return []

    monkeypatch.setattr(line_bot_server, "schedule_server_shutdown", _bump)
    monkeypatch.setattr(radar_manager, "reset_today_alerts", _bump)
    monkeypatch.setattr(radar_manager, "clear_all_radar", _bump)
    monkeypatch.setattr(radar_manager, "sync_screener_to_radar", _bump)

    def _collect_api_routes(route_list):
        out = []
        for r in route_list:
            if isinstance(r, APIRoute):
                out.append(r)
            elif hasattr(r, "original_router"):
                out.extend(_collect_api_routes(r.original_router.routes))
            elif hasattr(r, "routes"):
                out.extend(_collect_api_routes(r.routes))
        return out

    routes = []
    for r in _collect_api_routes(line_bot_server.app.routes):
        non_get = [m for m in (r.methods or set()) if m not in ("GET", "HEAD", "OPTIONS")]
        if non_get:
            routes.append((r.path, sorted(non_get)))

    assert len(routes) >= 24
    inv_file = tmp_path / "external_write_routes.txt"
    inv_file.write_text("\n".join(f"{p} {ms}" for p, ms in routes), encoding="utf-8")

    client = TestClient(line_bot_server.app, base_url=EXT_BASE)
    extra_check_paths = []
    for path, _ in routes:
        if path in ("/callback", "/api/heartbeat"):
            continue
        if "{action_name}" in path:
            targets = [path.replace("{action_name}", a) for a in (
                "run_screener", "run_radar", "run_optimizer", "sync_cloud", "test_line", "other_action"
            )]
        else:
            targets = [path]
        for t in targets:
            resp = client.post(t, headers=EXT_HEADERS, json={})
            assert resp.status_code == 403
            assert resp.json() == FORBIDDEN_JSON
            if len(extra_check_paths) < 3:
                extra_check_paths.append(t)

    for t in extra_check_paths:
        for m in ("PUT", "PATCH", "DELETE"):
            resp = client.request(m, t, headers=EXT_HEADERS)
            assert resp.status_code == 403
            assert resp.json() == FORBIDDEN_JSON

    assert calls["n"] == 0


def test_local_writes_still_allowed(monkeypatch):
    calls = {"reset": 0, "test_line": 0, "fusion": 0}

    monkeypatch.setattr(radar_manager, "reset_today_alerts", lambda: calls.__setitem__("reset", calls["reset"] + 1) or 0)
    monkeypatch.setattr(line_sender, "send_to_line", lambda *a, **kw: calls.__setitem__("test_line", calls["test_line"] + 1) or True)

    import lab.web.router as lab_router_mod
    monkeypatch.setattr(lab_router_mod, "get_screener_fusion_summary", lambda *a, **kw: {"candidates": []})
    monkeypatch.setattr(line_sender, "send_to_line", lambda *a, **kw: calls.__setitem__("test_line", calls["test_line"] + 1) or True)

    # Also patch send_to_line in fusion_push_line via line_sender
    loc = TestClient(line_bot_server.app, base_url=LOC_BASE)
    r1 = loc.post("/api/radar/reset_alerts")
    assert r1.status_code != 403
    assert calls["reset"] == 1

    r2 = loc.post("/api/action/test_line")
    assert r2.status_code != 403
    assert calls["test_line"] == 1

    r3 = loc.post("/api/lab/fusion/push-line", json={"max_items": 3})
    assert r3.status_code != 403
    assert calls["test_line"] >= 2

    default_client = TestClient(line_bot_server.app)
    assert default_client.get("/api/lab/templates").status_code != 403
    assert default_client.post("/api/lab/fusion/push-line", json={"max_items": 1}).status_code != 403


def test_admin_key_rules(monkeypatch):
    calls = {"reset": 0}
    monkeypatch.setattr(radar_manager, "reset_today_alerts", lambda: calls.__setitem__("reset", calls["reset"] + 1) or 0)
    ext = TestClient(line_bot_server.app, base_url=EXT_BASE)

    monkeypatch.setenv("TW_ADMIN_KEY", FAKE_ADMIN_KEY)
    r_ok = ext.post(
        "/api/radar/reset_alerts",
        headers={**EXT_HEADERS, "X-TW-Admin-Key": FAKE_ADMIN_KEY},
    )
    assert r_ok.status_code != 403
    assert calls["reset"] == 1
    _assert_no_secret_fragments(r_ok.text + repr(dict(r_ok.headers)), [FAKE_ADMIN_KEY])

    wrong_key = FAKE_ADMIN_KEY[:-1] + ("X" if FAKE_ADMIN_KEY[-1] != "X" else "Y")
    for bad_hdrs in (
        {**EXT_HEADERS, "X-TW-Admin-Key": wrong_key},
        {**EXT_HEADERS, "X-TW-Admin-Key": ""},
        EXT_HEADERS,
    ):
        r_bad = ext.post("/api/radar/reset_alerts", headers=bad_hdrs)
        assert r_bad.status_code == 403
        _assert_no_secret_fragments(r_bad.text + repr(dict(r_bad.headers)), [FAKE_ADMIN_KEY, wrong_key])

    short_key = "ShortKeySENTINEL0123456789abcde"  # 31 chars
    assert len(short_key) == 31
    monkeypatch.setenv("TW_ADMIN_KEY", short_key)
    r_short = ext.post("/api/radar/reset_alerts", headers={**EXT_HEADERS, "X-TW-Admin-Key": short_key})
    assert r_short.status_code == 403
    _assert_no_secret_fragments(r_short.text, [short_key])

    monkeypatch.delenv("TW_ADMIN_KEY", raising=False)
    r_unset = ext.post("/api/radar/reset_alerts", headers={**EXT_HEADERS, "X-TW-Admin-Key": FAKE_ADMIN_KEY})
    assert r_unset.status_code == 403

    monkeypatch.setenv("TW_ADMIN_KEY", FAKE_ADMIN_KEY)
    r_logs = ext.get("/logs", headers={**EXT_HEADERS, "X-TW-Admin-Key": FAKE_ADMIN_KEY})
    assert r_logs.status_code == 403
    _assert_no_secret_fragments(r_logs.text + repr(dict(r_logs.headers)), [FAKE_ADMIN_KEY])


def test_logs_local_only_and_no_token(monkeypatch, capsys):
    monkeypatch.setattr(line_bot_server, "CHANNEL_ACCESS_TOKEN", FAKE_TOKEN)
    ext = TestClient(line_bot_server.app, base_url=EXT_BASE)
    loc = TestClient(line_bot_server.app, base_url=LOC_BASE)

    assert ext.get("/logs", headers=EXT_HEADERS).status_code == 403

    line_bot_server.log_debug(
        "test Bearer xyz1234567890 token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ1234 "
        f"uid={FAKE_LINE_ID} password=abc"
    )
    out = capsys.readouterr().out
    assert "xyz1234567890" not in out
    assert "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ1234" not in out
    assert FAKE_LINE_ID not in out
    assert "password=abc" not in out
    assert "[已遮蔽]" in out

    r_loc = loc.get("/logs")
    assert r_loc.status_code == 200
    data = r_loc.json()
    assert "token_prefix" not in data
    assert data["token_set"] is True
    _assert_no_secret_fragments(r_loc.text, [FAKE_TOKEN])
    joined_logs = "\n".join(data["logs"])
    assert "xyz1234567890" not in joined_logs
    assert FAKE_LINE_ID not in joined_logs
    assert "[已遮蔽]" in joined_logs


def test_protected_get_routes(monkeypatch):
    import lab.web.router as lab_router_mod
    calls = {"n": 0}

    def _fake_report(*a, **kw):
        calls["n"] += 1
        return {"items": []}

    monkeypatch.setattr(lab_router_mod.ScreenerFusionBridge, "generate_next_day_advisor_report", staticmethod(_fake_report))
    monkeypatch.setattr(lab_router_mod.ScreenerFusionBridge, "generate_strategy_leaderboard", staticmethod(_fake_report))

    ext = TestClient(line_bot_server.app, base_url=EXT_BASE)
    loc = TestClient(line_bot_server.app, base_url=LOC_BASE)
    for p in sorted(access_guard.PROTECTED_GET_PATHS):
        r_ext = ext.get(p, headers=EXT_HEADERS)
        assert r_ext.status_code == 403
        assert r_ext.json() == FORBIDDEN_JSON
    assert calls["n"] == 0
    for p in sorted(access_guard.PROTECTED_GET_PATHS):
        r_loc = loc.get(p)
        assert r_loc.status_code != 403
    assert calls["n"] == len(access_guard.PROTECTED_GET_PATHS)


def test_public_reads_and_heartbeat_stay_open():
    ext = TestClient(line_bot_server.app, base_url=EXT_BASE)
    loc = TestClient(line_bot_server.app, base_url=LOC_BASE)
    for p in ("/", "/health", "/ping", "/api/radar/triggered_today", "/dashboard_guards.js"):
        r_ext = ext.get(p, headers=EXT_HEADERS)
        r_loc = loc.get(p)
        assert r_ext.status_code != 403
        assert r_ext.status_code == r_loc.status_code

    assert ext.post("/api/heartbeat", headers=EXT_HEADERS).status_code == 200
    assert ext.head("/health", headers=EXT_HEADERS).status_code != 403


def test_network_info_external_strips_lan(monkeypatch):
    sock_calls = {"n": 0}
    orig_socket = socket.socket

    class _FakeSock:
        def __init__(self, *a, **kw):
            sock_calls["n"] += 1

        def connect(self, *a, **kw):
            pass

        def getsockname(self):
            return ("192.168.50.88", 12345)

        def close(self):
            pass

    class _PatchedSocket(orig_socket):
        def __new__(cls, family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, fileno=None):
            if type == socket.SOCK_DGRAM:
                return _FakeSock()
            return orig_socket(family, type, proto, fileno)

    import tunnel_manager
    monkeypatch.setattr(socket, "socket", _PatchedSocket)
    monkeypatch.setattr(tunnel_manager, "get_current_tunnel_url", lambda: "https://stock.chihyuan.date")

    ext = TestClient(line_bot_server.app, base_url=EXT_BASE)
    r_ext = ext.get("/api/system/network_info", headers=EXT_HEADERS)
    assert r_ext.status_code == 200
    d_ext = r_ext.json()
    assert d_ext["lan_url"] == ""
    assert sock_calls["n"] == 0
    assert d_ext["local_url"].startswith("http://127.0.0.1:")
    assert d_ext["tunnel_url"] == "https://stock.chihyuan.date"

    loc = TestClient(line_bot_server.app, base_url=LOC_BASE)
    r_loc = loc.get("/api/system/network_info")
    assert r_loc.status_code == 200
    assert r_loc.json()["lan_url"].startswith("http://")
    assert sock_calls["n"] == 1


def test_browser_leaving_external_blocked(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(line_bot_server, "LOCAL_AUTO_SHUTDOWN", True)
    monkeypatch.setattr(line_bot_server, "schedule_server_shutdown", lambda *a, **kw: calls.__setitem__("n", calls["n"] + 1))

    ext = TestClient(line_bot_server.app, base_url=EXT_BASE)
    loc = TestClient(line_bot_server.app, base_url=LOC_BASE)

    assert ext.post("/api/browser-leaving", headers=EXT_HEADERS).status_code == 403
    assert calls["n"] == 0
    assert loc.post("/api/browser-leaving").status_code == 200
    assert calls["n"] == 1


def test_callback_valid_signature_external(monkeypatch):
    queued = {"n": 0}
    monkeypatch.setattr(line_bot_server, "LINE_CHANNEL_SECRET", FAKE_SECRET)
    monkeypatch.setattr(line_bot_server, "process_event_task", lambda ev: queued.__setitem__("n", queued["n"] + 1))

    payload = json.dumps({"events": [{"type": "message", "message": {"type": "text", "text": "2330"}}]}).encode("utf-8")
    sig = _sign_line(FAKE_SECRET, payload)

    ext = TestClient(line_bot_server.app, base_url=EXT_BASE)
    r_ext = ext.post("/callback", content=payload, headers={**EXT_HEADERS, "X-Line-Signature": sig})
    assert r_ext.status_code == 200
    assert r_ext.json()["events_queued"] == 1
    assert queued["n"] == 1

    loc = TestClient(line_bot_server.app, base_url=LOC_BASE)
    r_loc = loc.post("/callback", content=payload, headers={"X-Line-Signature": sig})
    assert r_loc.status_code == 200
    assert queued["n"] == 2


def test_callback_bad_signature_rejected(monkeypatch):
    queued = {"n": 0}
    json_calls = {"n": 0, "active": False}
    orig_loads = json.loads

    def _spy_loads(*a, **kw):
        if json_calls["active"]:
            json_calls["n"] += 1
        return orig_loads(*a, **kw)

    monkeypatch.setattr(line_bot_server, "LINE_CHANNEL_SECRET", FAKE_SECRET)
    monkeypatch.setattr(line_bot_server, "process_event_task", lambda ev: queued.__setitem__("n", queued["n"] + 1))
    monkeypatch.setattr(line_bot_server.json, "loads", _spy_loads)

    payload = json.dumps({"events": [{"type": "message", "message": {"type": "text", "text": "2330"}}]}).encode("utf-8")
    good_sig = _sign_line(FAKE_SECRET, payload)
    other_body_sig = _sign_line(FAKE_SECRET, b'{"events":[]}')
    other_secret_sig = _sign_line("OtherSecretSENTINEL0123456789abcd", payload)

    ext = TestClient(line_bot_server.app, base_url=EXT_BASE)
    bad_cases = [
        (payload, EXT_HEADERS),
        (payload, {**EXT_HEADERS, "X-Line-Signature": ""}),
        (payload, {**EXT_HEADERS, "X-Line-Signature": other_secret_sig}),
        (payload, {**EXT_HEADERS, "X-Line-Signature": other_body_sig}),
        (payload[:-1] + b" ", {**EXT_HEADERS, "X-Line-Signature": good_sig}),
        (payload, {**EXT_HEADERS, "X-Line-Signature": "not-valid-base64!!!"}),
        (payload, {**EXT_HEADERS, "X-Line-Signature": "X" + good_sig}),
    ]
    for body_bytes, hdrs in bad_cases:
        json_calls["active"] = True
        resp = ext.post("/callback", content=body_bytes, headers=hdrs)
        json_calls["active"] = False
        assert resp.status_code == 403
        assert resp.json() == {"status": "invalid_signature"}

    assert queued["n"] == 0
    assert json_calls["n"] == 0


def test_callback_secret_missing_warns_and_passes(monkeypatch):
    logs = []
    queued = {"n": 0}
    now_box = {"t": 1000000.0}

    monkeypatch.setattr(line_bot_server, "LINE_CHANNEL_SECRET", "")
    monkeypatch.setattr(line_bot_server, "_LAST_SECRET_WARN_AT", None)
    monkeypatch.setattr(line_bot_server, "log_debug", lambda m: logs.append(m))
    monkeypatch.setattr(line_bot_server, "process_event_task", lambda ev: queued.__setitem__("n", queued["n"] + 1))
    monkeypatch.setattr(time, "time", lambda: now_box["t"])

    payload = json.dumps({"events": [{"type": "message"}]}).encode("utf-8")
    ext = TestClient(line_bot_server.app, base_url=EXT_BASE)

    assert ext.post("/callback", content=payload, headers=EXT_HEADERS).status_code == 200
    assert ext.post("/callback", content=payload, headers={**EXT_HEADERS, "X-Line-Signature": "garbage"}).status_code == 200
    assert ext.post("/callback", content=payload, headers=EXT_HEADERS).status_code == 200
    assert queued["n"] == 3

    warns = [m for m in logs if "密鑰未設定" in m]
    assert len(warns) == 1

    now_box["t"] += 601.0
    assert ext.post("/callback", content=payload, headers=EXT_HEADERS).status_code == 200
    warns2 = [m for m in logs if "密鑰未設定" in m]
    assert len(warns2) == 2

    for m in logs:
        _assert_no_secret_fragments(m, [FAKE_SECRET, "garbage"])

    monkeypatch.setattr(line_bot_server, "LINE_CHANNEL_SECRET", FAKE_SECRET)
    r_bad = ext.post("/callback", content=payload, headers={**EXT_HEADERS, "X-Line-Signature": "wrong"})
    assert r_bad.status_code == 403
    assert queued["n"] == 4

    assert ext.post("/callback/", content=payload, headers=EXT_HEADERS).status_code == 403


def test_responses_never_contain_secrets(monkeypatch):
    blocked_calls = []
    monkeypatch.setattr(line_bot_server, "CHANNEL_ACCESS_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(line_bot_server, "LINE_CHANNEL_SECRET", FAKE_SECRET)
    monkeypatch.setenv("TW_ADMIN_KEY", FAKE_ADMIN_KEY)
    monkeypatch.setenv("CMONEY_ACCOUNT", FAKE_ACCOUNT)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", FAKE_CHAT_ID)
    monkeypatch.setattr(
        line_bot_server,
        "_on_external_block",
        lambda m, p: blocked_calls.append((m, p)),
    )
    # Also spy on the middleware's on_block reference if already bound
    mw = line_bot_server.app.middleware_stack
    orig_on_block = None
    cur = mw
    while cur is not None:
        if isinstance(cur, access_guard.ExternalGuardMiddleware):
            orig_on_block = cur.on_block
            cur.on_block = lambda m, p: blocked_calls.append((m, p))
            break
        cur = getattr(cur, "app", None)

    secrets_json = [FAKE_TOKEN, FAKE_SECRET, FAKE_ADMIN_KEY, FAKE_ACCOUNT, FAKE_CHAT_ID]
    secrets_html = [FAKE_TOKEN, FAKE_SECRET, FAKE_ADMIN_KEY, FAKE_ACCOUNT, FAKE_CHAT_ID[1:]]
    ext = TestClient(line_bot_server.app, base_url=EXT_BASE)
    loc = TestClient(line_bot_server.app, base_url=LOC_BASE)

    try:
        for c, hdrs in ((loc, {}), (ext, EXT_HEADERS)):
            for path in ("/", "/health", "/ping", "/logs", "/api/system/network_info"):
                r = c.get(path, headers=hdrs)
                chk = secrets_html if path == "/" else secrets_json
                _assert_no_secret_fragments(r.text + repr(dict(r.headers)), chk)
            r_post = c.post("/api/radar/reset_alerts?k=SENTINEL", headers=hdrs)
            _assert_no_secret_fragments(r_post.text + repr(dict(r_post.headers)), secrets_json)
            r_cb = c.post("/callback", content=b"{}", headers={**hdrs, "X-Line-Signature": "bad"})
            _assert_no_secret_fragments(r_cb.text + repr(dict(r_cb.headers)), secrets_json)

        assert any(p == "/api/radar/reset_alerts" and "SENTINEL" not in p for _, p in blocked_calls)
    finally:
        if orig_on_block is not None and cur is not None:
            cur.on_block = orig_on_block


def test_cmoney_no_account_fragment(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cmoney_sync, "ENV_PATH", tmp_path / ".missing.env")
    monkeypatch.setattr(cmoney_sync, "load_env", lambda: {})
    monkeypatch.setenv("CMONEY_ACCOUNT", FAKE_ACCOUNT)
    monkeypatch.setenv("CMONEY_PASSWORD", FAKE_PASSWORD)
    monkeypatch.delenv("CMONEY_AUTH_TOKEN", raising=False)

    class _Resp404:
        status = 404

        def read(self):
            return b""

    class _FakeOpener:
        def open(self, req, timeout=10):
            return _Resp404()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **kw: _FakeOpener())
    client = cmoney_sync.CMoneyWatchlistClient()
    ok, _msg = client.authenticate()
    assert ok is False
    out = capsys.readouterr().out
    _assert_no_secret_fragments(out, [FAKE_ACCOUNT, FAKE_PASSWORD])
    assert "帳號已設定" in out


def test_sender_and_radar_no_id_fragment(monkeypatch, capsys):
    class _FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":true}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _FakeResp())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ1234")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", FAKE_CHAT_ID)
    assert line_sender.send_telegram("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ1234", FAKE_CHAT_ID, "test") is True
    out_tg = capsys.readouterr().out
    _assert_no_secret_fragments(out_tg, [FAKE_CHAT_ID])
    assert "-100" not in out_tg

    import market_schedule
    monkeypatch.setattr(market_schedule, "should_suppress_push", lambda *a, **kw: (False, ""))
    monkeypatch.setattr(market_schedule, "is_intraday_push_suppressed", lambda *a, **kw: (False, ""))
    monkeypatch.setattr(line_sender, "send_to_line", ORIG_SEND_TO_LINE)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", FAKE_TOKEN)
    assert line_sender.send_to_line("hi", target_id=FAKE_LINE_ID) is True
    out_line = capsys.readouterr().out
    _assert_no_secret_fragments(out_line, [FAKE_LINE_ID, FAKE_TOKEN])
    assert "cdef" not in out_line

    monkeypatch.setattr(radar_manager, "get_radar_channel", lambda: FAKE_LINE_ID)
    monkeypatch.setattr(radar_manager, "load_watchlist", lambda: {})
    status_txt = radar_manager.get_radar_status()
    _assert_no_secret_fragments(status_txt, [FAKE_LINE_ID])
    assert "已綁定" in status_txt

    monkeypatch.setattr(line_bot_server, "reply_line_message", lambda *a, **kw: None)
    line_bot_server.process_event_task({
        "type": "join",
        "replyToken": "dummy",
        "source": {"groupId": "C0123abcd4567ef890123abcd4567ef89"},
    })
    out_join = capsys.readouterr().out
    assert "C0123abc" not in out_join


def test_static_guards_ast():
    work_dir = Path(__file__).resolve().parent.parent
    lbs_src = (work_dir / "line_bot_server.py").read_text(encoding="utf-8")
    lbs_tree = ast.parse(lbs_src)

    assert "token_prefix" not in lbs_src

    has_add_mw = False
    callback_verify_line = None
    callback_loads_line = None
    for node in ast.walk(lbs_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_middleware":
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "ExternalGuardMiddleware":
                has_add_mw = True
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            assert node.value.id not in ("CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET")
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "callback":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    if isinstance(sub.func, ast.Name) and sub.func.id == "verify_line_signature":
                        callback_verify_line = sub.lineno
                    elif isinstance(sub.func, ast.Attribute) and sub.func.attr == "loads":
                        callback_loads_line = sub.lineno

    assert has_add_mw
    assert callback_verify_line is not None and callback_loads_line is not None
    assert callback_verify_line < callback_loads_line

    ag_src = (work_dir / "access_guard.py").read_text(encoding="utf-8")
    ag_tree = ast.parse(ag_src)
    assert "hmac.compare_digest" in ag_src
    for node in ast.walk(ag_tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) >= 20:
            if not node.value.startswith("/"):
                assert re.fullmatch(r"[A-Za-z0-9+/=_-]{20,}", node.value) is None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "print"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert "log" not in node.func.attr.lower()

    for rel in ("cmoney_sync.py", "line_sender.py", "radar_manager.py"):
        txt = (work_dir / rel).read_text(encoding="utf-8")
        for pat in ("account[", "chat_id)[", "to_id[", "channel_id[", "target_dest["):
            assert pat not in txt, f"{rel} still contains {pat}"
