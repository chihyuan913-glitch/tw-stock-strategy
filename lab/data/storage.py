"""
MarketStorage: 台股量化實驗室嵌入式資料庫驅動 (SQLite WAL Mode)
嚴格落實防碎片與雲端硬碟規範：單一資料庫檔案，加強索引與零碎檔案抑制。
"""

import os
import sqlite3
from typing import List, Dict, Any, Optional, Union
import pandas as pd


class MarketStorage:
    def __init__(self, db_path: Optional[str] = None):
        """
        初始化資料庫連接與結構。
        預設存於 lab/data/tw_market.db
        """
        if db_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.db_path = os.path.join(base_dir, "tw_market.db")
        else:
            self.db_path = db_path
            
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._cache: Dict[str, pd.DataFrame] = {}
        self._init_database()

    def clear_cache(self):
        """清除記憶體快取"""
        self._cache.clear()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # 開啟 WAL 模式與效能調校
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA cache_size=-64000;")  # 64MB Cache
        return conn

    def _init_database(self):
        """建立核心表格結構與索引"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. 日 K 線行情表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                amount REAL DEFAULT 0.0,
                PRIMARY KEY (symbol, date)
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bars_symbol_date ON daily_bars (symbol, date);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bars_date ON daily_bars (date);")

            # 2. 三大法人籌碼表 (外資、投信、自營商買賣超，單位：張)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS chips (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                foreign_buy REAL DEFAULT 0.0,
                investment_trust_buy REAL DEFAULT 0.0,
                dealer_buy REAL DEFAULT 0.0,
                total_institutional_buy REAL DEFAULT 0.0,
                PRIMARY KEY (symbol, date)
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chips_symbol_date ON chips (symbol, date);")

            # 3. 信用交易與融資表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS margin_balances (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                margin_balance INTEGER DEFAULT 0,
                margin_purchase INTEGER DEFAULT 0,
                margin_redemption INTEGER DEFAULT 0,
                margin_utilization_rate REAL DEFAULT 0.0,
                margin_maintenance_ratio REAL DEFAULT 160.0,
                PRIMARY KEY (symbol, date)
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_margin_symbol_date ON margin_balances (symbol, date);")

            # 4. 個股基本資料元數據
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_meta (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT DEFAULT '',
                market TEXT DEFAULT 'TWSE',
                shares_issued INTEGER DEFAULT 0,
                updated_at TEXT
            );
            """)
            conn.commit()

    def save_daily_bars(self, symbol: str, data: Union[pd.DataFrame, List[Dict[str, Any]]]):
        """
        批次寫入日 K 線數據 (INSERT OR REPLACE)
        """
        if isinstance(data, pd.DataFrame):
            df = data.copy()
            if "date" not in df.columns and isinstance(df.index, pd.DatetimeIndex):
                df["date"] = df.index.strftime("%Y-%m-%d")
            records = df.to_dict(orient="records")
        else:
            records = data

        if not records:
            return

        insert_sql = """
        INSERT OR REPLACE INTO daily_bars (symbol, date, open, high, low, close, volume, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                symbol,
                str(r["date"])[:10],
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                int(r.get("volume", 0)),
                float(r.get("amount", 0.0)),
            )
            for r in records
        ]

        with self._get_connection() as conn:
            conn.executemany(insert_sql, rows)
            conn.commit()

        # 失效該標的之快取
        keys_to_del = [k for k in self._cache if k.startswith(f"bars_{symbol}_")]
        for k in keys_to_del:
            self._cache.pop(k, None)

    def get_daily_bars(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        查詢特定標的在指定日期區間之日 K 線 (含記憶體快取加速)
        """
        cache_key = f"bars_{symbol}_{start_date}_{end_date}"
        if cache_key in self._cache:
            return self._cache[cache_key].copy()

        query = "SELECT date, open, high, low, close, volume, amount FROM daily_bars WHERE symbol = ?"
        params: List[Any] = [symbol]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date ASC"

        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)

        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            
        self._cache[cache_key] = df
        return df.copy()

    def get_benchmark_series(
        self,
        symbol: str = "^TWII",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[pd.Series]:
        """
        取得大盤加權指數收盤序列
        """
        df = self.get_daily_bars(symbol, start_date=start_date, end_date=end_date)
        if not df.empty and "close" in df.columns:
            return df["close"]
        return None

    def save_chips(self, symbol: str, data: Union[pd.DataFrame, List[Dict[str, Any]]]):
        """
        批次寫入三大法人籌碼數據
        """
        if isinstance(data, pd.DataFrame):
            df = data.copy()
            if "date" not in df.columns and isinstance(df.index, pd.DatetimeIndex):
                df["date"] = df.index.strftime("%Y-%m-%d")
            records = df.to_dict(orient="records")
        else:
            records = data

        if not records:
            return

        insert_sql = """
        INSERT OR REPLACE INTO chips (
            symbol, date, foreign_buy, investment_trust_buy, dealer_buy, total_institutional_buy
        ) VALUES (?, ?, ?, ?, ?, ?)
        """
        rows = []
        for r in records:
            f_buy = float(r.get("foreign_buy", 0.0))
            it_buy = float(r.get("investment_trust_buy", 0.0))
            d_buy = float(r.get("dealer_buy", 0.0))
            total = float(r.get("total_institutional_buy", f_buy + it_buy + d_buy))
            rows.append((symbol, str(r["date"])[:10], f_buy, it_buy, d_buy, total))

        with self._get_connection() as conn:
            conn.executemany(insert_sql, rows)
            conn.commit()

    def get_chips(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        查詢三大法人籌碼
        """
        query = (
            "SELECT date, foreign_buy, investment_trust_buy, dealer_buy, total_institutional_buy "
            "FROM chips WHERE symbol = ?"
        )
        params: List[Any] = [symbol]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date ASC"

        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)

        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
        return df

    def save_margin(self, symbol: str, data: Union[pd.DataFrame, List[Dict[str, Any]]]):
        """
        批次寫入融資融券數據
        """
        if isinstance(data, pd.DataFrame):
            df = data.copy()
            if "date" not in df.columns and isinstance(df.index, pd.DatetimeIndex):
                df["date"] = df.index.strftime("%Y-%m-%d")
            records = df.to_dict(orient="records")
        else:
            records = data

        if not records:
            return

        insert_sql = """
        INSERT OR REPLACE INTO margin_balances (
            symbol, date, margin_balance, margin_purchase, margin_redemption,
            margin_utilization_rate, margin_maintenance_ratio
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                symbol,
                str(r["date"])[:10],
                int(r.get("margin_balance", 0)),
                int(r.get("margin_purchase", 0)),
                int(r.get("margin_redemption", 0)),
                float(r.get("margin_utilization_rate", 0.0)),
                float(r.get("margin_maintenance_ratio", 160.0)),
            )
            for r in records
        ]

        with self._get_connection() as conn:
            conn.executemany(insert_sql, rows)
            conn.commit()

    def get_margin(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        query = (
            "SELECT date, margin_balance, margin_purchase, margin_redemption, "
            "margin_utilization_rate, margin_maintenance_ratio FROM margin_balances WHERE symbol = ?"
        )
        params: List[Any] = [symbol]
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        query += " ORDER BY date ASC"

        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)

        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
        return df

    def save_stock_meta(self, symbol: str, name: str, category: str = "", market: str = "TWSE", shares_issued: int = 0):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO stock_meta (symbol, name, category, market, shares_issued, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (symbol, name, category, market, shares_issued),
            )
            conn.commit()

    def get_stock_meta(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, name, category, market, shares_issued, updated_at FROM stock_meta WHERE symbol = ?", (symbol,))
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    def get_all_symbols(self) -> List[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol")
            return [r[0] for r in cursor.fetchall()]

    def get_latest_date(self, symbol: str) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(date) FROM daily_bars WHERE symbol = ?", (symbol,))
            res = cursor.fetchone()
            return res[0] if res and res[0] else None
