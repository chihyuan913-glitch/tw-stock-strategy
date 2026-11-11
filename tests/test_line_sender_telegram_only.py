# -*- coding: utf-8 -*-
"""
tests/test_line_sender_telegram_only.py
驗證 line_sender.send_to_line 已完全停用 LINE 發送，只保留 Telegram (與 Discord 備援)
所有網路呼叫與外部模組 100% 嚴格 Mock，嚴禁任何真實連線。
"""

import io
import os
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import line_sender


class FakeResponse:
    """模擬 urllib urlopen 回傳物件 (具備 status=200 與 context manager)"""
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def read(self):
        return b"{}"


class FakeMarketSchedule:
    """模擬 market_schedule.should_suppress_push 永不攔截"""
    @staticmethod
    def should_suppress_push(force=False, task_type=""):
        return (False, "")


DEFAULT_FAKE_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456:FAKE_BOT_TOKEN",
    "TELEGRAM_CHAT_ID": "987654321",
    "LINE_CHANNEL_ACCESS_TOKEN": "FAKE_LINE_CHANNEL_ACCESS_TOKEN",
    "LINE_USER_ID": "U1234567890abcdef",
    "LINE_NOTIFY_TOKEN": "FAKE_LINE_NOTIFY_TOKEN",
}


@pytest.fixture(autouse=True)
def base_test_environment(monkeypatch):
    """基礎隔離環境配置：全域清除真實環境變數、注入假字典與守衛模組"""
    # 預設 load_env 回傳假環境設定
    current_env = dict(DEFAULT_FAKE_ENV)
    monkeypatch.setattr(line_sender, "load_env", lambda: dict(current_env))

    # 清除系統實際環境變數中任何推播相關鍵值
    keys_to_clear = [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "LINE_USER_ID",
        "LINE_NOTIFY_TOKEN",
        "DISCORD_WEBHOOK_URL",
    ]
    for k in keys_to_clear:
        monkeypatch.delenv(k, raising=False)

    for k in list(os.environ.keys()):
        if k.startswith("TELEGRAM_") or k.startswith("LINE_") or k.startswith("DISCORD_WEBHOOK_URL"):
            monkeypatch.delenv(k, raising=False)

    # 模擬 market_schedule
    monkeypatch.setitem(sys.modules, "market_schedule", FakeMarketSchedule())

    # LINE 相關函式若被調用直接拋出 AssertionError 報錯阻斷
    def fail_line(*args, **kwargs):
        raise AssertionError("LINE function must not be called")

    monkeypatch.setattr(line_sender, "send_line_messaging_api", fail_line)
    monkeypatch.setattr(line_sender, "send_line_notify", fail_line)


def test_only_telegram_called(monkeypatch):
    """1. send_to_line 只發送至 Telegram，絕不調用 LINE 端點"""
    recorded_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        return FakeResponse(200)

    monkeypatch.setattr(line_sender.urllib.request, "urlopen", fake_urlopen)

    res = line_sender.send_to_line("hi", strategy="09")
    assert res is True
    assert len(recorded_urls) > 0
    assert all(u.startswith("https://api.telegram.org/") for u in recorded_urls)
    assert not any("api.line.me" in u or "notify-api.line.me" in u for u in recorded_urls)


def test_dict_message_only_telegram(monkeypatch):
    """2. 傳入包含 _clean_text, _tg_card 與 LINE Flex 欄位之字典時，依然只走 Telegram"""
    recorded_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        return FakeResponse(200)

    monkeypatch.setattr(line_sender.urllib.request, "urlopen", fake_urlopen)

    msg = {
        "_clean_text": "【庫藏股宣告】2330 台積電",
        "_tg_card": "<b>【庫藏股宣告】2330 台積電</b>",
        "_stock_code": "2330",
        "altText": "庫藏股宣告 2330",
        "contents": {"type": "bubble", "body": {"type": "box"}},
    }
    res = line_sender.send_to_line(msg, strategy="09")
    assert res is True
    assert len(recorded_urls) > 0
    assert all(u.startswith("https://api.telegram.org/") for u in recorded_urls)
    assert not any("api.line.me" in u or "notify-api.line.me" in u for u in recorded_urls)


def test_telegram_http_error_returns_false(monkeypatch, capsys):
    """3. Telegram 遭遇 HTTP 500 時回傳 False，輸出 Telegram 推播失敗，不走 LINE"""
    recorded_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        raise urllib.error.HTTPError(url, 500, "err", {}, io.BytesIO(b"boom"))

    monkeypatch.setattr(line_sender.urllib.request, "urlopen", fake_urlopen)

    res = line_sender.send_to_line("hi", strategy="09")
    assert res is False
    captured = capsys.readouterr()
    assert "Telegram 推播失敗" in captured.out
    assert not any("api.line.me" in u or "notify-api.line.me" in u for u in recorded_urls)


def test_telegram_exception_returns_false(monkeypatch, capsys):
    """4. Telegram 遭遇底層網路例外 (OSError) 時回傳 False，輸出 Telegram 推播失敗"""
    recorded_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        raise OSError("network connection failed")

    monkeypatch.setattr(line_sender.urllib.request, "urlopen", fake_urlopen)

    res = line_sender.send_to_line("hi", strategy="09")
    assert res is False
    captured = capsys.readouterr()
    assert "Telegram 推播失敗" in captured.out


def test_no_telegram_config_returns_false_no_line(monkeypatch, capsys):
    """5. 僅有 LINE 設定而無 Telegram 設定時回傳 False，不呼叫任何 URL，輸出未設定 Telegram"""
    recorded_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        return FakeResponse(200)

    monkeypatch.setattr(line_sender.urllib.request, "urlopen", fake_urlopen)

    # 僅有 LINE 相關憑證，完全無 Telegram 鍵值
    monkeypatch.setattr(line_sender, "load_env", lambda: {
        "LINE_CHANNEL_ACCESS_TOKEN": "FAKE_LINE_TOKEN",
        "LINE_USER_ID": "FAKE_USER_ID",
        "LINE_NOTIFY_TOKEN": "FAKE_NOTIFY_TOKEN",
    })

    res = line_sender.send_to_line("hi", strategy="09")
    assert res is False
    assert len(recorded_urls) == 0
    captured = capsys.readouterr()
    assert "未設定 Telegram" in captured.out


def test_chunk_failure_not_masked(monkeypatch):
    """6. 長訊息 (> 3900 字元) 分段發送時，首段失敗後續段成功不得遮蔽錯誤，必須回傳 False"""
    call_count = 0

    def fake_urlopen(req, timeout=None):
        nonlocal call_count
        call_count += 1
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if call_count == 1:
            raise urllib.error.HTTPError(url, 500, "err", {}, io.BytesIO(b"server error"))
        return FakeResponse(200)

    monkeypatch.setattr(line_sender.urllib.request, "urlopen", fake_urlopen)

    long_msg = "\n".join([f"Line {i:04d}: test message payload content padding" for i in range(120)])
    assert len(long_msg) > 3900

    res = line_sender.send_telegram("fake_token", "fake_chat", long_msg, parse_mode="HTML")
    assert res is False
    assert call_count >= 2


def test_discord_unchanged(monkeypatch):
    """7. 當設定 DISCORD_WEBHOOK_URL 且 Telegram 失敗時，Discord Webhook 仍能獨立成功推播"""
    env_with_discord = dict(DEFAULT_FAKE_ENV)
    env_with_discord["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/123/fake_webhook"
    monkeypatch.setattr(line_sender, "load_env", lambda: dict(env_with_discord))

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        raise urllib.error.HTTPError(url, 500, "err", {}, io.BytesIO(b"tg boom"))

    monkeypatch.setattr(line_sender.urllib.request, "urlopen", fake_urlopen)

    discord_calls = []

    def fake_send_discord_webhook(webhook_url, message):
        discord_calls.append((webhook_url, message))
        return True

    monkeypatch.setattr(line_sender, "send_discord_webhook", fake_send_discord_webhook)

    res = line_sender.send_to_line("hi clean test message", strategy="09")
    assert res is True
    assert len(discord_calls) == 1
    assert discord_calls[0][0] == "https://discord.com/api/webhooks/123/fake_webhook"
    assert discord_calls[0][1] == "hi clean test message"
