#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下載並快取 2024-12-01 至 2026-10-01 台股所有上市標的及加權指數 (^TWII) 之日 K 線 (OHLCV)
存入 SQLite 資料庫：strategies/02_institutional_momentum/cache/prices.db
"""

import os
import sys
import time
import sqlite3
import pandas as pd
import yfinance as yf

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
CHIPS_DB_PATH = os.path.join(CACHE_DIR, 'chips.db')
PRICES_DB_PATH = os.path.join(CACHE_DIR, 'prices.db')

def init_db():
    os.makedirs(CACHE_DIR, exist_ok=True)
    conn = sqlite3.connect(PRICES_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_prices (
            code TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (code, date)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_date ON daily_prices(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_code ON daily_prices(code)')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS benchmark (
            date TEXT PRIMARY KEY,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def download_benchmark(start_date='2024-12-01', end_date='2026-10-01'):
    print("[*] 正在下載台股大盤指數 (^TWII) 基準行情...", flush=True)
    df = yf.download('^TWII', start=start_date, end=end_date, progress=False)
    if df.empty:
        print("[!] 下載加權指數失敗！", flush=True)
        return
        
    conn = sqlite3.connect(PRICES_DB_PATH)
    cursor = conn.cursor()
    rows = []
    for dt, row in df.iterrows():
        d_str = dt.strftime('%Y%m%d')
        o = float(row['Open'].iloc[0] if isinstance(row['Open'], pd.Series) else row['Open'])
        h = float(row['High'].iloc[0] if isinstance(row['High'], pd.Series) else row['High'])
        l = float(row['Low'].iloc[0] if isinstance(row['Low'], pd.Series) else row['Low'])
        c = float(row['Close'].iloc[0] if isinstance(row['Close'], pd.Series) else row['Close'])
        v = int(row['Volume'].iloc[0] if isinstance(row['Volume'], pd.Series) else row['Volume'])
        rows.append((d_str, round(o, 2), round(h, 2), round(l, 2), round(c, 2), v))
        
    cursor.executemany('''
        INSERT OR REPLACE INTO benchmark (date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', rows)
    conn.commit()
    conn.close()
    print(f"[OK] 加權指數 (^TWII) 已儲存 {len(rows)} 筆日K數據。", flush=True)

def get_stock_codes():
    if os.path.exists(CHIPS_DB_PATH):
        conn = sqlite3.connect(CHIPS_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT code FROM daily_chips")
        codes = [r[0] for r in cursor.fetchall() if len(r[0]) == 4 and r[0].isdigit()]
        conn.close()
        if codes:
            return sorted(codes)
    return []

def download_all_stock_prices(start_date='2024-12-01', end_date='2026-10-01'):
    init_db()
    download_benchmark(start_date, end_date)
    
    codes = get_stock_codes()
    print(f"[*] 預計下載 {len(codes)} 檔股票歷史價格數據...", flush=True)
    if not codes:
        print("[!] 尚未發現有效股票代碼，請確認 chips.db 是否已建立。", flush=True)
        return

    # 檢查已下載股票清單
    conn = sqlite3.connect(PRICES_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT code FROM daily_prices")
    done_codes = set(r[0] for r in cursor.fetchall())
    conn.close()
    
    pending_codes = [c for c in codes if c not in done_codes]
    print(f"[*] 已完成: {len(done_codes)} 檔，待下載: {len(pending_codes)} 檔", flush=True)
    
    if not pending_codes:
        print("[OK] 所有個股價格歷史數據已完整下載！", flush=True)
        return

    batch_size = 50
    total_batches = (len(pending_codes) + batch_size - 1) // batch_size
    t_start = time.time()
    
    for b_idx in range(total_batches):
        b_codes = pending_codes[b_idx*batch_size : (b_idx+1)*batch_size]
        tickers = [f"{c}.TW" for c in b_codes]
        b_t0 = time.time()
        
        try:
            hist_df = yf.download(tickers, start=start_date, end=end_date, progress=False, group_by='ticker')
            
            conn = sqlite3.connect(PRICES_DB_PATH)
            cursor = conn.cursor()
            inserted_rows = []
            
            for c in b_codes:
                t_sym = f"{c}.TW"
                try:
                    if len(tickers) == 1:
                        df_stock = hist_df.dropna()
                    else:
                        if t_sym in hist_df.columns.levels[0]:
                            df_stock = hist_df[t_sym].dropna()
                        else:
                            continue
                            
                    for dt, row in df_stock.iterrows():
                        d_str = dt.strftime('%Y%m%d')
                        o = float(row['Open'])
                        h = float(row['High'])
                        l = float(row['Low'])
                        close_val = float(row['Close'])
                        vol = int(row['Volume'])
                        inserted_rows.append((c, d_str, round(o, 2), round(h, 2), round(l, 2), round(close_val, 2), vol))
                except Exception:
                    continue
                    
            cursor.executemany('''
                INSERT OR REPLACE INTO daily_prices (code, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', inserted_rows)
            conn.commit()
            conn.close()
            
            done_cnt = len(done_codes) + min((b_idx + 1) * batch_size, len(pending_codes))
            pct = (done_cnt / len(codes)) * 100
            print(f"[{done_cnt}/{len(codes)} | {pct:.1f}%] 下載批次 {b_codes[0]}~{b_codes[-1]} (寫入 {len(inserted_rows)} 筆, 耗時 {time.time()-b_t0:.1f}s)", flush=True)
        except Exception as e:
            print(f"[!] 下載批次失敗: {e}", flush=True)
            
        time.sleep(0.5)

    print(f"\n[OK] 全部個股歷史日K下載完成！總耗時: {(time.time()-t_start)/60:.2f} 分鐘", flush=True)

if __name__ == '__main__':
    download_all_stock_prices()
