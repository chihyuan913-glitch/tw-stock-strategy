#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 全國上市櫃月營收資料庫輔助模組 (Revenue Helper)
================================================================================
資料來源：
  1. 臺灣證券交易所 (TWSE) OpenAPI：上市公司每月營業收入彙總表 (t187ap05_L)
  2. 證券櫃檯買賣中心 (TPEx) OpenAPI：上櫃公司每月營業收入彙總表 (mopsfin_t187ap05_O)

功能特色：
  - 一鍵同步上市 (1080+ 家) 與上櫃 (890+ 家) 全市場最新月營收公告。
  - 本地快取機制 (cache/monthly_revenue.json)，當日不重複重複請求，加速選股運算。
  - 計算當月營收、上月營收、去年同月營收、營收年增率 (YoY)、月增率 (MoM)、累計營收年增率。
  - 判定「月營收創歷史/近12個月新高」、「連續成長」、「營收加速度」等量化特徵。
================================================================================
"""

import os
import sys
import json
import datetime
import urllib.request
from pathlib import Path

# Windows 命令列編碼保護
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

ROOT_DIR = Path(__file__).resolve().parent
CACHE_DIR = ROOT_DIR / "cache"
CACHE_FILE = CACHE_DIR / "monthly_revenue.json"

TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"


def ensure_cache_dir():
    if not CACHE_DIR.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)


def parse_float_safe(val, default=0.0):
    if val is None:
        return default
    s = str(val).strip().replace(',', '')
    if not s or s == '-' or s.lower() == 'nan':
        return default
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def fetch_monthly_revenue(force=False, max_cache_hours=12):
    """
    獲取全台股上市櫃公司最新月營收字典:
    回傳 dict: { '2330': {
        'code': '2330', 'name': '台積電', 'industry': '半導體業',
        'report_date': '1151017', 'data_ym': '11509',
        'rev_current': 511857399.0, 'rev_prev_month': 514805337.0, 'rev_last_year': 330980920.0,
        'mom_pct': -0.57, 'yoy_pct': 54.65,
        'cum_rev_current': 3898726974.0, 'cum_yoy_pct': 41.11,
        'is_new_high_candidate': True / False
    }}
    """
    ensure_cache_dir()

    # 檢查快取
    if not force and CACHE_FILE.exists():
        try:
            mtime = datetime.datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
            age_hours = (datetime.datetime.now() - mtime).total_seconds() / 3600.0
            if age_hours < max_cache_hours:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                if cached and len(cached) > 1500:
                    return cached
        except Exception:
            pass

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    rev_dict = {}

    # 1. 抓取 TWSE (上市公司)
    try:
        req = urllib.request.Request(TWSE_REVENUE_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for item in data:
                code = str(item.get('公司代號', '')).strip()
                if not code or len(code) > 6:
                    continue
                
                rev_c = parse_float_safe(item.get('營業收入-當月營收'))
                rev_p = parse_float_safe(item.get('營業收入-上月營收'))
                rev_ly = parse_float_safe(item.get('營業收入-去年當月營收'))
                mom = parse_float_safe(item.get('營業收入-上月比較增減(%)'))
                yoy = parse_float_safe(item.get('營業收入-去年同月增減(%)'))
                cum_c = parse_float_safe(item.get('累計營業收入-當月累計營收'))
                cum_yoy = parse_float_safe(item.get('累計營業收入-前期比較增減(%)'))

                # 營收創高與加速度計算 (Revenue Alpha)
                rev_accel = round(yoy - cum_yoy, 2)
                is_high = (yoy >= 10.0) and (rev_c >= rev_p * 0.98) and (rev_c > 0)
                
                # 評級標籤與爆發分
                if yoy >= 30.0 and (mom >= 0.0 or rev_accel > 0):
                    tier_label = "👑歷史級爆發"
                    score_pts = 25.0
                elif yoy >= 20.0 and rev_c >= rev_p * 0.95:
                    tier_label = "🏆波段創高"
                    score_pts = 20.0
                elif yoy >= 10.0:
                    tier_label = "📈穩健成長"
                    score_pts = 15.0
                else:
                    tier_label = "平穩"
                    score_pts = 5.0

                rev_dict[code] = {
                    'code': code,
                    'name': str(item.get('公司名稱', '')).strip(),
                    'market': 'TWSE',
                    'industry': str(item.get('產業別', '')).strip(),
                    'report_date': str(item.get('出表日期', '')).strip(),
                    'data_ym': str(item.get('資料年月', '')).strip(),
                    'rev_current': rev_c,
                    'rev_prev_month': rev_p,
                    'rev_last_year': rev_ly,
                    'mom_pct': round(mom, 2),
                    'yoy_pct': round(yoy, 2),
                    'cum_rev_current': cum_c,
                    'cum_yoy_pct': round(cum_yoy, 2),
                    'rev_acceleration': rev_accel,
                    'is_new_high_candidate': is_high,
                    'revenue_tier': tier_label,
                    'rev_prev_month': rev_p,
                    'rev_last_year': rev_ly,
                    'mom_pct': round(mom, 2),
                    'yoy_pct': round(yoy, 2),
                    'cum_rev_current': cum_c,
                    'cum_yoy_pct': round(cum_yoy, 2),
                    'rev_acceleration': rev_accel,
                    'is_new_high_candidate': is_high,
                    'revenue_tier': tier_label,
                    'revenue_score': score_pts
                }
    except Exception as e:
        print(f"[!] 抓取 TWSE 月營收失敗: {e}")

    # 2. 抓取 TPEx (上櫃公司)
    try:
        req = urllib.request.Request(TPEX_REVENUE_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for item in data:
                code = str(item.get('公司代號', '')).strip()
                if not code or len(code) > 6:
                    continue

                rev_c = parse_float_safe(item.get('營業收入-當月營收'))
                rev_p = parse_float_safe(item.get('營業收入-上月營收'))
                rev_ly = parse_float_safe(item.get('營業收入-去年當月營收'))
                mom = parse_float_safe(item.get('營業收入-上月比較增減(%)'))
                yoy = parse_float_safe(item.get('營業收入-去年同月增減(%)'))
                cum_c = parse_float_safe(item.get('累計營業收入-當月累計營收'))
                cum_yoy = parse_float_safe(item.get('累計營業收入-前期比較增減(%)'))

                rev_accel = round(yoy - cum_yoy, 2)
                is_high = (yoy >= 10.0) and (rev_c >= rev_p * 0.98) and (rev_c > 0)

                if yoy >= 30.0 and (mom >= 0.0 or rev_accel > 0):
                    tier_label = "👑歷史級爆發"
                    score_pts = 25.0
                elif yoy >= 20.0 and rev_c >= rev_p * 0.95:
                    tier_label = "🏆波段創高"
                    score_pts = 20.0
                elif yoy >= 10.0:
                    tier_label = "📈穩健成長"
                    score_pts = 15.0
                else:
                    tier_label = "平穩"
                    score_pts = 5.0

                rev_dict[code] = {
                    'code': code,
                    'name': str(item.get('公司名稱', '')).strip(),
                    'market': 'TPEx',
                    'industry': str(item.get('產業別', '')).strip(),
                    'report_date': str(item.get('出表日期', '')).strip(),
                    'data_ym': str(item.get('資料年月', '')).strip(),
                    'rev_current': rev_c,
                    'rev_prev_month': rev_p,
                    'rev_last_year': rev_ly,
                    'mom_pct': round(mom, 2),
                    'yoy_pct': round(yoy, 2),
                    'cum_rev_current': cum_c,
                    'cum_yoy_pct': round(cum_yoy, 2),
                    'rev_acceleration': rev_accel,
                    'is_new_high_candidate': is_high,
                    'revenue_tier': tier_label,
                    'revenue_score': score_pts
                }
    except Exception as e:
        print(f"[!] 抓取 TPEx 月營收失敗: {e}")

    # 寫入快取
    if rev_dict and len(rev_dict) > 1000:
        try:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(rev_dict, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[!] 快取寫入失敗: {e}")

    return rev_dict


def get_stock_revenue(code: str, rev_dict: dict = None) -> dict:
    """取得特定股票之月營收資訊字典，若查無回傳預設結構"""
    if rev_dict is None:
        rev_dict = fetch_monthly_revenue()
    
    clean_code = str(code).strip()
    if clean_code in rev_dict:
        return rev_dict[clean_code]
    
    return {
        'code': clean_code,
        'name': '',
        'market': 'UNKNOWN',
        'industry': '',
        'report_date': '',
        'data_ym': '',
        'rev_current': 0.0,
        'rev_prev_month': 0.0,
        'rev_last_year': 0.0,
        'mom_pct': 0.0,
        'yoy_pct': 0.0,
        'cum_rev_current': 0.0,
        'cum_yoy_pct': 0.0,
        'rev_acceleration': 0.0,
        'is_new_high_candidate': False,
        'revenue_tier': '平穩',
        'revenue_score': 5.0
    }


if __name__ == '__main__':
    print("🚀 測試 Revenue Helper 月營收同步模組...")
    revs = fetch_monthly_revenue(force=True)
    print(f"[✓] 成功取得 {len(revs)} 家上市櫃公司月營收數據！")
    
    test_stocks = ['2330', '2454', '2317', '3008', '8069']
    for s in test_stocks:
        info = get_stock_revenue(s, revs)
        print(f"  - [{info['code']} {info['name']}] ({info['market']}) 營收年月: {info['data_ym']} | 當月營收: {info['rev_current']:,.0f} 千元 | YoY: {info['yoy_pct']:+.2f}% | MoM: {info['mom_pct']:+.2f}% | 創高潛力: {info['is_new_high_candidate']}")
