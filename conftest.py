#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pytest 全域配置 (conftest.py)
自動將專案根目錄注入 sys.path，確保全套單元測試無論在何處執行皆能順利引用根目錄與子模組。
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
