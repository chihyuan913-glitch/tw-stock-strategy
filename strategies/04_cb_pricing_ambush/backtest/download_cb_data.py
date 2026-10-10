#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下載台股可轉債 (CB) 發行資料與標的股票歷史日K線資料
回測區間：民國 114 年 1 月 1 日 (2025-01-01) 至 115 年 9 月 30 日 (2026-09-30)
"""

import sys
import os
import json
import time
import urllib.request
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
CB_ISSUES_JSON = os.path.join(BASE_DIR, "cb_issues_2025_2026.json")
OHLCV_PKL = os.path.join(BASE_DIR, "ohlcv_cb_stocks.pkl")

def download_tpex_cb_data():
    """從櫃買中心 OpenAPI 下載轉換債發行資料"""
    print("[1/3] 正在從 TPEx OpenAPI 下載轉(交)換債發行資料清單...")
    url = 'https://www.tpex.org.tw/openapi/v1/bond_ISSBD5_data'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    data = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                break
        except Exception as e:
            print(f"  重試中 ({attempt + 1}/3)... 錯誤: {e}")
            time.sleep(2)
            
    if not data:
        print("[!] 無法取得 TPEx 資料，請檢查網路連線")
        return []
        
    df = pd.DataFrame(data)
    print(f"  TPEx 原始轉債資料總筆數: {len(df)}")
    
    # 篩選民國 114 年 1 月 1 日 (20250101) 至 115 年 9 月 30 日 (20260930) 掛牌發行之 CB
    # 且 IssuerCode 須為合法台股四碼代號，BondType 為轉換債 (5)
    df_filtered = df[
        (df['IssueDate'] >= '20250101') & 
        (df['IssueDate'] <= '20260930') & 
        (df['IssuerCode'].str.len() == 4) &
        (df['IssuerCode'].str.isdigit())
    ].copy()
    
    # 轉換價格必須大於 0
    df_filtered['ConversionPrice'] = pd.to_numeric(df_filtered['Conversion/ExchangePriceAtIssuance'], errors='coerce')
    df_filtered = df_filtered[df_filtered['ConversionPrice'] > 0]
    
    # 發行額度轉為億元
    df_filtered['IssueAmountNum'] = pd.to_numeric(df_filtered['IssueAmount'], errors='coerce') / 100000000.0
    
    # 依照發行日期排序
    df_filtered = df_filtered.sort_values('IssueDate').reset_index(drop=True)
    
    records = df_filtered.to_dict(orient='records')
    with open(CB_ISSUES_JSON, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
        
    print(f"[OK] 成功篩選出 {len(records)} 檔於 2025/01/01 ~ 2026/09/30 發行之可轉債 (涵蓋 {df_filtered['IssuerCode'].nunique()} 家發行公司)")
    print(f"  已儲存至: {CB_ISSUES_JSON}")
    return records

def download_stocks_ohlcv(records):
    """下載所有發行公司標的股票之日K線 (2024-11-01 至 2026-10-09)"""
    stock_codes = sorted(list(set(r['IssuerCode'] for r in records)))
    print(f"[2/3] 準備下載 {len(stock_codes)} 檔標的股票歷史價量資料 (Yahoo Finance)...")
    
    # 已經下載過則讀取快取
    all_data = {}
    if os.path.exists(OHLCV_PKL):
        try:
            all_data = pd.read_pickle(OHLCV_PKL)
            print(f"  已從快取讀取 {len(all_data)} 檔歷史資料")
        except Exception:
            all_data = {}

    missing_codes = [c for c in stock_codes if c not in all_data or len(all_data[c]) < 50]
    print(f"  需下載/補齊檔數: {len(missing_codes)}")
    
    start_date = "2024-10-01"
    end_date = "2026-10-09"
    
    batch_size = 20
    for i in range(0, len(missing_codes), batch_size):
        batch = missing_codes[i:i+batch_size]
        tickers = [f"{c}.TW" for c in batch] + [f"{c}.TWO" for c in batch]
        print(f"  下載批次 [{i+1}~{min(i+batch_size, len(missing_codes))}/{len(missing_codes)}]...")
        
        try:
            df = yf.download(tickers, start=start_date, end=end_date, group_by='ticker', auto_adjust=False, progress=False)
            for c in batch:
                target_df = None
                for suffix in [".TW", ".TWO"]:
                    t = f"{c}{suffix}"
                    if t in df.columns.levels[0]:
                        sub_df = df[t].dropna(how='all')
                        if len(sub_df) >= 30:
                            target_df = sub_df.copy()
                            break
                if target_df is not None and not target_df.empty:
                    # 確保欄位標準化
                    target_df = target_df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                    all_data[c] = target_df
        except Exception as e:
            print(f"    批次下載錯誤: {e}")
            
        time.sleep(0.5)

    # 針對少數未抓到的個股單獨補抓
    still_missing = [c for c in stock_codes if c not in all_data or len(all_data[c]) < 30]
    if still_missing:
        print(f"  個別補抓 {len(still_missing)} 檔標的...")
        for c in still_missing:
            for suffix in [".TW", ".TWO"]:
                try:
                    df = yf.download(f"{c}{suffix}", start=start_date, end=end_date, auto_adjust=False, progress=False)
                    if df is not None and len(df) >= 30:
                        all_data[c] = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                        break
                except Exception:
                    pass

    # 儲存 pickle 快取
    pd.to_pickle(all_data, OHLCV_PKL)
    print(f"[3/3] 完成！成功儲存 {len(all_data)} 檔個股歷史數據至 {OHLCV_PKL}")
    return all_data

if __name__ == "__main__":
    records = download_tpex_cb_data()
    if records:
        download_stocks_ohlcv(records)
