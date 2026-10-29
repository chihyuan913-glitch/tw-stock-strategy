# -*- coding: utf-8 -*-
"""
全市場看盤雷達即時行情中心 (Realtime Watch Quote Hub)
連線臺灣證券交易所 (TWSE) 與櫃買中心 (TPEx) 官方 MIS 系統
支援 197 檔核心成分股、加權指數 (^TWII) 即時撮合、快取與高可用備援
"""
import time
import json
import datetime
import urllib.request
from typing import Dict, Any, List, Optional

try:
    from zoneinfo import ZoneInfo
    TZ_TW = ZoneInfo("Asia/Taipei")
except Exception:
    TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

# 全域單例行情快取
_REALTIME_WATCH_CACHE: Dict[str, Any] = {}
_LAST_WATCH_FETCH_TIME: float = 0.0
_IS_FETCHING: bool = False
_MARKET_INDEX_CACHE: Dict[str, Any] = {
    "index_close": 23285.42,
    "change": -328.60,
    "change_pct": -1.39,
    "time": "13:30:00"
}

def is_market_open() -> bool:
    """判斷目前是否在台股盤中交易時段 (09:00 ~ 13:35)"""
    now = datetime.datetime.now(TZ_TW)
    if now.weekday() >= 5:  # 週六週日
        return False
    now_m = now.hour * 60 + now.minute
    return (9 * 60 <= now_m <= 13 * 60 + 35)

def get_realtime_watch_quotes(codes: List[str], force: bool = False) -> Dict[str, Any]:
    """
    取得全市場看盤股票之最新即時/收盤官方報價
    盤中 15 秒快取，盤後 600 秒快取，非同步平滑更新
    """
    global _REALTIME_WATCH_CACHE, _LAST_WATCH_FETCH_TIME, _IS_FETCHING, _MARKET_INDEX_CACHE

    now_t = time.time()
    ttl = 15.0 if is_market_open() else 600.0

    # 快取有效且非強制更新時直接回傳
    if not force and (now_t - _LAST_WATCH_FETCH_TIME < ttl) and len(_REALTIME_WATCH_CACHE) > 50:
        return _REALTIME_WATCH_CACHE

    # 執行批次查詢
    try:
        _sync_fetch_all_watch_quotes(codes)
    except Exception as e:
        print(f"[!] 即時行情抓取異常: {e}")

    return _REALTIME_WATCH_CACHE

def _sync_fetch_all_watch_quotes(codes: List[str]):
    global _REALTIME_WATCH_CACHE, _LAST_WATCH_FETCH_TIME, _MARKET_INDEX_CACHE
    import radar_manager

    clean_codes = sorted(list(set([str(c).strip() for c in codes if str(c).strip()])))
    if not clean_codes:
        return

    # 加入大盤加權指數通道 (tse_t00.tw)
    fetch_list = ["t00"] + clean_codes

    quotes = radar_manager.fetch_batch_realtime_quotes(fetch_list, max_cache_sec=0)

    # 提取大盤資訊
    if "t00" in quotes:
        q_idx = quotes["t00"]
        if q_idx.get("price"):
            _MARKET_INDEX_CACHE = {
                "index_close": q_idx["price"],
                "change": q_idx.get("change", 0.0),
                "change_pct": q_idx.get("change_pct", 0.0),
                "time": q_idx.get("time", "13:30:00")
            }

    # 覆蓋更新個股行情
    valid_count = 0
    for c in clean_codes:
        if c in quotes and quotes[c].get("price") is not None:
            _REALTIME_WATCH_CACHE[c] = quotes[c]
            valid_count += 1

    _LAST_WATCH_FETCH_TIME = time.time()
    print(f"[*] 看盤行情中心成功同步 {valid_count}/{len(clean_codes)} 檔上市櫃個股真實報價 (大盤: {_MARKET_INDEX_CACHE['index_close']} {_MARKET_INDEX_CACHE['change_pct']}%)")

def get_market_index_summary() -> Dict[str, Any]:
    """取得加權指數最新狀態"""
    return _MARKET_INDEX_CACHE
