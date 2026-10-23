"""
單元測試: 台股量化策略實驗室 - 數據層持久化 (test_lab_data.py)
檢驗 SQLite WAL 模式、表格結構、批次寫入與去重更新。
"""

import os
import tempfile
import pytest
import pandas as pd

from lab.data.storage import MarketStorage
from lab.data.loader import MarketDataLoader


@pytest.fixture
def temp_storage():
    """建立臨時 SQLite 資料庫以利測試隔離"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_path = f.name
    storage = MarketStorage(db_path=temp_path)
    yield storage
    # 清理臨時檔案
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        wal_path = temp_path + "-wal"
        shm_path = temp_path + "-shm"
        if os.path.exists(wal_path):
            os.remove(wal_path)
        if os.path.exists(shm_path):
            os.remove(shm_path)
    except Exception:
        pass


def test_db_initialization_and_wal_mode(temp_storage):
    """斷言 1: 資料庫初始化、表格建立與 WAL 模式開啟"""
    with temp_storage._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal", f"預期 WAL 模式，實際為 {mode}"

        # 檢驗核心表格是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        assert "daily_bars" in tables
        assert "chips" in tables
        assert "margin_balances" in tables
        assert "stock_meta" in tables


def test_batch_write_and_range_query(temp_storage):
    """斷言 2: 批次寫入 120 筆 K 線行情與日期區間查詢精確匹配"""
    loader = MarketDataLoader(storage=temp_storage)
    symbol = "2330"
    df = loader.generate_mock_series(symbol=symbol, start_date="2024-01-01", days=60)
    assert len(df) == 60

    # 從資料庫取出
    queried_df = temp_storage.get_daily_bars(symbol=symbol)
    assert len(queried_df) == 60
    assert "close" in queried_df.columns
    assert "volume" in queried_df.columns

    # 檢驗區間過濾
    start_d = df.index[10].strftime("%Y-%m-%d")
    end_d = df.index[20].strftime("%Y-%m-%d")
    subset_df = temp_storage.get_daily_bars(symbol=symbol, start_date=start_d, end_date=end_d)
    assert len(subset_df) == 11
    assert subset_df.index[0].strftime("%Y-%m-%d") == start_d
    assert subset_df.index[-1].strftime("%Y-%m-%d") == end_d


def test_upsert_deduplication(temp_storage):
    """斷言 3: 重複寫入相同日期數據時觸發 REPLACE，筆數不重複且以最新為準"""
    symbol = "2454"
    date_str = "2024-05-10"
    init_data = [{"date": date_str, "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 1500}]
    temp_storage.save_daily_bars(symbol, init_data)

    df_first = temp_storage.get_daily_bars(symbol)
    assert len(df_first) == 1
    assert float(df_first.iloc[0]["close"]) == 102.0

    # 更新同日收盤價為 108.0
    updated_data = [{"date": date_str, "open": 100.0, "high": 110.0, "low": 99.0, "close": 108.0, "volume": 2000}]
    temp_storage.save_daily_bars(symbol, updated_data)

    df_second = temp_storage.get_daily_bars(symbol)
    assert len(df_second) == 1  # 依然只有 1 筆，不重複
    assert float(df_second.iloc[0]["close"]) == 108.0
    assert int(df_second.iloc[0]["volume"]) == 2000


def test_storage_in_memory_cache(temp_storage):
    """斷言 4: 記憶體快取命中與更新自動失效"""
    symbol = "2317"
    data = [{"date": "2024-01-02", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 5000}]
    temp_storage.save_daily_bars(symbol, data)

    # 第一次查詢，寫入快取
    df1 = temp_storage.get_daily_bars(symbol)
    cache_key = f"bars_{symbol}_None_None"
    assert cache_key in temp_storage._cache

    # 第二次查詢，命中快取
    df2 = temp_storage.get_daily_bars(symbol)
    assert len(df2) == 1

    # 寫入新資料時，快取自動失效
    temp_storage.save_daily_bars(symbol, [{"date": "2024-01-03", "open": 101.0, "high": 104.0, "low": 100.0, "close": 103.0, "volume": 6000}])
    assert cache_key not in temp_storage._cache
