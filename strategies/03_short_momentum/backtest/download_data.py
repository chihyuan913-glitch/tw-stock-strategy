#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略三：台股弱勢破線做空與股票期貨避險策略 歷史回測數據下載器
下載區間：2024-11-01 至 2026-10-01 (含暖機期，回測核心為 2025-01-01 至 2026-09-30)

數據內容：
1. 期交所股票期貨標的清單 (252 檔標的)
2. Yahoo Finance 歷史日K OHLCV 股價數據 (存為 ohlcv_futures.pkl)
3. FinMind 三大法人歷史買賣超數據 (存入 chips_futures.db)
"""

import os
import sys
import time
import json
import sqlite3
import random
import requests
import concurrent.futures
import pandas as pd
import yfinance as yf

# 設定標準輸出編碼為 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STRATEGY_DIR = os.path.dirname(BASE_DIR)
FUTURES_JSON_PATH = os.path.join(STRATEGY_DIR, "stock_futures_list.json")
DB_PATH = os.path.join(BASE_DIR, "chips_futures.db")
OHLCV_PATH = os.path.join(BASE_DIR, "ohlcv_futures.pkl")

START_DATE = "2024-11-01"
END_DATE = "2026-10-01"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chips (
            date TEXT,
            code TEXT,
            foreign_net INTEGER,
            trust_net INTEGER,
            dealer_net INTEGER,
            total_net INTEGER,
            PRIMARY KEY (date, code)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_status (
            code TEXT PRIMARY KEY,
            status TEXT,
            rows_count INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chips_date ON chips(date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chips_code ON chips(code)")
    conn.commit()
    conn.close()

def load_futures_stocks():
    if not os.path.exists(FUTURES_JSON_PATH):
        raise FileNotFoundError(f"找不到期貨標的名單: {FUTURES_JSON_PATH}")
    with open(FUTURES_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"[✓] 成功載入期交所股票期貨標的清單，共 {len(data)} 檔")
    return sorted(list(data.keys())), data

def download_ohlcv_batch(codes):
    if os.path.exists(OHLCV_PATH):
        print(f"[✓] OHLCV 歷史股價已存在: {OHLCV_PATH}，跳過下載")
        return pd.read_pickle(OHLCV_PATH)

    print(f"[*] 開始從 Yahoo Finance 下載 {len(codes)} 檔股票歷史日K ({START_DATE} ~ {END_DATE})...")
    tickers = [f"{c}.TW" for c in codes]
    chunk_size = 40
    all_chunks = []

    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        print(f"[*] 下載進度: {i + 1} ~ {min(i + chunk_size, len(tickers))} / {len(tickers)}...")
        for attempt in range(3):
            try:
                df = yf.download(chunk, start=START_DATE, end=END_DATE, auto_adjust=False, progress=False)
                if not df.empty:
                    all_chunks.append(df)
                    break
            except Exception as e:
                print(f"[!] 批次下載異常: {e}，重試中...")
                time.sleep(2)
        time.sleep(1)

    print("[*] 合併股價數據中...")
    full_df = pd.concat(all_chunks, axis=1)
    full_df.to_pickle(OHLCV_PATH)
    print(f"[✓] 成功儲存 OHLCV 股價數據至 {OHLCV_PATH}，形狀: {full_df.shape}")
    return full_df

def fetch_single_stock_chips(code):
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "data_id": code,
        "start_date": START_DATE,
        "end_date": "2026-09-30"
    }
    
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=12)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if not data:
                    return code, "EMPTY", []
                
                # 彙總成每日各法人買賣超 (股數)
                daily_agg = {}
                for row in data:
                    d_str = row["date"].replace("-", "")
                    name = row["name"]
                    net = row["buy"] - row["sell"]
                    
                    if d_str not in daily_agg:
                        daily_agg[d_str] = {"foreign": 0, "trust": 0, "dealer": 0, "total": 0}
                    
                    daily_agg[d_str]["total"] += net
                    if name in ("Foreign_Investor", "Foreign_Dealer_Self"):
                        daily_agg[d_str]["foreign"] += net
                    elif name == "Investment_Trust":
                        daily_agg[d_str]["trust"] += net
                    elif name in ("Dealer_self", "Dealer_Hedging"):
                        daily_agg[d_str]["dealer"] += net
                
                rows = []
                for d_str, v in daily_agg.items():
                    rows.append((d_str, code, v["foreign"], v["trust"], v["dealer"], v["total"]))
                
                return code, "OK", rows
            elif resp.status_code in (429, 400):
                time.sleep(2.0 + attempt * 2)
            else:
                time.sleep(1.0)
        except Exception:
            time.sleep(1.5)
            
    return code, "ERROR", []

def download_all_chips(codes):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT code FROM stock_status WHERE status = 'OK'")
    done_codes = set(r[0] for r in cur.fetchall())
    conn.close()

    pending_codes = [c for c in codes if c not in done_codes]
    print(f"[*] 股票期貨標的共 {len(codes)} 檔，已完成籌碼: {len(done_codes)} 檔，待下載: {len(pending_codes)} 檔")

    if not pending_codes:
        print("[✓] 所有股票期貨標的三大法人數據皆已就緒！")
        return

    batch_size = 20
    t0 = time.time()
    
    for i in range(0, len(pending_codes), batch_size):
        batch = pending_codes[i:i + batch_size]
        print(f"[*] 正在抓取法人籌碼: {i + 1} ~ {min(i + batch_size, len(pending_codes))} / {len(pending_codes)} (已耗時 {time.time()-t0:.1f}s)...")
        
        batch_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_code = {executor.submit(fetch_single_stock_chips, c): c for c in batch}
            for future in concurrent.futures.as_completed(future_to_code):
                batch_results.append(future.result())

        # 寫入 SQLite
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        for c, stat, rows in batch_results:
            cur.execute("INSERT OR REPLACE INTO stock_status (code, status, rows_count) VALUES (?, ?, ?)",
                        (c, stat, len(rows)))
            if rows:
                cur.executemany("INSERT OR REPLACE INTO chips VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()
        time.sleep(0.5)

    print(f"[✓] 三大法人籌碼數據下載完成！總計耗時: {time.time()-t0:.1f} 秒")

def main():
    init_db()
    codes, meta = load_futures_stocks()
    download_ohlcv_batch(codes)
    download_all_chips(codes)
    print("\n🎉【回測歷史數據全數準備完成】！即刻可執行 backtest_engine.py 進行回測分析。")

if __name__ == "__main__":
    main()
