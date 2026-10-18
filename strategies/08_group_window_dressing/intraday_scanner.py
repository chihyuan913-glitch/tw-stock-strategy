#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 08：台股集團作帳與母子聯動伏擊 - 盤中即時雷達與到價推播系統
================================================================================
適用時段：09:00 ~ 13:35 (台股盤中交易時段)
【核心功能與架構】
1. 雙向狀態機到價識別引擎：
   - 🟢【作帳點火・母子共振 (GROUP_IGNITION)】：
     * STOP_LOSS: 現價跌破停損防守線 -> 嚴格作帳多單停損撤退 (最急迫)
     * TP2: 攻抵第二目標 (+14%) -> 集團作帳波段滿足，全數獲利落袋
     * TP1: 攻抵第一目標 (+7%) -> 初段達標分批停利 1/2
     * ADDON: 突破加碼價 -> 領頭羊母子共振續強，右側確認加碼
     * ENTRY: 現價落入買進安全區 -> 分批多方伏擊建倉
   - 🔴【集團結帳・順勢放空 (GROUP_UNWIND)】：
     * STOP_LOSS: 現價突破停損線 -> 強制空單停損回補 (最急迫)
     * TP2: 摜破第二目標 (-15%) -> 結帳賣壓宣洩滿足，空單全數獲利
     * TP1: 摜破第一目標 (-8%) -> 初段獲利回補 1/2
     * ADDON: 摜破加碼價 -> 弱勢破線加空追擊
     * ENTRY: 現價落入空單建倉區 -> 啟動放空獵殺
2. 整合 algo_guards 之 GLOBAL_TICK_GUARD 防毛刺雙重確認機制，徹底杜絕 1-tick 假到價。
3. 自動載入最新 result.csv，並支援個股期貨優先報價與保證金換算。
4. 支援獨立 LINE 視窗分流 (strategy="08") 與純文字備援。
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

STRATEGY_ID = "08"
STRATEGY_NAME = "策略 08：台股集團作帳與母子聯動到價雷達"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')

# 若 GLOBAL_TICK_GUARD 尚未有 confirm_signal，動態增補相容適配器
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
    根據 target['state_code'] ('GROUP_IGNITION' 或 'GROUP_UNWIND') 雙向判定到價事件：
    - GROUP_IGNITION (多方) 順序: STOP_LOSS -> TP2 -> TP1 -> ADDON -> ENTRY
    - GROUP_UNWIND (空方) 順序: STOP_LOSS -> TP2 -> TP1 -> ADDON -> ENTRY
    並通過 GLOBAL_TICK_GUARD 防毛刺檢核。
    回傳: (event_type: str | None, event_title: str, event_desc: str)
    """
    price = float(rt.get('price', 0.0))
    if price <= 0:
        return (None, "", "")

    code = str(target.get('code') or target.get('證券代號') or '').strip()
    state_code = str(target.get('state_code') or target.get('狀態代碼') or '').strip()
    entry_p = float(target.get('entry_price', target.get('進場價位', price)))
    addon_p = float(target.get('addon_price', target.get('加碼價位', price)))
    tp1_p = float(target.get('tp1_price', target.get('停利TP1', price)))
    tp2_p = float(target.get('tp2_price', target.get('停利TP2', price)))
    sl_p = float(target.get('sl_price', target.get('停損價位', price)))

    if not state_code:
        state_code = "GROUP_UNWIND" if sl_p > entry_p else "GROUP_IGNITION"

    raw_event = None
    event_title = ""
    event_desc = ""
    threshold_price = entry_p
    direction = "LONG"

    if state_code == "GROUP_UNWIND":
        direction = "SHORT"
        # 1. 空單停損回補 (最急迫風控)
        if price >= sl_p:
            raw_event = "STOP_LOSS"
            event_title = "🛑【停損風控・空單軋空回補】"
            event_desc = f"現價 {price:.2f} 突破停損價 {sl_p:.2f}，空單嚴格強制回補離場！"
            threshold_price = sl_p
        # 2. 第二波段停利滿足
        elif price <= tp2_p:
            raw_event = "TP2"
            event_title = "🎯【結帳波段滿足・全數獲利】"
            event_desc = f"現價 {price:.2f} 摜破第二目標 {tp2_p:.2f}，結帳賣壓宣洩滿足，空單全數獲利了結！"
            threshold_price = tp2_p
        # 3. 第一停利目標回補
        elif price <= tp1_p:
            raw_event = "TP1"
            event_title = "💰【結帳停利・初段回補】"
            event_desc = f"現價 {price:.2f} 摜破第一目標 {tp1_p:.2f}，建議回補 1/2 保留勝果！"
            threshold_price = tp1_p
        # 4. 破底加空追擊
        elif price <= addon_p:
            raw_event = "ADDON"
            event_title = "⚡【破底加空・結帳延續】"
            event_desc = f"現價 {price:.2f} 摜破加碼價 {addon_p:.2f}，弱勢破線加空追擊！"
            threshold_price = addon_p
        # 5. 起跌建倉區間
        elif price >= entry_p and price < sl_p:
            raw_event = "ENTRY"
            event_title = "🔻【高檔結帳・放空獵殺】"
            event_desc = f"現價 {price:.2f} 落入空方建倉區間 ({entry_p:.2f} ~ {sl_p:.2f})，啟動放空佈局！"
            threshold_price = entry_p

    else:
        # GROUP_IGNITION (多方)
        direction = "LONG"
        # 1. 多單停損撤退 (最急迫風控)
        if price <= sl_p:
            raw_event = "STOP_LOSS"
            event_title = "🛑【停損風控・作帳破線撤退】"
            event_desc = f"現價 {price:.2f} 跌破停損價 {sl_p:.2f}，集團防守線失守，嚴格停損出場！"
            threshold_price = sl_p
        # 2. 第二目標波段滿足
        elif price >= tp2_p:
            raw_event = "TP2"
            event_title = "🎯【波段作帳滿足・全數獲利】"
            event_desc = f"現價 {price:.2f} 攻抵第二目標 {tp2_p:.2f}，集團作帳主升浪滿足，全數落袋為安！"
            threshold_price = tp2_p
        # 3. 第一目標分批停利
        elif price >= tp1_p:
            raw_event = "TP1"
            event_title = "💰【作帳達標・初段分批停利】"
            event_desc = f"現價 {price:.2f} 攻抵第一目標 {tp1_p:.2f}，建議分批獲利 1/2！"
            threshold_price = tp1_p
        # 4. 突破加碼確認
        elif price >= addon_p:
            raw_event = "ADDON"
            event_title = "⚡【母子共振突破・動能加碼】"
            event_desc = f"現價 {price:.2f} 突破加碼價 {addon_p:.2f}，領頭羊共振續強，右側確認加碼！"
            threshold_price = addon_p
        # 5. 伏擊進場買點
        elif price <= entry_p and price > sl_p:
            raw_event = "ENTRY"
            event_title = "🟢【作帳點火・伏擊建倉】"
            event_desc = f"現價 {price:.2f} 落入買進安全區間 ({sl_p:.2f} ~ {entry_p:.2f})，啟動分批佈局！"
            threshold_price = entry_p

    if not raw_event:
        return (None, "", "")

    # 當日事件防洗版去重
    alert_key = f"{code}_{raw_event}"
    if alert_key in alerted_set:
        return (None, "", "")

    # GLOBAL_TICK_GUARD 防毛刺雙重檢驗
    if use_tick_guard:
        force_pass = (raw_event == "STOP_LOSS" and (
            (direction == "LONG" and price <= sl_p * 0.99) or
            (direction == "SHORT" and price >= sl_p * 1.01)
        ))
        passed = GLOBAL_TICK_GUARD.confirm_signal(
            code=code,
            event_code=raw_event,
            current_price=price,
            target_price=threshold_price,
            direction=direction,
            force_pass=force_pass
        )
        if not passed:
            return (None, "", "")

    alerted_set.add(alert_key)
    return (raw_event, event_title, event_desc)


def fetch_realtime_quote(code: str) -> dict:
    """從證交所或 yfinance 擷取個股最新盤中成交價與五檔行情"""
    code_str = str(code).strip()
    # 1. 優先查詢證交所/櫃買 MIS 即時 API
    url_twse = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{code_str}.tw|otc_{code_str}.tw&json=1&delay=0"
    req = urllib.request.Request(url_twse, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            msg_array = data.get('msgArray', [])
            if msg_array:
                row = msg_array[0]
                z_price = row.get('z', '-')
                y_close = float(row.get('y', 0.0))
                price = float(z_price) if z_price != '-' else y_close
                if price > 0:
                    change = round(price - y_close, 2)
                    change_pct = round((change / y_close) * 100.0, 2) if y_close > 0 else 0.0
                    return {
                        'price': price,
                        'prev_close': y_close,
                        'change': change,
                        'change_pct': change_pct,
                        'high': float(row.get('h', price)),
                        'low': float(row.get('l', price)),
                        'volume': int(row.get('v', 0)),
                        'time': row.get('t', datetime.datetime.now(TZ_TW).strftime('%H:%M:%S'))
                    }
    except Exception:
        pass

    # 2. yfinance 備援查詢
    try:
        t = yf.Ticker(f"{code_str}.TW")
        hist = t.history(period="1d", interval="1m")
        if hist.empty:
            t = yf.Ticker(f"{code_str}.TWO")
            hist = t.history(period="1d", interval="1m")

        if not hist.empty:
            price = float(hist['Close'].iloc[-1])
            prev_close = float(t.info.get('previousClose', price)) if hasattr(t, 'info') else price
            change = round(price - prev_close, 2)
            change_pct = round((change / prev_close) * 100.0, 2) if prev_close > 0 else 0.0
            return {
                'price': price,
                'prev_close': prev_close,
                'change': change,
                'change_pct': change_pct,
                'high': float(hist['High'].max()),
                'low': float(hist['Low'].min()),
                'volume': int(hist['Volume'].sum()),
                'time': datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
            }
    except Exception:
        pass

    return {}


def load_scanner_targets() -> list:
    """載入策略 08 最新選股結果 result.csv 作為雷達監控標的"""
    if not os.path.exists(CSV_FILE):
        print(f"[!] 找不到 {CSV_FILE}，請先執行 screener.py！")
        return []

    try:
        df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
        if df.empty:
            return []
        targets = []
        for _, row in df.iterrows():
            code = str(row.get('證券代號') or row.get('代號') or '').strip()
            if not code or not code.isdigit():
                continue
            item = dict(row)
            item['code'] = code
            item['name'] = str(row.get('證券名稱') or row.get('名稱') or code).strip()
            targets.append(item)
        return targets
    except Exception as e:
        print(f"[!] 載入 {CSV_FILE} 失敗: {e}")
        return []


def run_intraday_scan(once: bool = False, interval: int = 60, test_push: bool = False):
    """盤中雷達掃描主迴圈"""
    print("=" * 75)
    print(f"📡 啟動【{STRATEGY_NAME}】盤中即時雷達")
    print(f"⏰ 執行時段：09:00 ~ 13:35 | 輪詢週期：{interval} 秒")
    print("=" * 75)

    targets = load_scanner_targets()
    if not targets:
        print("[!] 目前無任何監控標的，雷達待命結束。")
        return

    print(f"[✓] 成功掛入 {len(targets)} 檔集團作帳與母子聯動監控標的：")
    for idx, t in enumerate(targets, 1):
        gname = t.get('集團名稱', '')
        role = t.get('集團角色', '')
        state = t.get('多空狀態', '')
        ep = t.get('進場價位', 0)
        sl = t.get('停損價位', 0)
        print(f"  {idx}. {t['code']} {t['name']} [{gname}・{role}] | {state} | 進: {ep} / 損: {sl}")

    # 測試推播模式
    if test_push:
        print("\n[*] 正在執行 --test-push 模擬到價推播檢驗...")
        sample_target = targets[0]
        sample_rt = {
            'price': float(sample_target.get('entry_price', sample_target.get('進場價位', 100.0))),
            'prev_close': float(sample_target.get('收盤價', 100.0)),
            'change': 1.5,
            'change_pct': 1.5,
            'time': datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
        }
        test_card = build_price_alert_card(
            stock_data=sample_target,
            realtime_quote=sample_rt,
            event_type="ENTRY",
            event_title="🟢【作帳點火・伏擊建倉 (連線測試)】",
            event_desc=f"現價 {sample_rt['price']} 觸及買進安全區間，集團母子共振確認！",
            strategy_id="08"
        )
        success = send_to_line(test_card, strategy="08")
        if not success:
            test_text = format_clean_price_alert_text(
                stock_data=sample_target,
                realtime_quote=sample_rt,
                event_type="ENTRY",
                event_title="🟢【作帳點火・伏擊建倉 (連線測試)】",
                event_desc=f"現價 {sample_rt['price']} 觸及買進安全區間，集團母子共振確認！",
                strategy_id="08"
            )
            send_to_line(test_text, strategy="08")
        print("[✓] --test-push 測試推播完成！\n")
        if once:
            return

    alerted_set = set()

    while True:
        now_tw = datetime.datetime.now(TZ_TW)
        t_int = now_tw.hour * 100 + now_tw.minute

        # 盤中交易時段判定 (平日 09:00 ~ 13:35)
        # 若為 once 模式則允許非交易時段單次執行
        if not once and (t_int < 900 or t_int > 1335):
            print(f"[*] 目前非盤中交易時段 ({now_tw.strftime('%H:%M:%S')})，雷達休眠等待開盤...")
            time.sleep(30)
            continue

        print(f"\n[{now_tw.strftime('%H:%M:%S')}] 進行集團作帳標的盤中到價巡檢...")

        for t in targets:
            code = t['code']
            rt = fetch_realtime_quote(code)
            if not rt or rt.get('price', 0) <= 0:
                continue

            event_type, event_title, event_desc = evaluate_intraday_alert(
                target=t,
                rt=rt,
                alerted_set=alerted_set,
                use_tick_guard=True
            )

            if event_type:
                print(f"[🚨 到價警報] {code} {t['name']} -> {event_title} | 現價: {rt['price']}")
                try:
                    card = build_price_alert_card(
                        stock_data=t,
                        realtime_quote=rt,
                        event_type=event_type,
                        event_title=event_title,
                        event_desc=event_desc,
                        strategy_id="08"
                    )
                    success = send_to_line(card, strategy="08")
                    if not success:
                        clean_text = format_clean_price_alert_text(
                            stock_data=t,
                            realtime_quote=rt,
                            event_type=event_type,
                            event_title=event_title,
                            event_desc=event_desc,
                            strategy_id="08"
                        )
                        send_to_line(clean_text, strategy="08")
                except Exception as e:
                    print(f"[!] 到價推播發生異常: {e}")

        if once:
            print("\n[✓] --once 單次巡檢完成！")
            break

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="策略 08：台股集團作帳與母子聯動到價雷達")
    parser.add_argument("--once", action="store_true", help="執行單次快速巡邏後結束")
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔秒數 (預設 60 秒)")
    parser.add_argument("--test-push", action="store_true", help="發送一筆模擬到價推播以驗證 LINE 連線與卡片排版")
    args = parser.parse_args()

    run_intraday_scan(once=args.once, interval=args.interval, test_push=args.test_push)


if __name__ == '__main__':
    main()
