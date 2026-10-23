"""
MarketDataLoader: 歷史數據載入、清洗與模擬產生器
支援從外部 (Yahoo / TWSE) 增量獲取數據並轉入 Storage，亦提供嚴密受控的數理測試行情生成器。
"""

import math
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import numpy as np
import pandas as pd

from .storage import MarketStorage


class MarketDataLoader:
    def __init__(self, storage: Optional[MarketStorage] = None):
        self.storage = storage or MarketStorage()

    def generate_mock_series(
        self,
        symbol: str = "2330",
        start_date: str = "2024-01-01",
        days: int = 120,
        initial_price: float = 100.0,
        scenario: str = "trend_up",
        seed: int = 42,
    ) -> pd.DataFrame:
        """
        生成具備台股真實特徵（10% 漲跌幅限制、整數張數量能、籌碼特徵）的標準測試行情序列。

        scenario 支援:
        - 'trend_up': 多頭起漲 (適合法人起漲、突破加碼與 TP 測試)
        - 'trend_down': 空頭重挫 (適合停損、做空破線測試)
        - 'bb_reversal': 布林下軌超跌急拉 V 轉 (適合超跌反彈測試)
        - 'limit_up_trap': 漲停鎖死 (開盤即漲停，驗證不允許無效買入)
        - 'limit_down_trap': 跌停鎖死 (開盤即跌停，驗證真實流動性停損延後)
        - 'chop': 窄幅箱型震盪
        """
        np.random.seed(seed)
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")

        dates = []
        cur_dt = start_dt
        while len(dates) < days:
            if cur_dt.weekday() < 5:  # 週一至週五
                dates.append(cur_dt.strftime("%Y-%m-%d"))
            cur_dt += timedelta(days=1)

        prices = [initial_price]
        volumes = []
        foreign_buys = []
        it_buys = []

        for i in range(1, len(dates)):
            prev = prices[-1]
            if scenario == "trend_up":
                ret = np.random.normal(0.008, 0.015)
                vol = int(np.random.uniform(1500, 8000))
                f_buy = float(np.random.uniform(200, 2000))
                it_buy = float(np.random.uniform(100, 800))
            elif scenario == "trend_down":
                ret = np.random.normal(-0.012, 0.018)
                vol = int(np.random.uniform(1200, 6000))
                f_buy = float(np.random.uniform(-2000, -200))
                it_buy = float(np.random.uniform(-800, -50))
            elif scenario == "bb_reversal":
                # 前半段下跌跌破軌道，中段見底出大量長下影，後半段反彈
                mid = len(dates) // 2
                if i < mid - 5:
                    ret = -0.02
                    vol = int(np.random.uniform(1000, 2500))
                    f_buy, it_buy = -500.0, -200.0
                elif i <= mid:
                    ret = 0.01
                    vol = int(np.random.uniform(5000, 12000))  # 爆量止跌
                    f_buy, it_buy = 1500.0, 800.0  # 法人逆勢吃貨
                else:
                    ret = 0.015
                    vol = int(np.random.uniform(2500, 7000))
                    f_buy, it_buy = 800.0, 400.0
            elif scenario == "limit_up_trap":
                ret = 0.10 if i == 5 else np.random.normal(0.002, 0.005)
                vol = 50 if i == 5 else int(np.random.uniform(1500, 4000))
                f_buy, it_buy = 0.0, 0.0
            elif scenario == "limit_down_trap":
                ret = -0.10 if i == 5 else np.random.normal(-0.002, 0.005)
                vol = 30 if i == 5 else int(np.random.uniform(1500, 4000))
                f_buy, it_buy = -100.0, -50.0
            else:  # chop
                ret = np.random.normal(0.0, 0.01)
                vol = int(np.random.uniform(1000, 3000))
                f_buy, it_buy = 50.0, -30.0

            # 嚴格台股 10% 漲跌幅上限限制
            ret = max(-0.10, min(0.10, ret))
            new_p = round(prev * (1.0 + ret), 2)
            prices.append(new_p)
            volumes.append(vol)
            foreign_buys.append(f_buy)
            it_buys.append(it_buy)

        volumes.insert(0, 2000)
        foreign_buys.insert(0, 100.0)
        it_buys.insert(0, 50.0)

        # 構建完整日 K (包含 High, Low, Open)
        records = []
        for d, p, v in zip(dates, prices, volumes):
            spread = p * 0.012
            o = round(p - np.random.uniform(-spread, spread) * 0.5, 2)
            h = round(max(p, o) + abs(np.random.uniform(0, spread)), 2)
            l = round(min(p, o) - abs(np.random.uniform(0, spread)), 2)
            # 確保不會破壞高低價
            h = max(h, o, p)
            l = min(l, o, p)
            records.append({
                "symbol": symbol,
                "date": d,
                "open": o,
                "high": h,
                "low": l,
                "close": p,
                "volume": v,
                "amount": round(p * v * 1000, 0),
            })

        df = pd.DataFrame(records)
        df_chips = pd.DataFrame({
            "symbol": symbol,
            "date": dates,
            "foreign_buy": foreign_buys,
            "investment_trust_buy": it_buys,
            "dealer_buy": [0.0] * len(dates),
            "total_institutional_buy": [f + it for f, it in zip(foreign_buys, it_buys)],
        })

        # 儲存至資料庫
        self.storage.save_daily_bars(symbol, df)
        self.storage.save_chips(symbol, df_chips)
        self.storage.save_stock_meta(symbol, f"測試股票_{symbol}", "半導體", "TWSE", 25930380)

        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df

    def sync_symbol_from_yfinance(
        self, symbol: str, start_date: str = "2023-01-01", end_date: Optional[str] = None
    ) -> bool:
        """
        從 yfinance 增量下載並寫入 Storage
        """
        try:
            import yfinance as yf
            ticker_str = f"{symbol}.TW" if not symbol.endswith((".TW", ".TWO")) else symbol
            ticker = yf.Ticker(ticker_str)
            df = ticker.history(start=start_date, end=end_date)
            if df.empty and not symbol.endswith(".TWO"):
                ticker_str = f"{symbol}.TWO"
                ticker = yf.Ticker(ticker_str)
                df = ticker.history(start=start_date, end=end_date)

            if df.empty:
                return False

            clean_df = pd.DataFrame({
                "open": df["Open"].round(2),
                "high": df["High"].round(2),
                "low": df["Low"].round(2),
                "close": df["Close"].round(2),
                "volume": (df["Volume"] // 1000).astype(int),  # 轉為張數
                "amount": (df["Close"] * df["Volume"]).round(0),
            }, index=df.index.strftime("%Y-%m-%d"))

            self.storage.save_daily_bars(symbol, clean_df)
            return True
        except Exception:
            return False
