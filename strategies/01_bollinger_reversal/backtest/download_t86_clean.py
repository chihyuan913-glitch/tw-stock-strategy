#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
穩健下載台股三大法人買賣超 (T86)：
- 使用 requests.Session() 保持連線
- 設置標準瀏覽器 Headers 防止 428 Precondition Required
- 逐日循序下載並搭配適當延遲 (0.35s)
- 遇到休市日 (stat="很抱歉，沒有符合條件的資料!") 標記為 HOLIDAY
- 實時存入 SQLite 資料庫 (t86.sqlite)
"""

import sys
import os
import time
import sqlite3
import datetime
import requests
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

def get_trading_dates():
    print("[*] 正在取得台股交易日曆 (2025/01/01 ~ 2026/09/30)...")
    df = yf.download("0050.TW", start="2025-01-01", end="2026-10-01", auto_adjust=False, progress=False)
    trading_dates = [d.strftime("%Y%m%d") for d in df.index]
    print(f"[OK] 共有 {len(trading_dates)} 個交易日。")
    return trading_dates

def main():
    trading_dates = get_trading_dates()

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
    # 找出已經成功抓取 (status in ('OK', 'HOLIDAY')) 的日期
    cur.execute("SELECT date FROM dates_status WHERE status IN ('OK', 'HOLIDAY')")
    completed_dates = set(r[0] for r in cur.fetchall())
    conn.close()

    pending_dates = [d for d in trading_dates if d not in completed_dates]
    print(f"[*] 總交易日: {len(trading_dates)}，已就緒: {len(completed_dates)}，待下載: {len(pending_dates)}")
    if not pending_dates:
        print("[OK] 所有 T86 資料皆已就緒！")
        return

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': 'https://www.twse.com.tw/zh/trading/foreign/t86.html'
    })

    success_cnt = 0
    t_start = time.time()

    for idx, d_str in enumerate(pending_dates, 1):
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json"
        retries = 3
        stat_result = "ERROR"
        rows_to_insert = []

        for attempt in range(retries):
            try:
                r = session.get(url, timeout=12)
                if r.status_code == 200:
                    data = r.json()
                    stat = data.get("stat")
                    if stat == "OK":
                        raw_data = data.get("data", [])
                        for row in raw_data:
                            code = row[0].strip()
                            if len(code) == 4 and code.isdigit():
                                try:
                                    foreign = int(row[4].replace(",", ""))
                                    trust = int(row[10].replace(",", ""))
                                    dealer = int(row[11].replace(",", ""))
                                    total = int(row[18].replace(",", ""))
                                    rows_to_insert.append((d_str, code, foreign, trust, dealer, total))
                                except (ValueError, IndexError):
                                    pass
                        stat_result = "OK"
                        break
                    elif "沒有符合條件的資料" in stat:
                        stat_result = "HOLIDAY"
                        break
                    else:
                        stat_result = stat
                        break
                elif r.status_code == 428:
                    time.sleep(2.0 * (attempt + 1))
                else:
                    time.sleep(1.0)
            except Exception as e:
                time.sleep(1.5 * (attempt + 1))

        # 寫入 SQLite
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO dates_status VALUES (?, ?, ?)", (d_str, stat_result, len(rows_to_insert)))
        if rows_to_insert:
            cur.executemany("INSERT OR REPLACE INTO t86_trades VALUES (?, ?, ?, ?, ?, ?)", rows_to_insert)
        conn.commit()
        conn.close()

        if stat_result in ("OK", "HOLIDAY"):
            success_cnt += 1

        if idx % 10 == 0 or idx == len(pending_dates):
            elapsed = time.time() - t_start
            rate = idx / max(elapsed, 0.001)
            remain_sec = (len(pending_dates) - idx) / max(rate, 0.001)
            print(f"[{idx}/{len(pending_dates)}] {d_str}: {stat_result} (寫入 {len(rows_to_insert)} 筆) | 預估剩餘: {int(remain_sec)}s")

        time.sleep(0.35)

    print(f"[OK] 三大法人 T86 歷史資料同步完成！共處理 {len(pending_dates)} 個交易日。")

if __name__ == '__main__':
    main()
