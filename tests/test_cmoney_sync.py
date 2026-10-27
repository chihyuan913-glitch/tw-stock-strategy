#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
單元測試：籌碼K線 (CMoney) 自選股雲端與本地同步模組 (test_cmoney_sync.py)
"""

import sys
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cmoney_sync import (
    load_env,
    export_local_cmoney_file,
    collect_latest_screened_codes,
    CMoneyWatchlistClient,
    sync_stocks_to_cmoney,
    EXPORT_TXT_PATH
)


def test_load_env():
    """驗證環境變數讀取能力"""
    env = load_env()
    assert isinstance(env, dict)


def test_export_local_cmoney_file(tmp_path, monkeypatch):
    """驗證本地匯入檔生成格式"""
    test_file = tmp_path / "cmoney_test.txt"
    monkeypatch.setattr("cmoney_sync.EXPORT_TXT_PATH", test_file)

    codes = ["2330", "2317", "2454"]
    out_path = export_local_cmoney_file(codes)

    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    assert lines == ["2330", "2317", "2454"]


def test_collect_latest_screened_codes():
    """驗證從現有策略 result.csv 收集最新選股代碼"""
    codes = collect_latest_screened_codes()
    assert isinstance(codes, list)
    # 所有提取出的代碼必須為有效台股 4~6 碼數字
    for c in codes:
        assert c.isdigit()
        assert len(c) in (4, 5, 6)


def test_cmoney_client_not_configured(monkeypatch):
    """驗證未配置帳密時之安全診斷"""
    client = CMoneyWatchlistClient(account=None, password=None, token=None)
    monkeypatch.setattr(client, "account", None)
    monkeypatch.setattr(client, "password", None)
    monkeypatch.setattr(client, "token", None)

    assert client.is_configured() is False
    ok, msg = client.authenticate()
    assert ok is False
    assert "尚未於 .env 中配置" in msg


def test_sync_stocks_to_cmoney_fallback(tmp_path, monkeypatch):
    """驗證端到端同步入口之安全降級與剪貼簿保險"""
    test_file = tmp_path / "cmoney_fallback.txt"
    monkeypatch.setattr("cmoney_sync.EXPORT_TXT_PATH", test_file)

    test_codes = ["1316", "2385"]
    res = sync_stocks_to_cmoney(stock_codes=test_codes, verbose=False)

    assert res["total_codes"] == 2
    assert res["codes"] == ["1316", "2385"]
    assert res["clipboard_copied"] is True
    assert test_file.exists()
    assert "1316" in test_file.read_text(encoding="utf-8")
