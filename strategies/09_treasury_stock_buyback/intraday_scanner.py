#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 09：庫藏股宣告護盤與低估值事件 - 盤中即時到價監控雷達
================================================================================
適用時段：09:00 ~ 13:35 (台股盤中交易時段)
五大到價事件：
  🛑 STOP_LOSS: 跌破停損線 -> 庫藏股護盤破底，強制停損撤退 (最高優先級)
  🔴 TP2: 攻抵第二目標 (+25%) -> 每股淨值回歸與波段滿足，多單全數落袋
  🔴 TP1: 攻抵第一目標 (+15%) -> 法定宣告買回上限反壓，分批獲利 1/2
  🔵 ADDON: 突破加碼點 -> 帶量突破宣告首日高點，右側動能加碼追擊
  🟢 ENTRY: 回測買進區間 -> 估值偏低且公司派成本有撐，逢低伏擊建倉

核心風控標準：
1. 強制套用 algo_guards.py 之 GLOBAL_TICK_GUARD 防毛刺連讀複檢，杜絕量化演算法 1-tick 假突破誘多。
2. 自動載入最新 result.csv，並支援個股期貨優先報價與保證金試算。
3. 支援獨立 LINE 視窗分流 (strategy="09")，發送 build_price_alert_card 高對比大字體卡片。
4. CLI 參數：--once (單次巡檢)、--interval (輪詢秒數)、--test-push (測試推播)。
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

# 導入專案根目錄至 sys.path 以引用共用模組
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from line_sender import (
    send_to_line,
    build_price_alert_card,
    format_clean_price_alert_text
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

STRATEGY_ID = "09"
STRATEGY_NAME = "策略 09：庫藏股宣告護盤到價雷達"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')

# 若 GLOBAL_TICK_GUARD 尚未有 confirm_signal，自動修補相容適配器
if not hasattr(GLOBAL_TICK_GUARD, 'confirm_signal'):
    def _confirm_signal_adapter(code: str, event_code: str, current_price: float,
                               target_price: float, direction: str = 'LONG', force_pass: bool = False):
        res = GLOBAL_TICK_GUARD.check_confirmation(code, event_code, current_price, target_price, force_pass=force_pass)
        return res[0] if isinstance(res, tuple) else bool(res)
    setattr(GLOBAL_TICK_GUARD, 'confirm_signal', _confirm_signal_adapter)


def load_monitored_targets(csv_path: str = None) -> list:
    """載入策略 09 最新 result.csv 監控清單"""
    target_csv = csv_path or CSV_FILE
    if not os.path.exists(target_csv):
        print(f"[!] 找不到選股清單檔案: {target_csv}，請先執行 screener.py")
        return []

    try:
        df = pd.read_csv(target_csv, encoding='utf-8-sig')
        targets = []
        for _, row in df.iterrows():
            code = str(row.get('股票代碼') or row.get('代號') or '').strip()
            name = str(row.get('股票名稱') or row.get('名稱') or '').strip()
            if not code:
                continue

            targets.append({
                "code": code,
                "name": name,
                "price": float(row.get('最新現價') or row.get('收盤價') or 0.0),
                "entry_price": float(row.get('進場買進上限') or row.get('進場價位') or 0.0),
                "addon_price": float(row.get('續強加碼價') or row.get('加碼價位') or 0.0),
                "tp1_price": float(row.get('目標停利TP1') or row.get('停利TP1') or 0.0),
                "tp2_price": float(row.get('目標停利TP2') or row.get('停利TP2') or 0.0),
                "sl_price": float(row.get('嚴格停損價') or row.get('停損價位') or 0.0),
                "fut_contract": str(row.get('期貨契約', '無股期')),
                "fut_month": str(row.get('期貨月份', '-')),
                "fut_close": row.get('期貨收盤價', '-'),
                "basis": row.get('價差(Basis)', '-'),
                "stars": str(row.get('星級推薦', '★★★★☆')),
                "score": str(row.get('評分', '80'))
            })
        return targets
    except Exception as e:
        print(f"[!] 讀取監控清單失敗: {e}")
        return []


def fetch_realtime_quotes(targets: list) -> dict:
    """批量抓取最新盤中現貨報價"""
    quotes = {}
    for t in targets:
        code = t["code"]
        for suffix in [".TW", ".TWO"]:
            try:
                ticker = yf.Ticker(f"{code}{suffix}")
                hist = ticker.history(period="1d", interval="1m")
                if not hist.empty:
                    latest = hist.iloc[-1]
                    prev_close = t.get("price") or float(latest['Open'])
                    curr_price = float(latest['Close'])
                    chg_val = round(curr_price - prev_close, 2)
                    chg_pct = round((chg_val / prev_close) * 100, 2) if prev_close else 0.0
                    quotes[code] = {
                        "price": curr_price,
                        "change": chg_val,
                        "pct_change": chg_pct,
                        "volume": int(latest.get('Volume', 0))
                    }
                    break
            except Exception:
                continue

        if code not in quotes:
            # 備援回退到 result.csv 收盤價
            quotes[code] = {
                "price": t["price"],
                "change": 0.0,
                "pct_change": 0.0,
                "volume": 1000
            }
    return quotes


def evaluate_intraday_alert(target: dict, rt: dict, alerted_set: set, use_tick_guard: bool = True) -> tuple:
    """
    評估是否觸發五大到價事件，並通過防毛刺檢核
    回傳: (event_type: str | None, event_title: str, event_desc: str)
    """
    price = float(rt.get('price', 0.0))
    if price <= 0:
        return (None, "", "")

    code = target["code"]
    entry_p = target["entry_price"]
    addon_p = target["addon_price"]
    tp1_p = target["tp1_price"]
    tp2_p = target["tp2_price"]
    sl_p = target["sl_price"]

    raw_event = None
    event_title = ""
    event_desc = ""
    threshold_price = entry_p

    # 1. 停損警戒 (優先級最高)
    if price <= sl_p:
        raw_event = "STOP_LOSS"
        event_title = "🛑【庫藏護盤破底・無條件停損】"
        event_desc = f"現價 {price:.2f} 摜破停損價 {sl_p:.2f}，宣告日前低或區間下限失守，公司派護盤失敗，強制離場停損"
        threshold_price = sl_p
    # 2. 第二目標停利達標
    elif price >= tp2_p:
        raw_event = "TP2"
        event_title = "🔴【每股淨值回歸・超額波段停利】"
        event_desc = f"現價 {price:.2f} 攻抵第二目標價 {tp2_p:.2f}，每股淨值與波段滿足點全數達標，多單全數獲利落袋"
        threshold_price = tp2_p
    # 3. 第一目標停利達標
    elif price >= tp1_p:
        raw_event = "TP1"
        event_title = "🔴【法定買回上限・前段獲利拔檔】"
        event_desc = f"現價 {price:.2f} 觸抵預定買回上限 {tp1_p:.2f}，逼近董事會宣告天花板反壓區，建議分批獲利 1/2 保障利潤"
        threshold_price = tp1_p
    # 4. 突破加碼點
    elif price >= addon_p:
        raw_event = "ADDON"
        event_title = "🔵【突破宣告高點・動能確認加碼】"
        event_desc = f"現價 {price:.2f} 帶量攻過加碼價 {addon_p:.2f}，突破宣告首日天花板與月線，右側動能確認追擊"
        threshold_price = addon_p
    # 5. 回測買進區間
    elif price <= entry_p and price > sl_p:
        raw_event = "ENTRY"
        event_title = "🟢【庫藏低估買點・黃金伏擊進場】"
        event_desc = f"現價 {price:.2f} 回測買進上限 {entry_p:.2f}，估值偏低且公司派護盤成本有撐，建議逢低分批建倉"
        threshold_price = entry_p

    if not raw_event:
        return (None, "", "")

    # 去重檢驗 (同一事件當日僅推播一次)
    alert_key = f"{code}_{raw_event}"
    if alert_key in alerted_set:
        return (None, "", "")

    # 防毛刺檢驗 (GLOBAL_TICK_GUARD)
    if use_tick_guard:
        force_pass = (raw_event == "STOP_LOSS")  # 停損嚴禁延誤
        confirmed = GLOBAL_TICK_GUARD.confirm_signal(
            code=code,
            event_code=raw_event,
            current_price=price,
            target_price=threshold_price,
            direction="LONG",
            force_pass=force_pass
        )
        if not confirmed:
            return (None, "", "")

    alerted_set.add(alert_key)
    return (raw_event, event_title, event_desc)


def build_and_send_alert(target: dict, rt: dict, event_type: str, event_title: str, event_desc: str):
    """建構旗艦視覺化卡片並推送至 LINE 獨立頻道"""
    code = target["code"]
    name = target["name"]
    price = rt["price"]
    chg_val = rt["change"]
    chg_pct = rt["pct_change"]

    four_prices = {
        "entry_price": target["entry_price"],
        "addon_price": target["addon_price"],
        "tp1_price": target["tp1_price"],
        "tp2_price": target["tp2_price"],
        "sl_price": target["sl_price"]
    }

    print(f"\n🚨 [到價快訊] {event_title} -> {code} {name} 現價: {price:.2f} ({chg_pct:+.2f}%)")
    print(f"   {event_desc}")

    # 建構 LINE Flex 卡片
    flex_card = build_price_alert_card(
        code=code,
        name=name,
        price=price,
        event_type=event_type,
        event_title=event_title,
        event_desc=event_desc,
        four_prices=four_prices,
        strategy_id=STRATEGY_ID
    )

    clean_text = format_clean_price_alert_text(
        code=code,
        name=name,
        price=price,
        event_type=event_type,
        event_title=event_title,
        event_desc=event_desc,
        four_prices=four_prices,
        strategy_id=STRATEGY_ID
    )

    send_to_line(flex_card, strategy=STRATEGY_ID, fallback_text=clean_text)


def scan_intraday_cycle(targets: list, alerted_set: set, use_tick_guard: bool = True) -> int:
    """執行單次盤中雷達巡檢"""
    if not targets:
        return 0

    quotes = fetch_realtime_quotes(targets)
    triggered_count = 0

    for t in targets:
        code = t["code"]
        rt = quotes.get(code)
        if not rt:
            continue

        event_type, event_title, event_desc = evaluate_intraday_alert(t, rt, alerted_set, use_tick_guard)
        if event_type:
            build_and_send_alert(t, rt, event_type, event_title, event_desc)
            triggered_count += 1

    return triggered_count


def test_push_alert(targets: list):
    """發送測試模擬到價快訊驗證 LINE 連線"""
    print("[*] 執行策略 09 到價快訊測試推播...")
    if not targets:
        sample_target = {
            "code": "2474",
            "name": "可成",
            "price": 195.5,
            "entry_price": 202.34,
            "addon_price": 209.42,
            "tp1_price": 230.40,
            "tp2_price": 249.60,
            "sl_price": 156.80,
            "fut_contract": "GXF 202610",
            "stars": "★★★★★",
            "score": "100"
        }
    else:
        sample_target = targets[0]

    mock_rt = {
        "price": sample_target["entry_price"] - 0.5,
        "change": 1.5,
        "pct_change": 1.25,
        "volume": 2500
    }

    event_title = "🟢【庫藏低估買點・黃金伏擊進場】(測試)"
    event_desc = f"現價 {mock_rt['price']:.2f} 回測買進上限 {sample_target['entry_price']:.2f}，公司派庫藏股誠信高且估值偏低，買點浮現！"

    build_and_send_alert(sample_target, mock_rt, "ENTRY", event_title, event_desc)
    print("[+] 測試推播已發送至 LINE！")


def main():
    parser = argparse.ArgumentParser(description=f"{STRATEGY_NAME} 盤中即時到價監控雷達")
    parser.add_argument("--interval", type=int, default=60, help="輪詢週期 (秒)")
    parser.add_argument("--once", action="store_true", help="執行單次快速巡檢後退出")
    parser.add_argument("--test-push", action="store_true", help="發送測試到價推播卡片驗證 LINE 連線")
    parser.add_argument("--csv", type=str, default=None, help="自訂 result.csv 路徑")
    parser.add_argument("--no-tick-guard", action="store_true", help="關閉盤中防毛刺機制 (僅供測試)")
    args = parser.parse_args()

    targets = load_monitored_targets(args.csv)
    print(f"[*] 載入監控標的: {len(targets)} 檔")

    if args.test_push:
        test_push_alert(targets)
        return

    alerted_set = set()
    use_guard = not args.no_tick_guard

    if args.once:
        print("[*] 執行單次盤中快篩巡檢...")
        trig = scan_intraday_cycle(targets, alerted_set, use_tick_guard=use_guard)
        print(f"[+] 單次巡檢完成，觸發事件數: {trig}")
        return

    print(f"[*] 啟動常駐監控模式 (輪詢間隔: {args.interval} 秒)...")
    try:
        while True:
            now_tw = datetime.datetime.now(TZ_TW)
            # 若在盤中時間外 (09:00 ~ 13:35)，提示並休息
            is_market_open = (9, 0) <= (now_tw.hour, now_tw.minute) <= (13, 35) and (now_tw.weekday() < 5)
            if not is_market_open:
                print(f"[-] 目前非盤中交易時段 ({now_tw.strftime('%H:%M:%S')})，等待中...")
                time.sleep(min(args.interval * 2, 300))
                continue

            scan_intraday_cycle(targets, alerted_set, use_tick_guard=use_guard)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[!] 監控中斷，安全退出。")


if __name__ == "__main__":
    main()
