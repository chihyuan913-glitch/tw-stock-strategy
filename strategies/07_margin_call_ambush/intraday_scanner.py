#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 07：高融資使用率×低維持率斷頭伏擊與浮額清洗 - 盤中即時雷達與到價推播系統
================================================================================
適用時段：09:00 ~ 13:35 (台股盤中交易時段)
【核心功能與架構】
1. 雙向狀態機到價識別引擎：
   - 🔴【斷頭助跌・放空獵殺 (SHORT_BREAKDOWN)】：
     * STOP_LOSS: 現價突破停損線 -> 強制空單停損回補 (最急迫)
     * TP2: 摜破波段滿足點 (-15%) -> 全數獲利落袋
     * TP1: 摜破第一停利點 (-8%) -> 初段回補 1/2
     * ADDON: 摜破加碼價 -> 動能確認加空追擊
     * ENTRY: 現價落入空單建倉區 -> 啟動放空獵殺
   - 🟢【浮額清洗・逆勢抄底 (LONG_REVERSAL)】：
     * STOP_LOSS: 跌破停損防守線 -> 嚴格多單風控撤退
     * TP2: 攻抵第二目標 (+14%) -> 全數獲利落袋
     * TP1: 攻抵第一目標 (+7%) -> 分批停利 1/2
     * ADDON: 突破加碼價 -> 右側確認加碼佈局
     * ENTRY: 現價落入買進安全區 -> 分批多方建倉
2. 整合 algo_guards 之 GLOBAL_TICK_GUARD 防毛刺雙重確認機制，徹底杜絕 1-tick 假到價。
3. 自動載入最新 result.csv，並支援個股期貨優先報價與保證金換算。
4. 支援獨立 LINE 視窗分流 (strategy="07") 與純文字備援。
5. CLI 參數：--once (單次巡檢)、--interval (輪詢間隔)、--test-push (測試推播)。
================================================================================
"""

import sys
import os
import time
import datetime
import json
import urllib.request
import argparse
import pandas as pd
import yfinance as yf

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

# 加入專案根目錄至 sys.path 以引用共用模組
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from line_sender import (
    send_to_line,
    build_price_alert_card,
    format_clean_price_alert_text,
    build_aggregated_alert_card,
    format_clean_aggregated_text
)
from algo_guards import GLOBAL_TICK_GUARD
try:
    from market_schedule import should_suppress_push, is_market_open_today
except ImportError:
    def should_suppress_push(force=False, task_type=""):
        return False, "無 market_schedule 模組"
    def is_market_open_today():
        return True, "預設開盤" 

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

STRATEGY_ID = "07"
STRATEGY_NAME = "策略 07：高融資斷頭伏擊與浮額清洗雷達"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')

# 若 GLOBAL_TICK_GUARD 尚未有 confirm_signal，動態增補相容方法
if not hasattr(GLOBAL_TICK_GUARD, 'confirm_signal'):
    def _confirm_signal_adapter(code: str, event_code: str, current_price: float,
                               target_price: float, direction: str = 'LONG', force_pass: bool = False):
        res = GLOBAL_TICK_GUARD.check_confirmation(code, event_code, current_price, target_price, force_pass=force_pass)
        return res[0] if isinstance(res, tuple) else bool(res)
    setattr(GLOBAL_TICK_GUARD, 'confirm_signal', _confirm_signal_adapter)


def evaluate_intraday_alert(
    target: dict,
    rt: dict,
    alerted_set: set,
    use_tick_guard: bool = True
) -> tuple:
    """
    根據 target['state_code'] ('SHORT_BREAKDOWN' 或 'LONG_REVERSAL') 雙向判定到價事件：
    - SHORT_BREAKDOWN 順序: STOP_LOSS -> TP2 -> TP1 -> ADDON -> ENTRY
    - LONG_REVERSAL 順序: STOP_LOSS -> TP2 -> TP1 -> ADDON -> ENTRY
    並通過 GLOBAL_TICK_GUARD 防毛刺檢核。
    回傳: (event_type: str | None, event_title: str, event_desc: str)
    """
    price = float(rt.get('price', 0.0))
    if price <= 0:
        return (None, "", "")

    # 解析狀態代碼 (若無則根據停損與進場價位幾何關係自動推斷)
    state_code = str(target.get('state_code') or target.get('狀態代碼') or '').strip()
    entry_p = float(target.get('entry_price', target.get('進場價位', price)))
    addon_p = float(target.get('addon_price', target.get('加碼價位', price)))
    tp1_p = float(target.get('tp1_price', target.get('停利TP1', price)))
    tp2_p = float(target.get('tp2_price', target.get('停利TP2', price)))
    sl_p = float(target.get('sl_price', target.get('停損價位', price)))
    mc_p = float(target.get('margin_call_price', target.get('130%追繳價', 0.0)))

    if not state_code:
        state_code = "SHORT_BREAKDOWN" if sl_p > entry_p else "LONG_REVERSAL"

    raw_event = None
    event_title = ""
    event_desc = ""
    threshold_price = entry_p
    direction = "LONG"

    if state_code == "SHORT_BREAKDOWN":
        direction = "SHORT"
        # 1. 空單停損回補 (最急迫風控)
        if price >= sl_p:
            raw_event = "STOP_LOSS"
            event_title = "🛑【空單停損・破線反抽】"
            event_desc = f"現價 {price:.2f} 突破停損價 {sl_p:.2f}，空單嚴格強制回補離場！"
            threshold_price = sl_p
        # 2. 第二波段停利滿足
        elif price <= tp2_p:
            raw_event = "TP2"
            event_title = "🎯【波段滿足・全數獲利】"
            event_desc = f"現價 {price:.2f} 摜破第二目標 {tp2_p:.2f}，斷頭骨牌滿足，空單全數獲利了結！"
            threshold_price = tp2_p
        # 3. 第一停利目標回補
        elif price <= tp1_p:
            raw_event = "TP1"
            event_title = "💰【斷頭停利・初段回補】"
            event_desc = f"現價 {price:.2f} 摜破第一目標 {tp1_p:.2f}，建議回補 1/2 保留勝果！"
            threshold_price = tp1_p
        # 3.5. 融資 130% 追繳臨界線摜破 (多殺多賣壓加速)
        elif mc_p > 0 and price <= mc_p and price > tp1_p:
            raw_event = "MARGIN_CALL"
            event_title = "🚨【融資追繳觸發・多殺多加速】"
            event_desc = f"現價 {price:.2f} 摜破 130% 追繳警戒線 ({mc_p:.2f} 元)！融資追繳令引爆斷頭賣壓，空單續抱擴大戰果！"
            threshold_price = mc_p
        # 4. 破底加空追擊
        elif price <= addon_p:
            raw_event = "ADDON"
            event_title = "⚡【破底加空・空頭延續】"
            event_desc = f"現價 {price:.2f} 摜破加碼價 {addon_p:.2f}，動能確認加空追擊！"
            threshold_price = addon_p
        # 5. 起跌建倉區間
        elif price >= entry_p and price < sl_p:
            raw_event = "ENTRY"
            event_title = "🔻【起跌建倉・空方出擊】"
            event_desc = f"現價 {price:.2f} 落入空方建倉區間 ({entry_p:.2f} ~ {sl_p:.2f})，啟動放空佈局！"
            threshold_price = entry_p

    else:
        # LONG_REVERSAL (做多方向)
        direction = "LONG"
        # 1. 多單停損撤退 (最急迫風控)
        if price <= sl_p:
            raw_event = "STOP_LOSS"
            event_title = "🛑【多單停損・破線撤退】"
            event_desc = f"現價 {price:.2f} 跌破停損價 {sl_p:.2f}，嚴格執行風控停損出場！"
            threshold_price = sl_p
        # 2. 第二目標波段滿足
        elif price >= tp2_p:
            raw_event = "TP2"
            event_title = "🎯【波段滿足・全數獲利】"
            event_desc = f"現價 {price:.2f} 攻抵第二目標 {tp2_p:.2f}，浮額清洗主升滿足，全數獲利落袋！"
            threshold_price = tp2_p
        # 3. 第一目標分批拔檔
        elif price >= tp1_p:
            raw_event = "TP1"
            event_title = "💰【反彈停利・分批拔檔】"
            event_desc = f"現價 {price:.2f} 攻抵第一目標 {tp1_p:.2f}，建議分批獲利入袋 1/2！"
            threshold_price = tp1_p
        # 4. 突破轉強動能加碼
        elif price >= addon_p:
            raw_event = "ADDON"
            event_title = "⚡【突破加碼・多頭續強】"
            event_desc = f"現價 {price:.2f} 突破加碼價 {addon_p:.2f}，右側確認動能加碼！"
            threshold_price = addon_p
        # 5. 起漲分批建倉區間
        elif price <= entry_p and price > sl_p:
            raw_event = "ENTRY"
            event_title = "🟢【起漲建倉・多方佈局】"
            event_desc = f"現價 {price:.2f} 落入黃金建倉區間 ({sl_p:.2f} ~ {entry_p:.2f})，啟動分批佈局！"
            threshold_price = entry_p

    # 若無事件或當日已推播該事件，跳過
    if not raw_event or raw_event in alerted_set:
        return (None, "", "")

    # 防毛刺過濾檢驗
    if use_tick_guard:
        code = str(target.get('code') or target.get('證券代號') or '').strip()
        if hasattr(GLOBAL_TICK_GUARD, 'confirm_signal'):
            confirmed = GLOBAL_TICK_GUARD.confirm_signal(code, raw_event, price, threshold_price, direction=direction)
            if isinstance(confirmed, tuple):
                confirmed = confirmed[0]
        elif hasattr(GLOBAL_TICK_GUARD, 'check_confirmation'):
            confirmed, _ = GLOBAL_TICK_GUARD.check_confirmation(code, raw_event, price, threshold_price)
        else:
            confirmed = True

        if not confirmed:
            return (None, "", "")

    return (raw_event, event_title, event_desc)



def _safe_float(val, default: float = 0.0) -> float:
    if val is None or pd.isna(val):
        return default
    try:
        s = str(val).strip()
        if s in ('', '--', 'nan', 'NaN', 'None', 'null'):
            return default
        return float(s)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default: int = 0) -> int:
    if val is None or pd.isna(val):
        return default
    try:
        s = str(val).strip()
        if s in ('', '--', 'nan', 'NaN', 'None', 'null'):
            return default
        return int(float(s))
    except (ValueError, TypeError):
        return default
def load_targets() -> dict:
    """優先自 result.csv 載入最新斷頭伏擊與清洗標的與四大防線價位 (防呆安全解析)"""
    targets = {}
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
            for _, row in df.iterrows():
                code = str(row['證券代號']).strip()
                if not (code.isdigit() and len(code) == 4):
                    continue
                close_p = _safe_float(row.get('收盤價'), 0.0)
                if close_p <= 0:
                    continue
                st_code = str(row.get('狀態代碼', '')).strip()
                st_badge = str(row.get('多空狀態', '')).strip()
                tier_label = str(row.get('門檻層級', '')).strip()

                mc_val = _safe_float(row.get('130%追繳價', row.get('margin_call_price')), 0.0)
                dist_mc = _safe_float(row.get('距追繳空間%'), 0.0)
                mc_status = str(row.get('追繳狀態', '')).strip()
                basis_d = str(row.get('價差(Basis)', '')).strip()
                f_oi = _safe_int(row.get('期貨未平倉(OI)'), 0)
                f_close_val = _safe_float(row.get('期貨收盤價'), close_p)
                m_1lot_val = _safe_int(row.get('1口保證金(約)'), 0)

                targets[code] = {
                    'code': code,
                    '證券代號': code,
                    'name': str(row['證券名稱']).strip(),
                    '證券名稱': str(row['證券名稱']).strip(),
                    'close': close_p,
                    '收盤價': close_p,
                    'state_code': st_code,
                    '狀態代碼': st_code,
                    'state_badge': st_badge,
                    '多空狀態': st_badge,
                    'tier_level': tier_label,
                    '門檻層級': tier_label,
                    'entry_price': _safe_float(row.get('進場價位', row.get('現貨進場')), close_p),
                    'addon_price': _safe_float(row.get('加碼價位', row.get('現貨加碼')), close_p),
                    'tp1_price': _safe_float(row.get('停利TP1', row.get('現貨TP1')), close_p),
                    'tp2_price': _safe_float(row.get('停利TP2', row.get('現貨TP2')), close_p),
                    'sl_price': _safe_float(row.get('停損價位', row.get('現貨停損')), close_p),
                    'margin_call_price': mc_val,
                    '130%追繳價': mc_val,
                    'distance_to_margin_call_pct': dist_mc,
                    '距追繳空間%': dist_mc,
                    'margin_call_status': mc_status,
                    '追繳狀態': mc_status,
                    'contract': str(row.get('期貨契約', '--')).strip(),
                    'month': str(row.get('期貨月份', '--')).strip(),
                    'futures_close': f_close_val,
                    'basis_desc': basis_d,
                    '價差(Basis)': basis_d,
                    'futures_oi': f_oi,
                    '期貨未平倉(OI)': f_oi,
                    'margin_1lot': m_1lot_val,
                    'settlement_tag': str(row.get('結算倒數', '')).strip(),
                    '結算倒數': str(row.get('結算倒數', '')).strip(),
                    'next_month': str(row.get('次月合約', '')).strip(),
                    '次月合約': str(row.get('次月合約', '')).strip(),
                    'next_entry': str(row.get('次月進場參考', '')).strip(),
                    '次月進場參考': str(row.get('次月進場參考', '')).strip(),
                    'og_risk': str(row.get('隔日沖風控', '')).strip(),
                    '隔日沖風控': str(row.get('隔日沖風控', '')).strip(),
                    'maintenance_ratio': _safe_float(row.get('融資維持率%'), 0.0),
                    '融資維持率%': _safe_float(row.get('融資維持率%'), 0.0),
                    'usage_ratio': _safe_float(row.get('融資使用率%'), 0.0),
                    '融資使用率%': _safe_float(row.get('融資使用率%'), 0.0),
                    'avg_cost': _safe_float(row.get('預估融資均價'), 0.0),
                    '預估融資均價': _safe_float(row.get('預估融資均價'), 0.0),
                    'volume_ratio': _safe_float(row.get('量增比率'), 1.0),
                    '量增比率': _safe_float(row.get('量增比率'), 1.0),
                    'rr_ratio': _safe_float(row.get('盈虧比'), 2.0),
                    '盈虧比': _safe_float(row.get('盈虧比'), 2.0),
                    'stars': str(row.get('星級推薦', '★★★★☆')).strip()
                }
            if targets:
                print(f"[✓] 已自 result.csv 載入 {len(targets)} 檔融資斷頭與浮額清洗監控標的。")
                return targets
        except Exception as e:
            print(f"[!] 讀取 result.csv 失敗: {e}，將建立備援示範池...")

    # 若無 result.csv 則建立代表性備援池
    demo_codes = ['2603', '2330']
    for c in demo_codes:
        targets[c] = {
            'code': c,
            '證券代號': c,
            'name': f'追蹤標的_{c}',
            '證券名稱': f'追蹤標的_{c}',
            'close': 100.0,
            'state_code': 'SHORT_BREAKDOWN',
            'state_badge': '🔴【斷頭助跌・放空獵殺】',
            'tier_level': '🎯 Tier-1 絕對斷頭高危',
            'entry_price': 100.0,
            'addon_price': 97.5,
            'tp1_price': 92.0,
            'tp2_price': 85.0,
            'sl_price': 103.5,
            'contract': '--',
            'month': '--',
            'futures_close': 100.0,
            'margin_1lot': 0,
            'stars': '★★★★☆'
        }
    return targets


def fetch_realtime_quotes(stock_codes: list) -> dict:
    """向證交所/櫃買 mis.twse 批次查詢即時報價 (支援 Yahoo Finance 備援)"""
    if not stock_codes:
        return {}
    results = {}
    chunk_size = 50
    codes = list(stock_codes)

    for i in range(0, len(codes), chunk_size):
        chunk = codes[i:i + chunk_size]
        channel_str = "|".join([f"tse_{c}.tw|otc_{c}.tw" for c in chunk])
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={channel_str}&json=1&delay=0"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                for item in data.get('msgArray', []):
                    c = item.get('c', '')
                    z = item.get('z', '-')
                    y = item.get('y', '-')
                    o = item.get('o', '-')
                    h = item.get('h', '-')
                    l = item.get('l', '-')
                    v = item.get('v', '0')

                    price = float(z) if z not in ('-', '') else (float(y) if y not in ('-', '') else None)
                    low_p = float(l) if l not in ('-', '') else price
                    high_p = float(h) if h not in ('-', '') else price
                    open_p = float(o) if o not in ('-', '') else price
                    volume = int(v) if v and v.isdigit() else 0

                    if price is not None and c:
                        results[c] = {
                            'price': price,
                            'open': open_p,
                            'high': high_p,
                            'low': low_p,
                            'volume': volume,
                            'time': item.get('t', datetime.datetime.now().strftime('%H:%M:%S'))
                        }
        except Exception:
            pass

    # 針對缺漏標的使用 Yahoo Finance 備援
    missing = [c for c in codes if c not in results or results[c].get('price') is None]
    for c in missing:
        for suffix in ['TW', 'TWO']:
            try:
                url_yf = f'https://query1.finance.yahoo.com/v8/finance/chart/{c}.{suffix}?interval=1m&range=1d'
                req_yf = urllib.request.Request(url_yf, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req_yf, timeout=4) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    meta = data['chart']['result'][0]['meta']
                    p = meta.get('regularMarketPrice')
                    if p is not None:
                        results[c] = {
                            'price': round(float(p), 2),
                            'open': round(float(meta.get('regularMarketOpen', p)), 2),
                            'high': round(float(meta.get('regularMarketDayHigh', p)), 2),
                            'low': round(float(meta.get('regularMarketDayLow', p)), 2),
                            'volume': int(meta.get('regularMarketVolume', 0) // 1000),
                            'time': datetime.datetime.now().strftime('%H:%M:%S')
                        }
                        break
            except Exception:
                continue

    return results


def scan_once(targets: dict, notified_events: dict, force_test: bool = False) -> list:
    """執行一次單輪盤中到價掃描"""
    now_str = datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
    print(f"\n[*] [{now_str}] 正在檢查 {len(targets)} 檔融資斷頭與清洗標的盤中到價情況...")

    codes = list(targets.keys())
    rt_quotes = fetch_realtime_quotes(codes)
    triggered_alerts = []

    for code, target in targets.items():
        rt = rt_quotes.get(code)
        if not rt or rt.get('price') is None:
            # 離線或非開盤測試時以現貨收盤價代替
            rt = {'price': target['close'], 'open': target['close'], 'high': target['close'], 'low': target['close'], 'volume': 1000}

        price = rt['price']
        alerted_for_code = notified_events.setdefault(code, set())

        event_type, event_title, event_desc = evaluate_intraday_alert(
            target=target,
            rt=rt,
            alerted_set=alerted_for_code,
            use_tick_guard=(not force_test)
        )

        if event_type:
            alerted_for_code.add(event_type)
            triggered_alerts.append({
                'target': target,
                'rt': rt,
                'event_type': event_type,
                'event_title': event_title,
                'event_desc': event_desc
            })
            print(f"  [⚡ 觸發到價] {code} {target['name']} | {event_title} | 現價: {price:.2f}")
        else:
            print(f"  • {code} {target['name']}: 現價 {price:.2f} (狀態: {target['state_code']} | 進場: {target['entry_price']} | 停損: {target['sl_price']})")

    # 推播已確認之到價警報
    for alert in triggered_alerts:
        card = build_price_alert_card(
            target=alert['target'],
            rt=alert['rt'],
            event_type=alert['event_type'],
            event_title=alert['event_title'],
            event_desc=alert['event_desc'],
            strategy_id=STRATEGY_ID
        )
        success = send_to_line(card, strategy=STRATEGY_ID)
        if not success:
            clean_text = format_clean_price_alert_text(
                target=alert['target'],
                rt=alert['rt'],
                event_type=alert['event_type'],
                event_title=alert['event_title'],
                event_desc=alert['event_desc'],
                strategy_id=STRATEGY_ID
            )
            send_to_line(clean_text, strategy=STRATEGY_ID)

    return triggered_alerts


def send_test_push(alert_type: str = "ADDON"):
    """發送一則模擬到價卡片以驗證 LINE 連線與 Flex 排版 (支援 MARGIN_CALL 等全事件)"""
    print(f"[*] 正在發送【策略 07：融資斷頭與浮額清洗】測試推播 (事件: {alert_type})...")
    test_target = {
        'code': '2603',
        'name': '長榮',
        'state_code': 'SHORT_BREAKDOWN',
        'state_badge': '🔴【斷頭助跌・放空獵殺】',
        'tier_level': '🎯 Tier-1 絕對斷頭高危',
        'entry_price': 185.0,
        'addon_price': 180.5,
        'tp1_price': 170.0,
        'tp2_price': 157.0,
        'sl_price': 191.5,
        'margin_call_price': 172.5,
        'distance_to_margin_call_pct': 4.2,
        'margin_call_status': '⚠️ 逼近追繳警戒',
        'contract': 'CZF',
        'month': '202611',
        'futures_close': 184.5,
        'basis_desc': '-0.50 (-0.27%)',
        'margin_1lot': 49800,
        'stars': '★★★★★'
    }
    test_rt = {
        'price': 172.0 if alert_type == 'MARGIN_CALL' else 180.0,
        'open': 184.0,
        'high': 185.0,
        'low': 171.5 if alert_type == 'MARGIN_CALL' else 179.5,
        'volume': 15600,
        'time': datetime.datetime.now().strftime('%H:%M:%S')
    }

    if alert_type == 'MARGIN_CALL':
        ev_title = "🚨【融資追繳觸發・多殺多加速】"
        ev_desc = "現價 172.00 摜破 130% 追繳警戒線 (172.50 元)！融資追繳令引爆斷頭賣壓，空單續抱擴大戰果！"
    elif alert_type == 'STOP_LOSS':
        ev_title = "🛑【空單停損・破線反抽】"
        ev_desc = "現價 192.00 突破停損價 191.50，空單嚴格強制回補離場！"
    elif alert_type == 'TP1':
        ev_title = "💰【斷頭停利・初段回補】"
        ev_desc = "現價 170.00 摜破第一目標 170.00，建議回補 1/2 保留勝果！"
    elif alert_type == 'TP2':
        ev_title = "🎯【波段滿足・全數獲利】"
        ev_desc = "現價 157.00 摜破第二目標 157.00，斷頭骨牌滿足，空單全數獲利了結！"
    elif alert_type == 'ENTRY':
        ev_title = "🔻【起跌建倉・空方出擊】"
        ev_desc = "現價 185.00 落入空方建倉區間，啟動放空佈局！"
    else:
        ev_title = "⚡【破底加空・空頭延續】"
        ev_desc = "現價 180.00 摜破加碼價 180.50，融資斷頭賣壓傾瀉，動能確認加空追擊！"

    card = build_price_alert_card(
        target=test_target,
        rt=test_rt,
        event_type=alert_type,
        event_title=ev_title,
        event_desc=ev_desc,
        strategy_id=STRATEGY_ID
    )

    success = send_to_line(card, strategy=STRATEGY_ID)
    if not success:
        clean_text = format_clean_price_alert_text(
            target=test_target,
            rt=test_rt,
            event_type="ADDON",
            event_title="⚡【破底加空・空頭延續】",
            event_desc="現價 180.00 摜破加碼價 180.50，融資斷頭賣壓傾瀉，動能確認加空追擊！",
            strategy_id=STRATEGY_ID
        )
        send_to_line(clean_text, strategy=STRATEGY_ID)
    print(f"[✓] 測試推播已發送完畢 (結果: {'成功' if success else '以文字備援發送'})")


def main():
    parser = argparse.ArgumentParser(description=f"{STRATEGY_NAME} 盤中即時雷達")
    parser.add_argument("--once", action="store_true", help="執行單次快篩巡邏後結束")
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔秒數 (預設 60 秒)")
    parser.add_argument("--test-push", action="store_true", help="發送一則模擬到價推播以測試連線")
    parser.add_argument("--test-alert", type=str, default=None, choices=["MARGIN_CALL", "STOP_LOSS", "TP1", "TP2", "ADDON", "ENTRY"], help="模擬特定到價事件推播測試")
    parser.add_argument("--force", action="store_true", help="強制執行雷達監控 (略過休市防禦)")
    args = parser.parse_args()

    if args.test_push or args.test_alert:
        send_test_push(alert_type=args.test_alert or "ADDON")
        return

    # 休市防護檢驗 (未開盤日自動靜默結束，節省運算與通訊資源)
    if not args.force:
        suppress, reason = should_suppress_push(force=False, task_type="盤中雷達")
        if suppress:
            print(f"🛑 [休市防禦] 台股今日未開盤: {reason}，已自動靜默結束盤中雷達。使用 --force 可強制執行。")
            return

    targets = load_targets()
    notified_events = {}

    if args.once:
        scan_once(targets, notified_events)
        return

    print("=" * 70)
    print(f"🚀 啟動【{STRATEGY_NAME}】盤中即時巡邏雷達")
    print(f"⏰ 輪詢間隔：{args.interval} 秒 | 監控標的：{len(targets)} 檔")
    print("=" * 70)

    try:
        while True:
            scan_once(targets, notified_events)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[!] 收到終止訊號，盤中雷達已安全停止。")


if __name__ == "__main__":
    main()
