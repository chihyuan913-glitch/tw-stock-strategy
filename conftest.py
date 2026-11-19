#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pytest 全域配置 (conftest.py)
自動將專案根目錄注入 sys.path，確保全套單元測試無論在何處執行皆能順利引用根目錄與子模組。
"""

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest.fixture(autouse=True)
def _isolate_web_trigger_paths(monkeypatch, tmp_path):
    """將 web_trigger_once 的狀態與日誌輸出隔離至 tmp_path，避免污染專案 cache/。"""
    try:
        import run_all_intraday
    except ImportError:
        return
    monkeypatch.setattr(
        run_all_intraday,
        "WEB_TRIGGER_STATUS_PATH",
        tmp_path / "web_trigger_status.json",
        raising=False,
    )
    monkeypatch.setattr(
        run_all_intraday,
        "WEB_TRIGGER_LOG_PATH",
        tmp_path / "web_trigger_timeout.log",
        raising=False,
    )
