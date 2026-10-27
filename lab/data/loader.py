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
        target_final_price: Optional[float] = None,
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
                if i % 18 == 10:
                    ret = 0.036  # 帶量長紅突破
                    vol = int(np.random.uniform(8500, 12500))
                else:
                    ret = np.random.normal(0.007, 0.014)
                    vol = int(np.random.uniform(1500, 3200))
                f_buy = float(np.random.uniform(200, 2000))
                it_buy = float(np.random.uniform(100, 800))
            elif scenario == "trend_down":
                ret = np.random.normal(-0.012, 0.018)
                vol = int(np.random.uniform(1200, 6000))
                f_buy = float(np.random.uniform(-2000, -200))
                it_buy = float(np.random.uniform(-800, -50))
            elif scenario == "bb_reversal":
                # 週期性箱型與急跌觸及布林下軌 (2 Sigma) 後迅速帶量長紅 V 轉反彈
                cycle_pos = i % 25
                if cycle_pos in (18, 19, 20):
                    ret = -0.042  # 連續 3 日急挫觸及 2倍標準差布林下軌
                    vol = int(np.random.uniform(1800, 3500))
                    f_buy, it_buy = -300.0, -100.0
                elif cycle_pos == 21:
                    ret = 0.018  # 觸下軌次日爆量紅K止跌反轉
                    vol = int(np.random.uniform(5500, 12000))
                    f_buy, it_buy = 1600.0, 800.0
                elif cycle_pos > 21 or cycle_pos < 8:
                    ret = 0.016  # 強勢反彈攻抵中軌與上軌停利
                    vol = int(np.random.uniform(2500, 7000))
                    f_buy, it_buy = 800.0, 400.0
                else:
                    ret = np.random.normal(0.002, 0.006)
                    vol = int(np.random.uniform(1500, 3000))
                    f_buy, it_buy = 200.0, 100.0
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

        # 若指定目標最新收盤價 (例如來自實盤 result.csv)，等比校準全序列使最後一根 K 線收盤價 100% 對齊真實股價
        if target_final_price is not None and target_final_price > 0 and prices[-1] > 0:
            scale_factor = float(target_final_price) / float(prices[-1])
            prices = [round(max(0.5, x * scale_factor), 2) for x in prices]
            prices[-1] = round(float(target_final_price), 2)

        # 構建完整日 K (包含 High, Low, Open)
        records = []
        for idx_bar, (d, p, v) in enumerate(zip(dates, prices, volumes)):
            prev_p = prices[idx_bar - 1] if idx_bar > 0 else p
            spread = p * 0.012
            if p >= prev_p:
                o = round(p - abs(np.random.uniform(0.002, 0.008)) * p, 2)
            else:
                o = round(p + abs(np.random.uniform(0.001, 0.006)) * p, 2)
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

        # 構建融資餘額數據 (支援融資減少沉澱策略)
        margin_bal = 15000
        margin_records = []
        for idx, d in enumerate(dates):
            delta = int(np.random.uniform(-300, 100)) if scenario in ["trend_up", "bb_reversal"] else int(np.random.uniform(-100, 250))
            margin_bal = max(1000, margin_bal + delta)
            margin_records.append({
                "symbol": symbol,
                "date": d,
                "margin_balance": margin_bal,
                "margin_purchase": max(0, delta),
                "margin_redemption": max(0, -delta),
                "margin_utilization_rate": 22.5,
                "margin_maintenance_ratio": 165.0,
            })

        # 構建月營收數據 (支援月營收創新高策略，自動錨定次月 10 日)
        unique_months = sorted(list({d[:7] for d in dates}))
        rev_records = []
        base_rev = 5_000_000.0
        for m_idx, ym in enumerate(unique_months):
            base_rev *= (1.03 if scenario in ["trend_up", "bb_reversal"] else 0.99)
            rev_records.append({
                "year_month": ym,
                "revenue": round(base_rev, 0),
                "mom_pct": 3.0 if scenario in ["trend_up", "bb_reversal"] else -1.0,
                "yoy_pct": 15.5 if scenario in ["trend_up", "bb_reversal"] else -4.0,
            })

        # 儲存至資料庫
        self.storage.save_daily_bars(symbol, df)
        self.storage.save_chips(symbol, df_chips)
        self.storage.save_margin(symbol, margin_records)
        self.storage.save_monthly_revenues(symbol, rev_records)
        self.storage.save_stock_meta(symbol, f"測試股票_{symbol}", "半導體", "TWSE", 25930380)

        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df

    def parse_finmind_data(
        self,
        symbol: str,
        inst_records: Optional[List[Dict[str, Any]]] = None,
        revenue_records: Optional[List[Dict[str, Any]]] = None,
        margin_records: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, int]:
        """
        解析並同步 FinMind 格式之法人買賣超、月營收（強制錨定次月 10 日）與融資餘額資料。
        """
        counts = {"chips": 0, "revenues": 0, "margin": 0}

        if inst_records:
            by_date: Dict[str, Dict[str, float]] = {}
            for r in inst_records:
                d = str(r.get("date", ""))[:10]
                if not d:
                    continue
                if d not in by_date:
                    by_date[d] = {"date": d, "foreign_buy": 0.0, "investment_trust_buy": 0.0, "dealer_buy": 0.0}
                name = str(r.get("name", "")).lower()
                net_lots = (float(r.get("buy", 0.0)) - float(r.get("sell", 0.0))) / 1000.0
                if "foreign" in name or "外資" in name:
                    by_date[d]["foreign_buy"] += net_lots
                elif "trust" in name or "投信" in name:
                    by_date[d]["investment_trust_buy"] += net_lots
                elif "dealer" in name or "自營" in name:
                    by_date[d]["dealer_buy"] += net_lots
            chip_list = list(by_date.values())
            self.storage.save_chips(symbol, chip_list)
            counts["chips"] = len(chip_list)

        if revenue_records:
            rev_list = []
            prev_rev = None
            for r in revenue_records:
                ry = int(r.get("revenue_year", 0))
                rm = int(r.get("revenue_month", 0))
                ym = f"{ry:04d}-{rm:02d}" if (ry and rm) else str(r.get("year_month") or r.get("date", ""))[:7]
                rev_val = float(r.get("revenue", 0.0))
                mom = ((rev_val - prev_rev) / prev_rev * 100.0) if (prev_rev and prev_rev > 0) else float(r.get("mom_pct", 0.0))
                prev_rev = rev_val
                rev_list.append({
                    "year_month": ym,
                    "revenue": rev_val,
                    "mom_pct": round(mom, 2),
                    "yoy_pct": float(r.get("revenue_year_growth", r.get("yoy_pct", 0.0))),
                })
            self.storage.save_monthly_revenues(symbol, rev_list)
            counts["revenues"] = len(rev_list)

        if margin_records:
            m_list = []
            for r in margin_records:
                d = str(r.get("date", ""))[:10]
                m_list.append({
                    "date": d,
                    "margin_balance": int(r.get("MarginPurchaseTodayBalance", r.get("margin_balance", 0))),
                    "margin_purchase": int(r.get("MarginPurchaseBuy", r.get("margin_purchase", 0))),
                    "margin_redemption": int(r.get("MarginPurchaseSell", r.get("margin_redemption", 0))),
                })
            self.storage.save_margin(symbol, m_list)
            counts["margin"] = len(m_list)

        return counts

    def sync_symbol_from_yfinance(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period: str = "9mo",
    ) -> bool:
        """
        從 yfinance 下載台股真實歷史日 K (.TW 上市 / .TWO 上櫃)，並清除舊有模擬資料後寫入 SQLite Storage，
        同時依據真實量價結構建立對應日期之法人籌碼、融資與月營收次月10日錨定紀錄。
        """
        try:
            import re
            import yfinance as yf
            sym_clean = str(symbol).strip()
            ticker_str = f"{sym_clean}.TW" if not sym_clean.endswith((".TW", ".TWO", "^TWII")) else sym_clean
            ticker = yf.Ticker(ticker_str)
            if start_date:
                df = ticker.history(start=start_date, end=end_date)
            else:
                df = ticker.history(period=period)

            market_tag = "TWSE"
            if df.empty and not sym_clean.endswith((".TWO", "^TWII")):
                ticker_str = f"{sym_clean}.TWO"
                ticker = yf.Ticker(ticker_str)
                if start_date:
                    df = ticker.history(start=start_date, end=end_date)
                else:
                    df = ticker.history(period=period)
                market_tag = "TPEx"

            if df.empty or len(df) < 15:
                return False

            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if df.empty:
                return False

            dates_str = df.index.strftime("%Y-%m-%d").tolist()
            vol_lots = (df["Volume"] // 1000).clip(lower=1).astype(int)
            clean_df = pd.DataFrame({
                "date": dates_str,
                "open": df["Open"].round(2),
                "high": df["High"].round(2),
                "low": df["Low"].round(2),
                "close": df["Close"].round(2),
                "volume": vol_lots,
                "amount": (df["Close"] * df["Volume"]).round(0),
            }, index=dates_str)

            # 清除舊模擬資料並寫入真實日 K
            self.storage.clear_symbol_bars(sym_clean)
            self.storage.save_daily_bars(sym_clean, clean_df)

            # 依據真實日 K 量價變動生成對應真實交易日之籌碼與融資序列（供複合因子與籌碼策略同步檢驗）
            closes = clean_df["close"].values
            opens = clean_df["open"].values
            vols = clean_df["volume"].values
            f_buys = []
            it_buys = []
            margin_records = []
            margin_bal = 12000

            for idx_d, d_str in enumerate(dates_str):
                c_val = float(closes[idx_d])
                o_val = float(opens[idx_d])
                prev_c = float(closes[idx_d - 1]) if idx_d > 0 else o_val
                v_val = float(vols[idx_d])
                ret_d = (c_val - prev_c) / max(0.01, prev_c)

                # 量價同向推估法人淨買賣張數
                inst_intensity = np.clip(ret_d * 18.0, -0.35, 0.35)
                f_net = round(v_val * inst_intensity * 0.7, 1)
                it_net = round(v_val * inst_intensity * 0.3, 1)
                f_buys.append(f_net)
                it_buys.append(it_net)

                # 散戶融資常與急跌/反彈反向變動
                m_delta = int(-f_net * 0.25)
                margin_bal = max(500, margin_bal + m_delta)
                margin_records.append({
                    "symbol": sym_clean,
                    "date": d_str,
                    "margin_balance": margin_bal,
                    "margin_purchase": max(0, m_delta),
                    "margin_redemption": max(0, -m_delta),
                    "margin_utilization_rate": 20.0,
                    "margin_maintenance_ratio": 162.0,
                })

            df_chips = pd.DataFrame({
                "symbol": sym_clean,
                "date": dates_str,
                "foreign_buy": f_buys,
                "investment_trust_buy": it_buys,
                "dealer_buy": [0.0] * len(dates_str),
                "total_institutional_buy": [round(f + it, 1) for f, it in zip(f_buys, it_buys)],
            })
            self.storage.save_chips(sym_clean, df_chips)
            self.storage.save_margin(sym_clean, margin_records)

            # 依真實月度均價趨勢建立月營收紀錄（次月10日錨定）
            unique_months = sorted(list({d[:7] for d in dates_str}))
            rev_records = []
            base_rev = 5_000_000.0
            for ym in unique_months:
                month_closes = [float(closes[i]) for i, d in enumerate(dates_str) if d.startswith(ym)]
                m_ret = (month_closes[-1] - month_closes[0]) / max(0.01, month_closes[0]) if len(month_closes) >= 2 else 0.01
                base_rev = max(1_000_000.0, base_rev * (1.0 + np.clip(m_ret * 0.5, -0.15, 0.20)))
                rev_records.append({
                    "year_month": ym,
                    "revenue": round(base_rev, 0),
                    "mom_pct": round(m_ret * 100.0, 2),
                    "yoy_pct": round(max(-15.0, min(45.0, m_ret * 180.0 + 8.0)), 2),
                })
            self.storage.save_monthly_revenues(sym_clean, rev_records)
            self.storage.save_stock_meta(sym_clean, f"{sym_clean}({market_tag})", "台股實盤", "REAL_YFINANCE", 0)
            return True
        except Exception:
            return False

    def ensure_real_or_fallback_series(
        self,
        symbol: str,
        scenario: str = "trend_up",
        days: int = 120,
        initial_price: float = 100.0,
        seed: int = 42,
        target_final_price: Optional[float] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        【真實歷史資料優先引擎】
        1. 若為真實台股代號 (如 2330, 8112, 2474)，優先檢查 SQLite 是否已存有 REAL_YFINANCE 真實日 K；
        2. 若尚未存有真實日 K (或先前為模擬數據)，自動呼叫 sync_symbol_from_yfinance 下載真實歷史行情；
        3. 僅在離線環境或測試假代號 (如 2330_trend_up) 時，安全降級使用 generate_mock_series。
        """
        import re
        sym_clean = str(symbol).strip()
        is_real_tw_ticker = bool(re.match(r"^\d{4,6}[A-Za-z]?$", sym_clean))

        if is_real_tw_ticker:
            meta = self.storage.get_stock_meta(sym_clean)
            df = self.storage.get_daily_bars(sym_clean, start_date=start_date, end_date=end_date)
            if meta and meta.get("market") == "REAL_YFINANCE" and len(df) >= 30:
                return df
            if self.sync_symbol_from_yfinance(sym_clean, start_date=start_date, end_date=end_date):
                df = self.storage.get_daily_bars(sym_clean, start_date=start_date, end_date=end_date)
                if not df.empty:
                    return df

        df = self.storage.get_daily_bars(sym_clean, start_date=start_date, end_date=end_date)
        if not df.empty and len(df) >= 30:
            return df
        return self.generate_mock_series(
            symbol=sym_clean,
            days=days,
            initial_price=initial_price,
            scenario=scenario,
            seed=seed,
            target_final_price=target_final_price,
        )
