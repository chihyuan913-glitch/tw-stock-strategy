#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
穩健快取 2024-12-01 至 2026-09-30 (114年1月~115年9月底) 證交所三大法人買賣超數據 (T86)
存入 SQLite 資料庫：strategies/02_institutional_momentum/cache/chips.db
"""

import os
import sys
import time
import json
import sqlite3
import random
import requests
import concurrent.futures
import yfinance as yf

CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
DB_PATH = os.path.join(CACHE_DIR, 'chips.db')

URL_TEMPLATES = [
    'https://www.twse.com.tw/fund/T86?date={date}&selectType=ALLBUT0999&response=json',
    'https://www.twse.com.tw/zh/fund/T86?date={date}&selectType=ALLBUT0999&response=json'
]

def init_db():
    os.makedirs(CACHE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_chips (
            date TEXT,
            code TEXT,
            name TEXT,
            foreign_net INTEGER,
            trust_net INTEGER,
            dealer_net INTEGER,
            total_net INTEGER,
            PRIMARY KEY (date, code)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS download_status (
            date TEXT PRIMARY KEY,
            status TEXT,
            stock_count INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_date ON daily_chips(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_code ON daily_chips(code)')
    conn.commit()
    conn.close()

def get_trading_dates(start_date='2024-12-01', end_date='2026-10-01'):
    taiex = yf.download('^TWII', start=start_date, end=end_date, progress=False)
    dates = [d.strftime('%Y%m%d') for d in taiex.index]
    return dates

def fetch_single_date(d_str):
    session = requests.Session()
    session.headers.update({
        'User-Agent': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.{random.randint(1000,9999)}.0',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    })
    
    for attempt in range(5):
        url = URL_TEMPLATES[attempt % len(URL_TEMPLATES)].format(date=d_str)
        try:
            resp = session.get(url, timeout=12)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    time.sleep(1.0 + attempt)
                    continue

                stat = data.get('stat', '')
                if stat == 'OK':
                    rows = []
                    for row in data.get('data', []):
                        code = row[0].strip()
                        if len(code) == 4 and code.isdigit():
                            try:
                                name = row[1].strip()
                                foreign = int(row[4].replace(',', ''))
                                trust = int(row[10].replace(',', ''))
                                dealer = int(row[11].replace(',', ''))
                                total = int(row[18].replace(',', ''))
                                rows.append((d_str, code, name, foreign, trust, dealer, total))
                            except (ValueError, IndexError):
                                continue
                    return d_str, 'OK', rows
                elif '很抱歉' in stat or '查無資料' in stat:
                    return d_str, 'NO_DATA', []
                else:
                    time.sleep(1.0 + attempt * 1.5)
            elif resp.status_code in (429, 307):
                time.sleep(3.0 + attempt * 2.0)
            else:
                time.sleep(1.0)
        except Exception:
            time.sleep(1.5 + attempt * 1.5)
            
    return d_str, 'FAIL', []

def save_batch_to_db(results):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for d_str, status, rows in results:
        if status == 'OK':
            cursor.executemany('''
                INSERT OR REPLACE INTO daily_chips (date, code, name, foreign_net, trust_net, dealer_net, total_net)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', rows)
            cursor.execute('''
                INSERT OR REPLACE INTO download_status (date, status, stock_count)
                VALUES (?, ?, ?)
            ''', (d_str, 'OK', len(rows)))
        elif status == 'NO_DATA':
            cursor.execute('''
                INSERT OR REPLACE INTO download_status (date, status, stock_count)
                VALUES (?, ?, ?)
            ''', (d_str, 'NO_DATA', 0))
    conn.commit()
    conn.close()

def main():
    init_db()
    trading_dates = get_trading_dates()
    print(f"[*] 總計需處理交易日: {len(trading_dates)} 天 ({trading_dates[0]} ~ {trading_dates[-1]})", flush=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT date FROM download_status WHERE status = 'OK'")
    already_done = set(r[0] for r in cursor.fetchall())
    conn.close()

    pending_dates = [d for d in trading_dates if d not in already_done]
    print(f"[*] 已完成: {len(already_done)} 天，尚需下載: {len(pending_dates)} 天", flush=True)

    if not pending_dates:
        print("[✓] 所有交易日法人數據已下載齊全！", flush=True)
        return

    batch_size = 10
    total_batches = (len(pending_dates) + batch_size - 1) // batch_size
    t_start = time.time()

    for b_idx in range(total_batches):
        b_dates = pending_dates[b_idx*batch_size : (b_idx+1)*batch_size]
        b_t0 = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            batch_results = list(executor.map(fetch_single_date, b_dates))
            
        save_batch_to_db(batch_results)
        
        ok_cnt = sum(1 for _, s, _ in batch_results if s == 'OK')
        elapsed = time.time() - t_start
        done_cnt = len(already_done) + min((b_idx + 1) * batch_size, len(pending_dates))
        pct = (done_cnt / len(trading_dates)) * 100
        rate = ((b_idx + 1) * batch_size) / elapsed if elapsed > 0 else 0
        rem_sec = (len(pending_dates) - (b_idx + 1) * batch_size) / rate if rate > 0 else 0
        
        print(f"[{done_cnt}/{len(trading_dates)} | {pct:.1f}%] 批次 {b_dates[0]}~{b_dates[-1]} ({ok_cnt}/{len(b_dates)} 成功, 耗時 {time.time()-b_t0:.1f}s, 預估剩餘 {rem_sec/60:.1f}m)", flush=True)
        time.sleep(random.uniform(0.6, 1.2))

    print(f"\n[✓] 全部籌碼資料下載與快取完成！總耗時: {(time.time()-t_start)/60:.2f} 分鐘", flush=True)

if __name__ == '__main__':
    main()
