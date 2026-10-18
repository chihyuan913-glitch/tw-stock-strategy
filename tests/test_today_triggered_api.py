#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
單元測試：儀表板當日觸及到價與新增推播焦點欄位 API (test_today_triggered_api.py)
驗證 /api/radar/watchlist 與 /api/radar/triggered_today 的資料合約、
當日觸發判定準確性（只置放當日觸發標的）與欄位完整性。
"""

import sys
import pytest
from pathlib import Path

# 將專案根目錄加入路徑
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from line_bot_server import get_radar_watchlist_endpoint, get_radar_triggered_today_endpoint


@pytest.mark.anyio
async def test_radar_watchlist_endpoint_contract():
    """驗證 /api/radar/watchlist 端點資料結構與欄位注入"""
    res = await get_radar_watchlist_endpoint()
    assert res["status"] == "ok"
    assert "count" in res
    assert "today_triggered_count" in res
    assert "today_triggered_items" in res
    assert "items" in res
    assert isinstance(res["today_triggered_items"], list)
    assert len(res["today_triggered_items"]) == res["today_triggered_count"]

    # 檢查每個標的的注入欄位
    for it in res["items"]:
        assert "triggered_today" in it
        assert isinstance(it["triggered_today"], bool)
        assert "today_events" in it
        assert isinstance(it["today_events"], list)
        assert "today_push_time" in it
        assert "is_new_today" in it
        assert "diff_from_entry" in it
        assert "diff_pct_from_entry" in it


@pytest.mark.anyio
async def test_today_triggered_isolation_rule():
    """驗證專屬欄位嚴格隔離規則：只置放當天有觸發或推播的個股"""
    res = await get_radar_watchlist_endpoint()
    today_items = res["today_triggered_items"]

    # 驗證所有在 today_triggered_items 中的標的，其 triggered_today 必須全為 True
    for it in today_items:
        assert it.get("triggered_today") is True
        # 必須具備今日事件或今日推播紀錄
        has_events = len(it.get("today_events", [])) > 0
        has_push = bool(it.get("today_push_time"))
        assert has_events or has_push, f"標的 {it.get('code')} 缺乏今日觸發證據"

    # 反向驗證：未被標記為 triggered_today 的標的，絕不得出現在 today_triggered_items
    non_triggered_codes = {it["code"] for it in res["items"] if not it.get("triggered_today")}
    today_codes = {it["code"] for it in today_items}
    intersection = non_triggered_codes.intersection(today_codes)
    assert len(intersection) == 0, f"非當日觸發標的誤入專屬焦點欄位: {intersection}"


@pytest.mark.anyio
async def test_dedicated_triggered_today_endpoint():
    """驗證專屬端點 /api/radar/triggered_today 的回應一致性"""
    res_all = await get_radar_watchlist_endpoint()
    res_today = await get_radar_triggered_today_endpoint()

    assert res_today["status"] == "ok"
    assert res_today["count"] == res_all["today_triggered_count"]
    assert len(res_today["items"]) == res_all["today_triggered_count"]

    codes_all = [it["code"] for it in res_all["today_triggered_items"]]
    codes_today = [it["code"] for it in res_today["items"]]
    assert codes_all == codes_today
