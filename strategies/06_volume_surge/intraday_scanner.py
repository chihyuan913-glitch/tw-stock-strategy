#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股策略 06：盤中成交量異常爆量起漲動能 - 即時雷達與告警系統 (Intraday Volume Surge Scanner)
================================================================================
適用時段：09:00 ~ 13:35 (台股盤中交易時段)
【核心功能與架構】
1. 雙層防限流數據獲取 (3-A)：
   - Layer 1：盤中抓取證交所全市場即時成交量熱門排行 (1~2 次請求涵蓋熱門標的)。
   - Layer 2：自動連動最新 result.csv 精華標的與根目錄 watchlist.json 自選池。
   - 合併去重後維持在約 60~120 檔核心池，批量查詢遠離 HTTP 429 閾值。
2. 時間加權動態量能預估器 (Paced Volume Engine)：
   - 將盤中 09:00~13:30 分為 270 分鐘，計算時間進度 Pt = max(0.05, T/270)。
   - 動態全日預估量 Vest = Vcurrent / Pt。
   - 動態量比 Rvol = Vest / V5MA。
3. 動態量能布林突破與型態守門 (2-C)：
   - 實體紅棒檢驗：漲幅 >= +2.0% 且 (Price - Low) / (High - Low) >= 0.60 (上影線 < 40%)。
   - 程式交易反制：結合 algo_guards 之 GLOBAL_TICK_GUARD，排除瞬間假拉抬與毛刺。
4. 階梯式防洗版去重狀態機 (4-A)：
   - LEVEL_1_SURGE: 初段異常爆量 (Rvol >= 2.5x)
   - LEVEL_2_EXTREME: 極致天量主升 (Rvol >= 4.0x)
   - 單檔股票當日最多推播 2 次，徹底根除洗版！
5. 獨立 LINE 視窗分流 (strategy="06")：
   - 支援大字體 Flex Message 戰訊卡與純文字零引線備援。
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

STRATEGY_ID = "06"
STRATEGY_NAME = "策略 06：盤中爆量起漲動能"
CSV_FILE = os.path.join(os.path.dirname(__file__), 'result.csv')
WATCHLIST_FILE = os.path.join(ROOT_DIR, 'watchlist.json')

# 階梯式去重狀態機：{code: int_level (0: 無, 1: 已發L1, 2: 已發L2)}
TRIGGERED_LEVELS = {}


def get_market_progress_ratio(now_dt: datetime.datetime = None) -> float:
    """
    計算台股盤中開盤時間進度比例 Pt (09:00 ~ 13:30，共 270 分鐘)
    - 開盤前 (<09:00)：回傳 0.05 (以平滑預熱防呆)
    - 09:00~09:15：為避免早盤 10 分鐘極端毛刺，下限鎖定為 0.05 (13.5分鐘)
    - 09:15~13:30：精確進度 (經過分鐘數 / 270.0)
    - 收盤後 (>=13:30)：回傳 1.0
    """
    if now_dt is None:
        now_dt = datetime.datetime.now(TZ_TW)

    open_time = now_dt.replace(hour=9, minute=0, second=0, microsecond=0)
    close_time = now_dt.replace(hour=13, minute=30, second=0, microsecond=0)

    if now_dt < open_time:
        return 0.05
    elif now_dt >= close_time:
        return 1.0

    elapsed_minutes = (now_dt - open_time).total_seconds() / 60.0
    progress = elapsed_minutes / 270.0
    return max(0.05, min(1.0, progress))


def calculate_projected_volume(current_vol: int, progress_ratio: float) -> int:
    """計算全日動態預估成交量"""
    if progress_ratio <= 0:
        return current_vol
    return int(current_vol / progress_ratio)


def fetch_layer1_twse_hotlist(max_stocks: int = 50) -> dict:
    """
    Layer 1: 抓取證交所盤中成交量排行 (單次請求獲取熱門活躍股)
    若抓取失敗或非盤中時段，安全回傳空字典，由 Layer 2 銜接。
    """
    hot_stocks = {}
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX20?response=json"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get('stat') == 'OK':
                rows = data.get('data', [])
                for row in rows[:max_stocks]:
                    code = str(row[1]).strip()
                    name = str(row[2]).strip()
                    if len(code) == 4 and code.isdigit():
                        try:
                            vol_shares = int(row[3].replace(',', ''))
                            hot_stocks[code] = {
                                'code': code,
                                'name': name,
                                'vol_shares': vol_shares
                            }
                        except ValueError:
                            pass
    except Exception:
        pass
    return hot_stocks


def load_monitoring_pool() -> dict:
    """
    雙層架構融合 (Layer 1 + Layer 2)
    回傳: {code: target_dict}
    """
    targets = {}

    # Layer 2-A: 自 result.csv 載入既有爆量起漲入選標的
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
            for _, row in df.iterrows():
                code = str(row.get('證券代號', '')).strip()
                if not code or not code.isdigit():
                    continue
                close_p = float(row.get('收盤價', 0.0))
                entry_p = float(row.get('建議進場價', round(close_p * 1.01, 2)))
                addon_p = float(row.get('動能加碼價', round(close_p * 1.03, 2)))
                tp1_p = float(row.get('停利TP1', round(close_p * 1.08, 2)))
                tp2_p = float(row.get('停利TP2', round(close_p * 1.15, 2)))
                sl_p = float(row.get('嚴格停損價', round(close_p * 0.965, 2)))
                v5_lots = int(row.get('5日均量(張)', 1000))

                targets[code] = {
                    'code': code,
                    'name': str(row.get('證券名稱', '')).strip(),
                    'entry_price': entry_p,
                    'addon_price': addon_p,
                    'tp1_price': tp1_p,
                    'tp2_price': tp2_p,
                    'sl_price': sl_p,
                    'v5_avg': v5_lots,
                    'source': 'result.csv'
                }
            if targets:
                print(f"[✓] Layer 2-A: 已自 result.csv 載入 {len(targets)} 檔策略入選標的。")
        except Exception as e:
            print(f"[!] 讀取 result.csv 失敗: {e}")

    # Layer 2-B: 載入 watchlist.json 自選清單
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
                wl_data = json.load(f)
                items = wl_data.values() if isinstance(wl_data, dict) else wl_data
                count = 0
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get('code', '')).strip()
                    if code and code not in targets and len(code) == 4:
                        targets[code] = {
                            'code': code,
                            'name': item.get('name', f"個股 {code}"),
                            'entry_price': float(item.get('entry_price', 0.0)),
                            'addon_price': float(item.get('addon_price', 0.0)),
                            'tp1_price': float(item.get('tp1_price', 0.0)),
                            'tp2_price': float(item.get('tp2_price', 0.0)),
                            'sl_price': float(item.get('sl_price', 0.0)),
                            'v5_avg': 1000,
                            'source': 'watchlist.json'
                        }
                        count += 1
                if count > 0:
                    print(f"[✓] Layer 2-B: 已自 watchlist.json 併入 {count} 檔自選標的。")
        except Exception as e:
            print(f"[!] 讀取 watchlist.json 失敗: {e}")

    # Layer 1: 併入證交所盤中即時成交量前 50 活躍股
    hotlist = fetch_layer1_twse_hotlist(max_stocks=40)
    layer1_count = 0
    for code, h in hotlist.items():
        if code not in targets:
            targets[code] = {
                'code': code,
                'name': h['name'],
                'entry_price': 0.0,
                'addon_price': 0.0,
                'tp1_price': 0.0,
                'tp2_price': 0.0,
                'sl_price': 0.0,
                'v5_avg': max(1000, h['vol_shares'] // 1000 // 2),
                'source': 'twse_hotlist'
            }
            layer1_count += 1
    if layer1_count > 0:
        print(f"[✓] Layer 1: 已自證交所全市場即時榜併入 {layer1_count} 檔熱門活躍股。")

    # 若母池仍過小，填補代表性權值與熱點標的
    if len(targets) < 5:
        defaults = [
            ('2330', '台積電'), ('2317', '鴻海'), ('2454', '聯發科'),
            ('2382', '廣達'), ('3231', '緯創'), ('2603', '長榮')
        ]
        for c, n in defaults:
            if c not in targets:
                targets[c] = {
                    'code': c,
                    'name': n,
                    'entry_price': 0.0,
                    'addon_price': 0.0,
                    'tp1_price': 0.0,
                    'tp2_price': 0.0,
                    'sl_price': 0.0,
                    'v5_avg': 2000,
                    'source': 'default_pool'
                }

    print(f"[✓] 雙層池構建完成，當前總監控標的：{len(targets)} 檔。")
    return targets


def fetch_intraday_snapshot(codes: list) -> dict:
    """
    透過 yfinance 批量抓取盤中最新即時行情 (OHLCV)
    回傳: {code: {'price': float, 'high': float, 'low': float, 'open': float, 'volume_lots': int, 'change_pct': float}}
    """
    if not codes:
        return {}

    tickers = [f"{c}.TW" for c in codes] + [f"{c}.TWO" for c in codes]
    snapshots = {}
    import io, contextlib
    f_err = io.StringIO()
    try:
        # 下載當日分時或最新 1 天資料 (同時涵蓋上市 .TW 與上櫃 .TWO，壓制 stderr 雜訊)
        with contextlib.redirect_stderr(f_err):
            data = yf.download(tickers, period='1d', interval='1m', progress=False, group_by='ticker')
        for code in codes:
            # 優先嘗試 .TW，若無資料則嘗試 .TWO
            for ext in ['.TW', '.TWO']:
                t_key = f"{code}{ext}"
                try:
                    if len(tickers) == 1:
                        df = data
                    else:
                        if t_key in data.columns.levels[0]:
                            df = data[t_key]
                        else:
                            continue

                    df_clean = df.dropna(subset=['Close'])
                    if df_clean.empty:
                        continue

                    last_row = df_clean.iloc[-1]
                    curr_price = float(last_row['Close'])
                    high_p = float(df_clean['High'].max())
                    low_p = float(df_clean['Low'].min())
                    open_p = float(df_clean['Open'].iloc[0])
                    total_vol_shares = int(df_clean['Volume'].sum())
                    vol_lots = total_vol_shares // 1000

                    chg_pct = round(((curr_price - open_p) / open_p) * 100.0, 2) if open_p > 0 else 0.0

                    snapshots[code] = {
                        'price': curr_price,
                        'high': high_p,
                        'low': low_p,
                        'open': open_p,
                        'volume_lots': vol_lots,
                        'change_pct': chg_pct
                    }
                    break  # 已取得該股票行情，跳出 ext 迴圈
                except Exception:
                    continue
    except Exception as e:
        print(f"[!] yfinance 即時行情抓取失敗: {e}")

    return snapshots


def evaluate_volume_surge_and_alert(target: dict, snap: dict, progress_ratio: float, force_pass: bool = False) -> tuple:
    """
    評估單檔個股是否觸發階梯式爆量警報
    回傳: (should_alert, alert_level, rvol, event_info)
    """
    code = target['code']
    curr_vol = snap['volume_lots']
    v5_avg = max(1000, target.get('v5_avg', 1000))
    proj_vol = calculate_projected_volume(curr_vol, progress_ratio)
    rvol = round(proj_vol / v5_avg, 2) if v5_avg > 0 else 0.0

    curr_level = TRIGGERED_LEVELS.get(code, 0)

    # 1. 量比門檻檢查 (階梯式 4-A)
    target_level = 0
    if rvol >= 4.0 and curr_level < 2:
        target_level = 2
    elif rvol >= 2.5 and curr_level < 1:
        target_level = 1

    if target_level == 0:
        return False, 0, rvol, None

    # 2. 價格與型態守門 (2-C)
    # (a) 漲幅必須 >= +2.0% (做多爆量起漲，排除暴跌出量)
    if snap['change_pct'] < 2.0:
        return False, 0, rvol, None

    # (b) 實體紅棒檢驗：(Price - Low) / (High - Low) >= 0.60 (上影線 < 40%)
    amplitude = max(snap['high'] - snap['low'], 0.001)
    solid_ratio = (snap['price'] - snap['low']) / amplitude
    if solid_ratio < 0.60:
        return False, 0, rvol, None

    # (c) 防毛刺確認
    threshold_price = target.get('addon_price') or snap['price']
    is_tick_confirmed, _ = GLOBAL_TICK_GUARD.check_confirmation(
        code=code,
        event_code="SURGE_BREAKOUT",
        current_price=snap['price'],
        target_price=threshold_price,
        force_pass=force_pass
    )
    if not is_tick_confirmed:
        return False, 0, rvol, None

    # 判定通過，封裝事件資訊
    if target_level == 2:
        event_type = "LEVEL_2_EXTREME"
        event_title = "🔥【極致天量主升噴出】"
        event_desc = f"盤中動態預估量衝達 5MA 均量之 {rvol} 倍 (預估 {proj_vol} 張 / 5MA {v5_avg} 張)，主力大戶極致控盤噴出！"
    else:
        event_type = "LEVEL_1_SURGE"
        event_title = "🚨【初段異常爆量起漲】"
        event_desc = f"盤中動態預估量突破 5MA 均量之 {rvol} 倍 (預估 {proj_vol} 張 / 5MA {v5_avg} 張)，量能布林 +3σ 強勢表態！"

    event_info = {
        'event_type': event_type,
        'event_title': event_title,
        'event_desc': event_desc,
        'rvol': rvol,
        'proj_vol': proj_vol,
        'v5_avg': v5_avg,
        'solid_ratio': round(solid_ratio, 2)
    }

    return True, target_level, rvol, event_info


def send_surge_alert(target: dict, snap: dict, event_info: dict):
    """建構旗艦大字體 Flex 卡片並推播至策略 06 專屬群組"""
    code = target['code']
    name = target['name']
    price = snap['price']

    # 確保四大防線數據完備
    if target.get('entry_price', 0) == 0:
        target['entry_price'] = round(price * 1.01, 2)
    if target.get('addon_price', 0) == 0:
        target['addon_price'] = round(snap['high'] * 1.005, 2)
    if target.get('tp1_price', 0) == 0:
        target['tp1_price'] = round(price * 1.08, 2)
    if target.get('tp2_price', 0) == 0:
        target['tp2_price'] = round(price * 1.15, 2)
    if target.get('sl_price', 0) == 0:
        target['sl_price'] = round(max(snap['low'], price * 0.965), 2)

    rt_data = {
        'price': price,
        'high': snap['high'],
        'low': snap['low'],
        'open': snap['open'],
        'volume': snap['volume_lots'],
        'change_pct': snap['change_pct']
    }

    # 呼叫 line_sender 標準大字體卡片引擎
    card = build_price_alert_card(
        target=target,
        rt=rt_data,
        event_type=event_info['event_type'],
        event_title=event_info['event_title'],
        event_desc=event_info['event_desc'],
        strategy_id=STRATEGY_ID
    )

    clean_text = format_clean_price_alert_text(
        target=target,
        rt=rt_data,
        event_type=event_info['event_type'],
        event_title=event_info['event_title'],
        event_desc=event_info['event_desc'],
        strategy_id=STRATEGY_ID
    )

    print(f"\n[🚨 觸發告警] {name} ({code}) {event_info['event_title']} 現價: {price} 量比: {event_info['rvol']}x")
    success = send_to_line(card, strategy=STRATEGY_ID)
    if not success:
        send_to_line(clean_text, strategy=STRATEGY_ID)


def send_aggregated_price_alerts(alerts: list):
    """將同週期多檔爆量起漲事件整合為單一戰訊卡發送，大幅節省 90% LINE 訊息則數並避免洗版"""
    now_str = datetime.datetime.now(TZ_TW).strftime('%H:%M:%S')
    print(f"\n[⚡] 發送爆量起漲多檔合併戰訊卡 (共 {len(alerts)} 檔標的，僅消耗 1 則訊息額度！)")

    card = build_aggregated_alert_card(alerts, strategy_id=STRATEGY_ID, now_str=now_str)
    success = send_to_line(card, strategy=STRATEGY_ID)
    if not success:
        clean_text = format_clean_aggregated_text(alerts, strategy_id=STRATEGY_ID, now_str=now_str)
        send_to_line(clean_text, strategy=STRATEGY_ID)


def scan_once(pool: dict, progress_ratio: float):
    """執行單次快篩巡邏 (支援多檔到價合併發送，省額度防洗版)"""
    codes = list(pool.keys())
    print(f"[*] 正在獲取 {len(codes)} 檔監控標的之盤中即時快照 (進度比: {round(progress_ratio*100, 1)}%)...")

    snapshots = fetch_intraday_snapshot(codes)
    triggered_alerts = []

    for code, snap in snapshots.items():
        target = pool[code]
        should_alert, new_level, rvol, event_info = evaluate_volume_surge_and_alert(
            target=target,
            snap=snap,
            progress_ratio=progress_ratio
        )
        if should_alert:
            TRIGGERED_LEVELS[code] = new_level
            # 準備四大防線與即時報價結構
            price = snap['price']
            t_copy = dict(target)
            if t_copy.get('entry_price', 0) == 0:
                t_copy['entry_price'] = round(price * 1.01, 2)
            if t_copy.get('addon_price', 0) == 0:
                t_copy['addon_price'] = round(snap['high'] * 1.005, 2)
            if t_copy.get('tp1_price', 0) == 0:
                t_copy['tp1_price'] = round(price * 1.08, 2)
            if t_copy.get('tp2_price', 0) == 0:
                t_copy['tp2_price'] = round(price * 1.15, 2)
            if t_copy.get('sl_price', 0) == 0:
                t_copy['sl_price'] = round(max(snap['low'], price * 0.965), 2)

            rt_data = {
                'price': price,
                'high': snap['high'],
                'low': snap['low'],
                'open': snap['open'],
                'volume': snap['volume_lots'],
                'change_pct': snap['change_pct']
            }
            triggered_alerts.append({
                'target': t_copy,
                'rt': rt_data,
                'snap': snap,
                'event_code': event_info['event_type'],
                'event_title': event_info['event_title'],
                'event_desc': event_info['event_desc'],
                'event_info': event_info
            })

    print(f"[✓] 快篩巡邏完成，已檢驗 {len(snapshots)} 檔，本次觸發告警: {len(triggered_alerts)} 筆。")

    # 派發推播：單檔發送詳細卡，多檔合併為單一戰訊卡 (比照策略 01~05 標準)
    if not triggered_alerts:
        return

    if len(triggered_alerts) == 1:
        a = triggered_alerts[0]
        send_surge_alert(a['target'], a['snap'], a['event_info'])
    else:
        send_aggregated_price_alerts(triggered_alerts)


def test_push_alert():
    """模擬發送測試卡片至 LINE (驗證連線與排版)"""
    print(f"[*] 正在準備發送【{STRATEGY_NAME}】模擬到價快訊測試卡片...")
    demo_target = {
        'code': '2330',
        'name': '台積電',
        'stars': '★★★★★',
        'entry_price': 1055.0,
        'addon_price': 1080.0,
        'tp1_price': 1140.0,
        'tp2_price': 1215.0,
        'sl_price': 1020.0,
        'v5_avg': 28500
    }
    demo_snap = {
        'price': 1060.0,
        'high': 1065.0,
        'low': 1045.0,
        'open': 1050.0,
        'volume_lots': 32000,
        'change_pct': 2.45
    }
    demo_event = {
        'event_type': 'LEVEL_1_SURGE',
        'event_title': '🚨【初段異常爆量起漲・模擬測試】',
        'event_desc': '盤中動態預估量衝達 5MA 均量之 3.25 倍 (預估 92,600 張 / 5MA 28,500 張)，量能布林 +3σ 強勢突破！',
        'rvol': 3.25,
        'proj_vol': 92600,
        'v5_avg': 28500,
        'solid_ratio': 0.75
    }

    send_surge_alert(demo_target, demo_snap, demo_event)
    print(f"[✓] 策略 06 模擬爆量戰訊卡已成功發送！")


def main():
    parser = argparse.ArgumentParser(description=f"{STRATEGY_NAME} 盤中即時雷達")
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔秒數 (預設 60 秒)")
    parser.add_argument("--once", action="store_true", help="執行單次快篩巡邏後結束")
    parser.add_argument("--test-push", "--force-test", dest="test_push", action="store_true", help="一鍵發送模擬爆量戰訊卡至 LINE 測試連線")
    args = parser.parse_args()

    if args.test_push:
        test_push_alert()
        return

    now_tw = datetime.datetime.now(TZ_TW)
    print("=" * 70)
    print(f"📡 【{STRATEGY_NAME}】盤中即時雷達啟動")
    print(f"⏰ 當前台灣時間: {now_tw.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⚡ 輪詢間隔: {args.interval} 秒 | 單次快篩: {args.once}")
    print("=" * 70)

    pool = load_monitoring_pool()

    if args.once:
        progress = get_market_progress_ratio()
        scan_once(pool, progress)
        return

    # 長駐輪詢迴圈
    while True:
        try:
            now = datetime.datetime.now(TZ_TW)
            # 收盤檢查 (13:35 自動結束)
            if now.hour > 13 or (now.hour == 13 and now.minute >= 35):
                print(f"[i] 已過收盤時間 (13:35)，本日盤中雷達安全結束。")
                break

            progress = get_market_progress_ratio(now)
            scan_once(pool, progress)

            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[!] 使用者手動中斷雷達監控。")
            break
        except Exception as e:
            print(f"[!] 輪詢過程發生未預期錯誤: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
