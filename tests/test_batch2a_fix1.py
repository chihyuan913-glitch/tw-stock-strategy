# -*- coding: utf-8 -*-
import ast
import asyncio
import datetime
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import access_guard
import cmoney_sync
import intraday_speed
import line_bot_server
import line_sender
import market_schedule
import radar_sse

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))


def _dt_tw(y, m, d, hh=0, mm=0, ss=0):
    return datetime.datetime(y, m, d, hh, mm, ss, tzinfo=TZ_TW)


@pytest.fixture(autouse=True)
def _sandbox_fix1_env(monkeypatch, tmp_path):
    raw_now = os.environ.get("TW_BATCH2A_TEST_NOW", "").strip()
    if raw_now:
        default_now = datetime.datetime.fromisoformat(raw_now).astimezone(TZ_TW)
    else:
        default_now = _dt_tw(2026, 11, 16, 10, 0, 0)

    ledger_dir = tmp_path / "ledger"
    fake_env_file = tmp_path / "test_fake.env"
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
    monkeypatch.setattr(intraday_speed, "ledger_dir", lambda: ledger_dir)

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


def _make_scope(external: bool, spec_version: str = "2.3"):
    if external:
        headers = [
            (b"host", b"stock.chihyuan.date"),
            (b"cf-connecting-ip", b"203.0.113.5"),
        ]
    else:
        headers = [(b"host", b"127.0.0.1:8989")]
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/radar/stream",
        "raw_path": b"/api/radar/stream",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8989),
    }


def _make_hub(monkeypatch, tmp_path, *, max_lifetime_sec=60):
    now_calls = {"n": 0}
    mono_clk = {"v": 0.0}

    def _now_fn():
        now_calls["n"] += 1
        return intraday_speed.taipei_now()

    hub = radar_sse.RadarSseHub(
        ledger_dir_fn=lambda: tmp_path / "ledger",
        now_fn=_now_fn,
        window_fn=lambda now: intraday_speed.is_radar_push_allowed(now)[0],
        max_clients=20,
        max_external=5,
        poll_sec=1,
        settle_sec=0,
        heartbeat_sec=5,
        max_lifetime_sec=max_lifetime_sec,
    )

    async def _fake_sleep(sec):
        mono_clk["v"] += float(sec)

    def _wrapped_stream(is_disconnected, *, external=False):
        return radar_sse.RadarSseHub.stream(
            hub,
            is_disconnected,
            external=external,
            sleep=_fake_sleep,
            mono=lambda: mono_clk["v"],
        )

    monkeypatch.setattr(hub, "stream", _wrapped_stream)
    monkeypatch.setattr(line_bot_server, "_get_radar_sse_hub", lambda: hub)
    return hub, now_calls, mono_clk


def _run_asgi(scope, receive, send):
    try:
        asyncio.run(line_bot_server.app(scope, receive, send))
    except Exception as exc:
        return type(exc).__name__
    return None


def test_sse_slot_released_when_disconnected_before_first_pull(monkeypatch, tmp_path):
    hub, now_calls, mono_clk = _make_hub(monkeypatch, tmp_path, max_lifetime_sec=60)

    # (a) spec_version 2.3: disconnect immediately before first pull, 5 times
    for idx in range(5):
        sent = []

        async def _recv_disc():
            return {"type": "http.disconnect"}

        async def _send_rec(msg, s=sent):
            s.append(msg)
            await asyncio.sleep(0)

        _run_asgi(_make_scope(external=True, spec_version="2.3"), _recv_disc, _send_rec)
        counts = (hub.active_clients, hub.active_external)
        assert counts == (0, 0), f"slot leak on 2.3 disconnect #{idx+1}: counts={counts}"
        assert now_calls["n"] == 0

    # (b) spec_version 2.4: send raises OSError on http.response.start, 5 times
    for idx in range(5):
        async def _recv_wait():
            await asyncio.Event().wait()

        async def _send_oserr(msg):
            if msg.get("type") == "http.response.start":
                raise OSError("broken pipe on response start")

        _run_asgi(_make_scope(external=True, spec_version="2.4"), _recv_wait, _send_oserr)
        counts = (hub.active_clients, hub.active_external)
        assert counts == (0, 0), f"slot leak on 2.4 OSError #{idx+1}: counts={counts}"
        assert now_calls["n"] == 0

    # (c) After 10 pre-pull disconnects, normal external stream returns 200 (not 503) and releases slot
    mono_clk["v"] = 0.0
    sent_ok = []

    async def _recv_never():
        await asyncio.Event().wait()

    async def _send_ok(msg):
        sent_ok.append(msg)

    _run_asgi(_make_scope(external=True, spec_version="2.3"), _recv_never, _send_ok)
    starts = [m for m in sent_ok if m.get("type") == "http.response.start"]
    assert starts and starts[0]["status"] == 200
    counts = (hub.active_clients, hub.active_external)
    assert counts == (0, 0), f"slot leak after normal external stream: counts={counts}"

    # (d) External cap (5) still enforced -> 503 busy; local still 200
    for _ in range(5):
        assert hub.try_register(external=True) is True

    sent_503 = []

    async def _send_503(msg):
        sent_503.append(msg)

    _run_asgi(_make_scope(external=True, spec_version="2.3"), _recv_never, _send_503)
    starts_503 = [m for m in sent_503 if m.get("type") == "http.response.start"]
    assert starts_503 and starts_503[0]["status"] == 503
    body_503 = b"".join(m.get("body", b"") for m in sent_503 if m.get("type") == "http.response.body")
    assert json.loads(body_503.decode("utf-8")) == {"status": "busy"}

    mono_clk["v"] = 0.0
    sent_loc = []

    async def _send_loc(msg):
        sent_loc.append(msg)

    _run_asgi(_make_scope(external=False, spec_version="2.3"), _recv_never, _send_loc)
    starts_loc = [m for m in sent_loc if m.get("type") == "http.response.start"]
    assert starts_loc and starts_loc[0]["status"] == 200

    for _ in range(5):
        hub.unregister(external=True)
    assert (hub.active_clients, hub.active_external) == (0, 0), "slot leak after manual unregister"

    mono_clk["v"] = 0.0
    sent_200 = []
    _run_asgi(_make_scope(external=True, spec_version="2.3"), _recv_never, lambda m: sent_200.append(m) or asyncio.sleep(0))
    starts_200 = [m for m in sent_200 if m.get("type") == "http.response.start"]
    assert starts_200 and starts_200[0]["status"] == 200
    assert (hub.active_clients, hub.active_external) == (0, 0), "slot leak after re-open 200"

    # (e) Local scope 2.3 pre-pull disconnect
    now_before_e = now_calls["n"]

    async def _recv_disc_loc():
        return {"type": "http.disconnect"}

    _run_asgi(_make_scope(external=False, spec_version="2.3"), _recv_disc_loc, lambda m: asyncio.sleep(0))
    counts_e = (hub.active_clients, hub.active_external)
    assert counts_e == (0, 0), f"slot leak on local 2.3 disconnect: counts={counts_e}"
    assert now_calls["n"] == now_before_e


def test_sse_slot_released_after_normal_stream_idempotent(monkeypatch, tmp_path):
    hub, now_calls, mono_clk = _make_hub(monkeypatch, tmp_path, max_lifetime_sec=3)

    # (a) Normal complete stream via TestClient (local + external)
    client = TestClient(line_bot_server.app)
    r_loc = client.get("/api/radar/stream")
    assert r_loc.status_code == 200
    assert r_loc.headers.get("content-type", "").startswith("text/event-stream")
    assert r_loc.text.startswith("retry: 5000\n\n")
    assert "SENTINEL" not in r_loc.text
    assert (hub.active_clients, hub.active_external) == (0, 0), "slot leak after TestClient local"

    mono_clk["v"] = 0.0
    ext_client = TestClient(line_bot_server.app, base_url="http://stock.chihyuan.date")
    r_ext = ext_client.get("/api/radar/stream", headers={"CF-Connecting-IP": "203.0.113.5"})
    assert r_ext.status_code == 200
    assert (hub.active_clients, hub.active_external) == (0, 0), "slot leak after TestClient external"

    # (b) Mid-stream disconnect on 2.4 path (OSError on first non-empty body)
    mono_clk["v"] = 0.0
    now_before_b = now_calls["n"]

    async def _recv_wait():
        await asyncio.Event().wait()

    async def _send_mid_err(msg):
        if msg.get("type") == "http.response.body" and msg.get("body"):
            raise OSError("client closed mid-stream")

    _run_asgi(_make_scope(external=True, spec_version="2.4"), _recv_wait, _send_mid_err)
    assert (hub.active_clients, hub.active_external) == (0, 0), "slot leak on mid-stream disconnect"
    assert now_calls["n"] >= now_before_b + 1

    # (c) SlotGuardedStream.finalize idempotency
    from radar_sse import SlotGuardedStream

    assert hub.try_register(external=True) is True
    g1 = SlotGuardedStream(hub, hub.stream(lambda: True, external=True), external=True)
    assert asyncio.run(g1.finalize()) is True
    assert asyncio.run(g1.finalize()) is False
    assert (hub.active_clients, hub.active_external) == (0, 0), "slot leak or negative on double finalize"

    # Other connection holds (1, 1); pulled stream finalized 3 times must NOT steal that slot
    assert hub.try_register(external=True) is True
    mono_clk["v"] = 0.0
    assert hub.try_register(external=True) is True
    g2 = SlotGuardedStream(hub, hub.stream(lambda: False, external=True), external=True)

    async def _drain_and_triple_fin():
        async for _ in g2:
            pass
        r1 = await g2.finalize()
        r2 = await g2.finalize()
        r3 = await g2.finalize()
        return r1, r2, r3

    f_res = asyncio.run(_drain_and_triple_fin())
    assert f_res == (True, False, False)
    assert (hub.active_clients, hub.active_external) == (1, 1), "slot leak or stole other slot on triple finalize"

    hub.unregister(external=True)
    assert (hub.active_clients, hub.active_external) == (0, 0)
    hub.unregister(external=True)
    assert (hub.active_clients, hub.active_external) == (0, 0)

    # (d) AST / source checks on line_bot_server.py
    src = (ROOT_DIR / "line_bot_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    sse_cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_SseStreamingResponse":
            sse_cls = node
            break
    assert sse_cls is not None
    assert any(isinstance(b, ast.Name) and b.id == "StreamingResponse" for b in sse_cls.bases)
    call_fn = next((m for m in sse_cls.body if isinstance(m, ast.AsyncFunctionDef) and m.name == "__call__"), None)
    assert call_fn is not None
    assert any(isinstance(n, ast.Try) and len(n.finalbody) > 0 for n in ast.walk(call_fn))

    route_seg = src.split('@app.get("/api/radar/stream")', 1)[1].split("@app.", 1)[0]
    assert "SlotGuardedStream" in route_seg
    assert "status_code=503" in route_seg
    assert "query_params" not in route_seg
    assert 'host="127.0.0.1"' in src
