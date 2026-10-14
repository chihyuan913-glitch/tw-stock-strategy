#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 股票期貨報價與定價引擎 (Futures Pricing & Quote Helper)
================================================================================
本模組為全專案共用底層期貨基建，支援策略 03 (弱勢放空避險) 與策略 05 (波段多方動能)：
1. 【股票期貨母池維護 (Stock Futures Pool)】:
   - 自動連線臺灣期貨交易所 (TAIFEX) 官網動態載入最新個股期貨契約與代碼。
   - 本地 stock_futures_list.json 快取備援。
2. 【期交所即時/盤後行情抓取 (TAIFEX Quotes)】:
   - 連線期交所官方 OpenAPI (DailyMarketReportFut) 抓取即時/盤後行情。
   - 自動識別近月主力合約 (Front-Month Active Contract, 最大未平倉量與成交量)。
3. 【期現貨價差與四大防線換算 (Futures 4-Price Mapping)】:
   - 計算期現貨價差 (Basis = 期貨價格 - 現貨價格) 與價差率。
   - 將現貨四大防線自動轉化為「期貨實戰四大防線價位 (進場、加碼、停利、停損)」。
   - 試算一口保證金需求 (表彰 2 張現貨 = 2,000 股，預設 13.5% 級距)。
================================================================================
"""

import os
import sys
import re
import json
import urllib.request
import datetime
from typing import Dict, Any, Optional, Tuple

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))

def get_third_wednesday(year: int, month: int) -> datetime.date:
    """計算指定年月之第三個星期三 (期交所個股期貨法定結算日)"""
    d = datetime.date(year, month, 1)
    days_to_first_wed = (2 - d.weekday()) % 7
    first_wed = d + datetime.timedelta(days=days_to_first_wed)
    return first_wed + datetime.timedelta(days=14)

def get_futures_settlement_info(ref_date: Optional[datetime.date] = None) -> Dict[str, Any]:
    """
    計算目前台股股票期貨之結算週期與倒數交易日數
    - 臺灣期交所結算日為各該契約交割月份第三個星期三
    - 結算日前 5 個交易日自動進入「可開次月新倉提醒期」
    """
    if ref_date is None:
        ref_date = datetime.date.today()

    curr_month_settle = get_third_wednesday(ref_date.year, ref_date.month)

    # 若今天已超過當月第三個星期三，近月合約自動進位為下個曆月
    if ref_date > curr_month_settle:
        y = ref_date.year if ref_date.month < 12 else ref_date.year + 1
        m = ref_date.month + 1 if ref_date.month < 12 else 1
        front_settle = get_third_wednesday(y, m)
        front_month_str = f"{y}{m:02d}"

        ny = y if m < 12 else y + 1
        nm = m + 1 if m < 12 else 1
        next_month_str = f"{ny}{nm:02d}"
    else:
        front_settle = curr_month_settle
        front_month_str = f"{ref_date.year}{ref_date.month:02d}"

        ny = ref_date.year if ref_date.month < 12 else ref_date.year + 1
        nm = ref_date.month + 1 if ref_date.month < 12 else 1
        next_month_str = f"{ny}{nm:02d}"

    # 計算距離結算日之剩餘交易日 (週一至週五)
    trading_days = 0
    curr = ref_date + datetime.timedelta(days=1)
    while curr <= front_settle:
        if curr.weekday() < 5:
            trading_days += 1
        curr += datetime.timedelta(days=1)

    is_near_settlement = (trading_days <= 5)

    if is_near_settlement:
        badge_text = f"⚠️ 結算倒數 {trading_days} 日 (建議開次月 {next_month_str})"
        banner_text = (
            f"⚠️【期貨結算倒數 {trading_days} 日・次月新倉開倉期】近月合約 ({front_month_str}) "
            f"即將於 {front_settle.strftime('%m/%d')} 結算！新建立部位強烈建議直接開立「次月新倉 ({next_month_str})」，"
            f"提早佈局次月主升段並省去結算週轉倉手續費與價差摩擦！"
        )
    else:
        badge_text = f"距結算 {trading_days} 日"
        banner_text = f"🟢【期貨常規交易期】近月合約 ({front_month_str}) 距結算日尚有 {trading_days} 個交易日，流動性最充沛。"

    return {
        'ref_date': ref_date.strftime('%Y-%m-%d'),
        'front_settle': front_settle.strftime('%Y-%m-%d'),
        'front_settle_short': front_settle.strftime('%m/%d'),
        'front_month': front_month_str,
        'next_month': next_month_str,
        'trading_days_left': trading_days,
        'is_near_settlement': is_near_settlement,
        'badge_text': badge_text,
        'banner_text': banner_text
    }

def safe_float(v: Any, default: float = 0.0) -> float:
    """安全浮點數解析器，容錯 NULL / -- / - / 空字串"""
    try:
        s = str(v).strip().replace(',', '')
        if s in ['', '-', '--', 'NULL', 'null', 'None']:
            return default
        return float(s)
    except Exception:
        return default

def load_stock_futures_pool(cache_path: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """
    載入臺灣期貨交易所 (TAIFEX) 股票期貨標的清單
    回傳字典: { '2330': {'contract': 'QF', 'name': '台積電'}, ... }
    """
    if cache_path is None:
        candidates = [
            os.path.join(ROOT_DIR, 'stock_futures_list.json'),
            os.path.join(ROOT_DIR, 'strategies', '03_short_momentum', 'stock_futures_list.json'),
            os.path.join(ROOT_DIR, 'strategies', '05_stock_futures_long', 'stock_futures_list.json')
        ]
        for p in candidates:
            if os.path.exists(p):
                cache_path = p
                break
        if cache_path is None:
            cache_path = os.path.join(ROOT_DIR, 'stock_futures_list.json')

    futures_map = {}

    # 1. 嘗試聯網動態抓取最新期交所名單
    try:
        url = 'https://www.taifex.com.tw/cht/2/stockLists'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=6) as r:
            html = r.read().decode('utf-8', errors='ignore')
            rows = re.findall(r'<tr>.*?<td[^>]*>([A-Za-z0-9]{2,3})</td>.*?<td[^>]*>([0-9]{4})</td>.*?<td[^>]*>(.*?)</td>.*?</tr>', html, re.DOTALL)
            for contract, code, name in rows:
                clean_name = re.sub(r'<.*?>', '', name).strip()
                futures_map[code] = {
                    'contract': contract.strip(),
                    'name': clean_name
                }
            if len(futures_map) >= 150:
                try:
                    with open(cache_path, 'w', encoding='utf-8') as f:
                        json.dump(futures_map, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                return futures_map
    except Exception:
        pass

    # 2. 聯網失敗時讀取本地快取
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                futures_map = json.load(f)
        except Exception:
            pass

    return futures_map


def fetch_taifex_futures_quotes() -> Dict[str, Dict[str, Any]]:
    """
    連線期交所官方 OpenAPI (DailyMarketReportFut) 抓取全部期貨合約最新行情
    自動解析每個股票期貨的「近月主力合約」與「次月合約」
    包含未平倉口數 (Open Interest, OI)、成交量、結算價與最新價格
    回傳字典鍵值為期貨基礎代碼 (如 'QF', 'CA', 'CC')
    """
    url = 'https://openapi.taifex.com.tw/v1/DailyMarketReportFut'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    settle_info = get_futures_settlement_info()
    front_m_target = settle_info['front_month']
    next_m_target = settle_info['next_month']

    all_contracts = {}

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_bytes = resp.read()
            text = raw_bytes.decode('utf-8-sig', errors='ignore').strip()
            
            # 若為 JSON 格式
            if text.startswith('[') or text.startswith('{'):
                data = json.loads(text)
                for row in data:
                    contract = row.get('Contract', '').strip()
                    month = row.get('ContractMonth(Week)', '').strip()
                    if len(month) != 6 or not month.isdigit():
                        continue

                    vol = int(safe_float(row.get('Volume', 0)))
                    last_p = safe_float(row.get('Last'))
                    settle_p = safe_float(row.get('SettlementPrice'))
                    price = last_p if last_p > 0 else settle_p
                    if price <= 0:
                        continue

                    open_p = safe_float(row.get('Open'), default=price)
                    high_p = safe_float(row.get('High'), default=price)
                    low_p = safe_float(row.get('Low'), default=price)
                    oi = int(safe_float(row.get('OpenInterest', 0)))

                    base_code = contract[:-1] if (len(contract) == 3 and contract.endswith('F')) else contract
                    if base_code not in all_contracts:
                        all_contracts[base_code] = {}

                    if month not in all_contracts[base_code] or vol > all_contracts[base_code][month]['volume'] or (oi > 0 and all_contracts[base_code][month]['open_interest'] == 0):
                        all_contracts[base_code][month] = {
                            'contract_sym': contract,
                            'month': month,
                            'price': price,
                            'settle': settle_p if settle_p > 0 else price,
                            'open': open_p,
                            'high': high_p,
                            'low': low_p,
                            'change': row.get('Change', ''),
                            'pct_change': row.get('%', ''),
                            'volume': max(vol, all_contracts[base_code].get(month, {}).get('volume', 0)),
                            'open_interest': max(oi, all_contracts[base_code].get(month, {}).get('open_interest', 0))
                        }
            else:
                # CSV 格式解析
                import csv
                import io
                reader = csv.reader(io.StringIO(text))
                header = next(reader, None)
                for row in reader:
                    if len(row) < 12:
                        continue
                    contract = row[1].strip()
                    month = row[2].strip()
                    if len(month) != 6 or not month.isdigit():
                        continue

                    vol = int(safe_float(row[9]))
                    last_p = safe_float(row[6])
                    settle_p = safe_float(row[10])
                    price = last_p if last_p > 0 else settle_p
                    if price <= 0:
                        continue

                    open_p = safe_float(row[3], default=price)
                    high_p = safe_float(row[4], default=price)
                    low_p = safe_float(row[5], default=price)
                    oi = int(safe_float(row[11]))

                    base_code = contract[:-1] if (len(contract) == 3 and contract.endswith('F')) else contract
                    if base_code not in all_contracts:
                        all_contracts[base_code] = {}

                    if month not in all_contracts[base_code] or vol > all_contracts[base_code][month]['volume'] or (oi > 0 and all_contracts[base_code][month]['open_interest'] == 0):
                        all_contracts[base_code][month] = {
                            'contract_sym': contract,
                            'month': month,
                            'price': price,
                            'settle': settle_p if settle_p > 0 else price,
                            'open': open_p,
                            'high': high_p,
                            'low': low_p,
                            'change': row[7].strip(),
                            'pct_change': row[8].strip(),
                            'volume': max(vol, all_contracts[base_code].get(month, {}).get('volume', 0)),
                            'open_interest': max(oi, all_contracts[base_code].get(month, {}).get('open_interest', 0))
                        }
    except Exception as e:
        print(f"[!] 連線期交所行情失敗 ({e})，將啟用價差動態擬合模式")

    # 彙整每個 base_code 的近月主力與次月合約
    fut_map = {}
    for base_code, months_data in all_contracts.items():
        if not months_data:
            continue

        # 優先鎖定當前近月月份 (front_m_target)，若無則取未平倉量最大或月份最早者
        sorted_months = sorted(months_data.keys())
        front_month_key = front_m_target if front_m_target in months_data else sorted_months[0]
        front_item = months_data[front_month_key]

        # 尋找次月合約 (next_m_target 或排序中大於近月的下一個月份)
        next_month_key = None
        if next_m_target in months_data:
            next_month_key = next_m_target
        else:
            for m in sorted_months:
                if m > front_month_key:
                    next_month_key = m
                    break

        next_item = months_data.get(next_month_key) if next_month_key else None

        fut_map[base_code] = {
            'contract_sym': front_item['contract_sym'],
            'month': front_item['month'],
            'price': front_item['price'],
            'settle': front_item['settle'],
            'open': front_item['open'],
            'high': front_item['high'],
            'low': front_item['low'],
            'change': front_item['change'],
            'pct_change': front_item['pct_change'],
            'volume': front_item['volume'],
            'open_interest': front_item['open_interest'],
            # 次月合約資訊 (供結算前五日開新倉與換月參考)
            'next_contract_sym': next_item['contract_sym'] if next_item else front_item['contract_sym'],
            'next_month': next_item['month'] if next_item else next_m_target,
            'next_price': next_item['price'] if next_item else front_item['price'],
            'next_settle': next_item['settle'] if next_item else front_item['settle'],
            'next_volume': next_item['volume'] if next_item else 0,
            'next_open_interest': next_item['open_interest'] if next_item else 0
        }

    return fut_map


def calculate_futures_pricing(
    spot_close: float,
    futures_quote: Optional[Dict[str, Any]],
    entry_spot: float,
    addon_spot: float,
    tp1_spot: float,
    tp2_spot: float,
    sl_spot: float,
    contract_base: str = "",
    is_long: bool = True
) -> Dict[str, Any]:
    """
    計算以「股票期貨價位優先」之實戰操盤價格矩陣
    包含近月主力合約、未平倉口數 (OI)、結算日前五日倒數與次月新倉參考價位矩陣
    """
    settle_info = get_futures_settlement_info()
    has_real_quote = futures_quote is not None and futures_quote.get('price', 0) > 0

    if has_real_quote:
        fut_close = float(futures_quote['price'])
        fut_contract = futures_quote.get('contract_sym', f"{contract_base}F")
        fut_month = futures_quote.get('month', settle_info['front_month'])
        basis = round(fut_close - spot_close, 2)
        basis_pct = round((basis / spot_close) * 100, 2) if spot_close > 0 else 0.0
        fut_vol = futures_quote.get('volume', 0)
        fut_oi = futures_quote.get('open_interest', 0)

        # 次月合約資訊
        next_contract = futures_quote.get('next_contract_sym', fut_contract)
        next_month = futures_quote.get('next_month', settle_info['next_month'])
        next_close = float(futures_quote.get('next_price', fut_close))
        next_basis = round(next_close - spot_close, 2)
        next_basis_pct = round((next_basis / spot_close) * 100, 2) if spot_close > 0 else 0.0
        next_vol = futures_quote.get('next_volume', 0)
        next_oi = futures_quote.get('next_open_interest', 0)
    else:
        # 無真實期貨報價時，以現貨價為基準擬合 (價差為 0)
        fut_close = round(spot_close, 2)
        fut_contract = f"{contract_base}F" if contract_base else "--"
        fut_month = settle_info['front_month']
        basis = 0.0
        basis_pct = 0.0
        fut_vol = 0
        fut_oi = 0

        next_contract = fut_contract
        next_month = settle_info['next_month']
        next_close = fut_close
        next_basis = 0.0
        next_basis_pct = 0.0
        next_vol = 0
        next_oi = 0

    # 近月期貨四大價位計算 (現貨點位加上近月價差平移)
    fut_entry = round(entry_spot + basis, 2)
    fut_addon = round(addon_spot + basis, 2)
    fut_tp1 = round(tp1_spot + basis, 2)
    fut_tp2 = round(tp2_spot + basis, 2)
    fut_sl = round(sl_spot + basis, 2)

    # 次月期貨四大價位計算 (現貨點位加上次月價差平移，供結算前開立次月新倉參考)
    next_entry = round(entry_spot + next_basis, 2)
    next_addon = round(addon_spot + next_basis, 2)
    next_tp1 = round(tp1_spot + next_basis, 2)
    next_tp2 = round(tp2_spot + next_basis, 2)
    next_sl = round(sl_spot + next_basis, 2)

    # 1口期貨保證金試算：一口 = 2,000 股現貨 (2 張)
    # 期貨保證金 = 期貨契約價值 * 適用保證金比例 (預設 13.5% 估算，十位數四捨五入)
    margin_1lot = int(round(fut_close * 2000 * 0.135, -1))
    next_margin_1lot = int(round(next_close * 2000 * 0.135, -1))

    basis_desc = f"{'+' if basis > 0 else ''}{basis:.2f} ({'+' if basis_pct > 0 else ''}{basis_pct:.2f}%)"
    if basis > 0:
        basis_tag = f"正價差 +{basis:.2f}"
    elif basis < 0:
        basis_tag = f"逆價差 {basis:.2f}"
    else:
        basis_tag = "平價差 0.00"

    next_basis_desc = f"{'+' if next_basis > 0 else ''}{next_basis:.2f} ({'+' if next_basis_pct > 0 else ''}{next_basis_pct:.2f}%)"

    return {
        'has_real_quote': has_real_quote,
        'contract_sym': fut_contract,
        'month': fut_month,
        'futures_close': fut_close,
        'basis': basis,
        'basis_pct': basis_pct,
        'basis_desc': basis_desc,
        'basis_tag': basis_tag,
        'futures_vol': fut_vol,
        'futures_oi': fut_oi,
        'futures_entry': fut_entry,
        'futures_addon': fut_addon,
        'futures_tp1': fut_tp1,
        'futures_tp2': fut_tp2,
        'futures_sl': fut_sl,
        'margin_1lot': margin_1lot,
        'shares_per_lot': 2000,
        # 次月合約矩陣
        'next_contract_sym': next_contract,
        'next_month': next_month,
        'next_close': next_close,
        'next_basis': next_basis,
        'next_basis_desc': next_basis_desc,
        'next_vol': next_vol,
        'next_oi': next_oi,
        'next_entry': next_entry,
        'next_addon': next_addon,
        'next_tp1': next_tp1,
        'next_tp2': next_tp2,
        'next_sl': next_sl,
        'next_margin_1lot': next_margin_1lot,
        # 結算週期與前五日提醒
        'days_to_settle': settle_info['trading_days_left'],
        'is_near_settlement': settle_info['is_near_settlement'],
        'settlement_tag': settle_info['badge_text'],
        'settlement_reminder': settle_info['banner_text'],
        'front_settle_date': settle_info['front_settle'],
        'front_settle_short': settle_info['front_settle_short']
    }

