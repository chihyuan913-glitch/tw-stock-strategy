#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下載台股歷史回測資料：
1. 證交所三大法人買賣超 (T86) 2025/01/01 ~ 2026/09/30 (存入 SQLite)
2. Yahoo Finance 台股 OHLCV 歷史股價 2024/11/01 ~ 2026/10/08 (存入 Pickle/Parquet)
"""

import sys
import os
import time
import json
import sqlite3
import datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
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
DB_PATH = os.path.join(BASE_DIR, "t86.sqlite")
OHLCV_PATH = os.path.join(BASE_DIR, "ohlcv.pkl")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dates_status (
            date TEXT PRIMARY KEY,
            status TEXT,
            rows_count INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS t86_trades (
            date TEXT,
            code TEXT,
            foreign_net INTEGER,
            trust_net INTEGER,
            dealer_net INTEGER,
            total_net INTEGER,
            PRIMARY KEY (date, code)
        )
    """)
    conn.commit()
    conn.close()

def get_trading_dates():
    print("[*] 正在從 0050.TW 取得交易日曆...")
    df = yf.download("0050.TW", start="2025-01-01", end="2026-10-01", auto_adjust=False, progress=False)
    trading_dates = [d.strftime("%Y%m%d") for d in df.index]
    print(f"[OK] 取得 {len(trading_dates)} 個台股交易日 ({trading_dates[0]} ~ {trading_dates[-1]})")
    return trading_dates

def fetch_single_t86(date_str):
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                stat = data.get("stat")
                if stat == "OK":
                    rows = []
                    for r in data.get("data", []):
                        code = r[0].strip()
                        if len(code) == 4 and code.isdigit():
                            try:
                                foreign = int(r[4].replace(",", ""))
                                trust = int(r[10].replace(",", ""))
                                dealer = int(r[11].replace(",", ""))
                                total = int(r[18].replace(",", ""))
                                rows.append((date_str, code, foreign, trust, dealer, total))
                            except (ValueError, IndexError):
                                pass
                    return date_str, "OK", rows
                else:
                    return date_str, stat, []
        except Exception as e:
            time.sleep(1.0 * (attempt + 1))
    return date_str, "ERROR", []

def download_t86_all(trading_dates):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT date FROM dates_status WHERE status = 'OK'")
    done_dates = set(r[0] for r in cur.fetchall())
    conn.close()

    pending_dates = [d for d in trading_dates if d not in done_dates]
    print(f"[*] 總交易日: {len(trading_dates)}，已完成: {len(done_dates)}，待下載: {len(pending_dates)}")
    if not pending_dates:
        print("[OK] 所有 T86 資料皆已就緒！")
        return

    # 使用多執行緒批次下載
    batch_size = 20
    for i in range(0, len(pending_dates), batch_size):
        batch = pending_dates[i:i + batch_size]
        print(f"[*] 下載進度: {i + 1}/{len(pending_dates)} ~ {min(i + batch_size, len(pending_dates))}...")
        
        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_date = {executor.submit(fetch_single_t86, d): d for d in batch}
            for future in as_completed(future_to_date):
                results.append(future.result())

        # 寫入 SQLite
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        for d, stat, rows in results:
            cur.execute("INSERT OR REPLACE INTO dates_status VALUES (?, ?, ?)", (d, stat, len(rows)))
            if rows:
                cur.executemany("INSERT OR REPLACE INTO t86_trades VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()
        time.sleep(0.5)

    print("[OK] 三大法人 T86 歷史資料全部下載完畢！")

def get_stock_codes():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT code FROM t86_trades")
    codes = [r[0] for r in cur.fetchall()]
    conn.close()
    return sorted(codes)

def download_ohlcv(stock_codes):
    if os.path.exists(OHLCV_PATH):
        print(f"[OK] 歷史股價 OHLCV 檔案已存在: {OHLCV_PATH}")
        return

    print(f"[*] 開始從 Yahoo Finance 下載 {len(stock_codes)} 檔股票歷史股價 (2024-11-01 ~ 2026-10-08)...")
    tickers = [f"{c}.TW" for c in stock_codes]
    chunk_size = 100
    all_dfs = []

    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        print(f"[*] 下載股價進度: {i + 1} ~ {min(i + chunk_size, len(tickers))} / {len(tickers)}...")
        try:
            df = yf.download(chunk, start="2024-11-01", end="2026-10-08", auto_adjust=False, progress=False)
            all_dfs.append(df)
        except Exception as e:
            print(f"[!] 下載批次失敗: {e}")
        time.sleep(0.5)

    print("[*] 合併所有股價數據...")
    merged_df = pd.concat(all_dfs, axis=1)
    # 存檔 pickle
    merged_df.to_pickle(OHLCV_PATH)
    print(f"[OK] 成功下載並儲存股價數據至 {OHLCV_PATH}，形狀: {merged_df.shape}")

def main():
    init_db()
    trading_dates = get_trading_dates()
    download_t86_all(trading_dates)
    codes = get_stock_codes()
    print(f"[*] 標的股票代碼總數: {len(codes)}")
    download_ohlcv(codes)
    print("[✓] 全部回測資料準備就緒！")

if __name__ == "__main__":
    main()
