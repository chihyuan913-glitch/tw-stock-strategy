# -*- coding: utf-8 -*-
"""
tests/test_line_push_disabled.py
驗證 line_bot_server.py 與 radar_manager.py 已完全停用 LINE Push API。
所有網路請求一律 Mock，嚴禁真實連線。
"""

import io
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 確保 TW_STOCK_ISOLATE 未設定，使 line_bot_server 可正常導入
os.environ.pop("TW_STOCK_ISOLATE", None)

import line_sender
import line_bot_server
import radar_manager


class FakeResponse:
    """模擬 urllib urlopen 回傳物件 (具備 status=200 與 context manager)"""
    def __init__(self, status=200, body=b"{}"):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def read(self):
        return self._body


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """清除推播相關環境變數，設置測試安全環境"""
    monkeypatch.delenv("TW_STOCK_ISOLATE", raising=False)
    for k in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_USER_ID"]:
        monkeypatch.delenv(k, raising=False)


def test_reply_failure_does_not_push(monkeypatch):
    """1. Reply 失敗時絕不退回 LINE Push API，僅嘗試 Reply URL"""
    recorded_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        raise urllib.error.HTTPError(
            url, 400, "Bad Request", hdrs={}, fp=io.BytesIO(b'{"message":"Invalid reply token"}')
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(line_bot_server.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(line_bot_server, "CHANNEL_ACCESS_TOKEN", "fake_line_token")

    # 呼叫 reply_line_message (reply_token, message_payload, target_id, fallback_text)
    line_bot_server.reply_line_message(
        reply_token="fake_reply_token",
        message_payload="測試訊息",
        target_id="U1234567890abcdef",
        fallback_text="純文字備援"
    )

    assert len(recorded_urls) > 0
    assert any("api.line.me/v2/bot/message/reply" in u for u in recorded_urls)
    assert not any("/v2/bot/message/push" in u for u in recorded_urls)


def test_reply_success_still_works(monkeypatch):
    """2. Reply 成功時正常呼叫 Reply API 一次，無 Push 呼叫"""
    recorded_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        return FakeResponse(200, b'{}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(line_bot_server.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(line_bot_server, "CHANNEL_ACCESS_TOKEN", "fake_line_token")

    line_bot_server.reply_line_message(
        reply_token="fake_reply_token",
        message_payload="測試訊息",
        target_id="U1234567890abcdef"
    )

    assert len(recorded_urls) == 1
    assert "api.line.me/v2/bot/message/reply" in recorded_urls[0]
    assert not any("/v2/bot/message/push" in u for u in recorded_urls)


def test_radar_test_alert_uses_telegram_only(monkeypatch):
    """3. radar_manager.generate_test_alert 僅使用 Telegram 發送，絕不使用 LINE API"""
    fake_env = {
        "TELEGRAM_BOT_TOKEN": "123456:FAKE_TG_TOKEN",
        "TELEGRAM_CHAT_ID": "987654321",
        "LINE_CHANNEL_ACCESS_TOKEN": "fake_line_channel_token",
    }
    monkeypatch.setattr(line_sender, "load_env", lambda: dict(fake_env))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    recorded_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        return FakeResponse(200, b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(line_sender.urllib.request, "urlopen", fake_urlopen)

    res = radar_manager.generate_test_alert("fake-line-token", "Cfake")

    assert res.startswith("✅")
    assert len(recorded_urls) > 0
    assert all(u.startswith("https://api.telegram.org/") for u in recorded_urls)
    assert not any("api.line.me" in u for u in recorded_urls)


def test_radar_test_alert_telegram_failure_reported(monkeypatch):
    """4. Telegram 發送失敗時回傳 ❌ 開頭字串，且無任何 api.line.me 請求"""
    fake_env = {
        "TELEGRAM_BOT_TOKEN": "123456:FAKE_TG_TOKEN",
        "TELEGRAM_CHAT_ID": "987654321",
        "LINE_CHANNEL_ACCESS_TOKEN": "fake_line_channel_token",
    }
    monkeypatch.setattr(line_sender, "load_env", lambda: dict(fake_env))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    recorded_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        raise OSError("Simulated Telegram connection error")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(line_sender.urllib.request, "urlopen", fake_urlopen)

    res = radar_manager.generate_test_alert("fake-line-token", "Cfake")

    assert res.startswith("❌")
    assert not any("api.line.me" in u for u in recorded_urls)


def test_radar_test_alert_no_telegram_config(monkeypatch):
    """5. 未設定 Telegram 時回傳提示訊息，且不發出任何網路請求"""
    fake_env = {
        "LINE_CHANNEL_ACCESS_TOKEN": "fake_line_channel_token",
    }
    monkeypatch.setattr(line_sender, "load_env", lambda: dict(fake_env))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    recorded_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        return FakeResponse(200, b'{}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(line_sender.urllib.request, "urlopen", fake_urlopen)

    res = radar_manager.generate_test_alert("fake-line-token", "Cfake")

    assert res.startswith("⚠️ 未設定 Telegram")
    assert len(recorded_urls) == 0


def test_no_push_url_left():
    """6. 靜態檢驗：line_bot_server.py 與 radar_manager.py 中已無 api.line.me/v2/bot/message/push，但保留 reply"""
    line_bot_text = (ROOT_DIR / "line_bot_server.py").read_text(encoding="utf-8")
    radar_text = (ROOT_DIR / "radar_manager.py").read_text(encoding="utf-8")

    assert "api.line.me/v2/bot/message/push" not in line_bot_text
    assert "api.line.me/v2/bot/message/push" not in radar_text
    assert "api.line.me/v2/bot/message/reply" in line_bot_text
