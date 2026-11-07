#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨市場跨境 ETF 事件性監控與到價/折溢價推播雷達 (Cross-Market Event Radar)
專為台股跨境海外 ETF（如 009812 野村日本東證、00830 國泰費半 等）設計：
1. 盤前領先預警 (08:00~08:55)：日本 08:00 早開盤，比對海外現貨與匯率，精算「今日合理開盤漲跌幅」。
2. 試撮與開盤防坑 (08:45~09:05)：比對台股撮合價與理論價，偵測「嚴重溢價(假突破追高陷阱)」或「嚴重折價(黃金低接)」。
3. 盤中即時背離 (09:00~13:30)：即時監控台股 ETF 與母國資產走勢背離事件，觸發 LINE 即時通報。
"""

import sys
import os
import time
import json
import argparse
import datetime
import urllib.request
from typing import Dict, Any, Optional

# 設定路徑引用根目錄模組
ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from line_sender import send_to_line
except ImportError:
    def send_to_line(msg, strategy=None):
        print(f"[LINE 模擬推播]:\n{msg}")
        return True

# 監控標的配置表
TARGET_CONFIG = {
    "009812": {
        "name": "野村日本東證",
        "market": "JP",
        "underlying_ticker": "1306.T",       # 日本母基金 NEXT FUNDS TOPIX
        "fx_ticker": "JPYTWD=X",             # 日圓/新台幣匯率
        "index_name": "日本東證指數 (TOPIX)",
        "premium_threshold": 0.8,            # 溢價警戒線 (%)
        "discount_threshold": -0.8,          # 折價進場線 (%)
        "divergence_threshold": 1.2          # 走勢背離警報差額 (%)
    },
    "00830": {
        "name": "國泰費城半導體",
        "market": "US",
        "underlying_ticker": "^SOX",         # 費城半導體指數
        "fx_ticker": "USDTWD=X",             # 美元/新台幣匯率
        "index_name": "費城半導體指數 (SOX)",
        "premium_threshold": 1.0,
        "discount_threshold": -1.0,
        "divergence_threshold": 1.5
    }
}

# 防重複洗版暫存
SENT_EVENTS = set()

def fetch_twse_realtime(code: str) -> Optional[Dict[str, Any]]:
    """從證交所 MIS 取得台股 ETF 即時現價、昨收與成交量"""
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{code}.tw"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("msgArray", [])
            if not items:
                return None
            item = items[0]
            yesterday_close = float(item.get("y", 0.0))
            
            # 當前成交價 (若尚未撮合則取最後試撮價或買賣中位數)
            trade_z = item.get("trade", {}).get("z") if isinstance(item.get("trade"), dict) else None
            price = None
            if trade_z and trade_z != "-":
                price = float(trade_z)
            elif item.get("z") and item.get("z") != "-":
                price = float(item.get("z"))
            elif item.get("o") and item.get("o") != "-":
                price = float(item.get("o"))
            elif item.get("b") and item.get("b") != "-":
                # 取買一賣一中位
                b1 = float(item.get("b").split("_")[0]) if "_" in item.get("b") else 0
                a1 = float(item.get("a").split("_")[0]) if "_" in item.get("a") else 0
                if b1 > 0 and a1 > 0:
                    price = round((b1 + a1) / 2.0, 2)

            if not price or yesterday_close <= 0:
                return None

            chg_pct = round(((price - yesterday_close) / yesterday_close) * 100.0, 2)
            return {
                "code": code,
                "price": price,
                "yesterday_close": yesterday_close,
                "chg_pct": chg_pct,
                "time": item.get("t", "")
            }
    except Exception as e:
        # Fallback to yfinance if TWSE MIS is unavailable
        if yf:
            try:
                t = yf.Ticker(f"{code}.TW")
                hist = t.history(period="2d")
                if len(hist) >= 2:
                    y_close = float(hist["Close"].iloc[-2])
                    price = float(hist["Close"].iloc[-1])
                    chg_pct = round(((price - y_close) / y_close) * 100.0, 2)
                    return {
                        "code": code,
                        "price": price,
                        "yesterday_close": y_close,
                        "chg_pct": chg_pct,
                        "time": "YF"
                    }
            except Exception:
                pass
    return None

def fetch_overseas_data(target_code: str) -> Optional[Dict[str, Any]]:
    """抓取海外底層標的與即時匯率變化"""
    cfg = TARGET_CONFIG.get(target_code)
    if not cfg or not yf:
        return None

    try:
        # 1. 抓底層母基金/指數
        u_ticker = yf.Ticker(cfg["underlying_ticker"])
        u_hist = u_ticker.history(period="2d")
        if len(u_hist) < 2:
            return None
        u_prev = float(u_hist["Close"].iloc[-2])
        u_curr = float(u_hist["Close"].iloc[-1])
        u_chg_pct = ((u_curr - u_prev) / u_prev) * 100.0

        # 2. 抓即時匯率
        fx_ticker = yf.Ticker(cfg["fx_ticker"])
        fx_hist = fx_ticker.history(period="2d")
        if len(fx_hist) < 2:
            fx_chg_pct = 0.0
            fx_curr = 0.0
        else:
            fx_prev = float(fx_hist["Close"].iloc[-2])
            fx_curr = float(fx_hist["Close"].iloc[-1])
            fx_chg_pct = ((fx_curr - fx_prev) / fx_prev) * 100.0

        # 3. 計算以新台幣計價之理論合理漲跌幅
        # 理論倍數 = (1 + 海外漲跌率) * (1 + 匯率漲跌率) - 1
        theoretical_chg_pct = round(((1 + u_chg_pct / 100.0) * (1 + fx_chg_pct / 100.0) - 1) * 100.0, 2)

        return {
            "underlying_name": cfg["index_name"],
            "underlying_chg_pct": round(u_chg_pct, 2),
            "underlying_price": round(u_curr, 2),
            "fx_name": cfg["fx_ticker"],
            "fx_price": round(fx_curr, 4),
            "fx_chg_pct": round(fx_chg_pct, 2),
            "theoretical_chg_pct": theoretical_chg_pct
        }
    except Exception as e:
        print(f"抓取海外數據失敗 ({target_code}): {e}")
        return None

def analyze_and_detect_event(code: str) -> Optional[Dict[str, Any]]:
    """比對兩地市場，產出事件訊號與推播內文"""
    cfg = TARGET_CONFIG.get(code)
    if not cfg:
        return None

    tw_data = fetch_twse_realtime(code)
    overseas_data = fetch_overseas_data(code)

    if not tw_data or not overseas_data:
        return None

    tw_chg = tw_data["chg_pct"]
    theo_chg = overseas_data["theoretical_chg_pct"]
    underlying_chg = overseas_data["underlying_chg_pct"]
    fx_chg = overseas_data["fx_chg_pct"]

    # 折溢價/背離差額估算: 台股實際漲幅 - 理論應有漲幅
    divergence = round(tw_chg - theo_chg, 2)

    event_type = None
    level = "INFO"
    title = ""
    advice = ""

    # 1. 嚴重溢價 / 假突破誘多 (台股衝太高、海外跟不上)
    if divergence >= cfg["premium_threshold"] and tw_chg > 0:
        if underlying_chg <= 0:
            event_type = "PREMIUM_DIVERGENCE_TRAP"
            level = "DANGER"
            title = f"🔴【{cfg['name']} 開盤嚴重溢價・小心假突破誘多】"
            advice = "【操盤警訊】海外母資產實質走弱，台股散戶追價溢價過高！隨時面臨造市商收斂賣壓，切勿盲目追高，持股者可逢高拔檔。"
        else:
            event_type = "PREMIUM_WARNING"
            level = "WARNING"
            title = f"🟡【{cfg['name']} 估值偏高・出現超額溢價】"
            advice = "【操盤警訊】台股漲幅大幅超越海外合理漲幅，溢價正在擴大，建議等待盤中折溢價收斂後再進場。"

    # 2. 嚴重折價 / 恐慌超跌 (台股錯殺、海外強勢)
    elif divergence <= cfg["discount_threshold"] and theo_chg > 0:
        event_type = "DISCOUNT_OPPORTUNITY"
        level = "OPPORTUNITY"
        title = f"🟢【{cfg['name']} 甜甜折價浮現・超跌低接機會】"
        advice = "【操盤錦囊】海外母資產持續走強，但台股開盤反應落後，出現實質折價！具備較高安全邊際，適合分批低接撿便宜。"

    return {
        "code": code,
        "name": cfg["name"],
        "event_type": event_type,
        "level": level,
        "title": title,
        "advice": advice,
        "divergence": divergence,
        "tw_price": tw_data["price"],
        "tw_chg": tw_chg,
        "underlying_name": overseas_data["underlying_name"],
        "underlying_chg": underlying_chg,
        "fx_chg": fx_chg,
        "theo_chg": theo_chg,
        "time": tw_data.get("time", "")
    }

def format_event_card(ev: Dict[str, Any]) -> str:
    """產出符合總指揮中心規範的大字體純文字戰訊卡"""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sign_tw = "+" if ev["tw_chg"] > 0 else ""
    sign_u = "+" if ev["underlying_chg"] > 0 else ""
    sign_fx = "+" if ev["fx_chg"] > 0 else ""
    sign_th = "+" if ev["theo_chg"] > 0 else ""
    sign_div = "+" if ev["divergence"] > 0 else ""

    text = f"""⚡【跨市場事件交易即時雷達】
{ev['title']}

標的資訊：{ev['code']} {ev['name']}
現價水位：{ev['tw_price']} 元 ({sign_tw}{ev['tw_chg']}%)
偵測時間：{now_str}

【跨市場兩地數據精算】
• 海外母體：{ev['underlying_name']} ({sign_u}{ev['underlying_chg']}%)
• 即時匯率：日圓/新台幣變化 ({sign_fx}{ev['fx_chg']}%)
• 理論合理漲跌：{sign_th}{ev['theo_chg']}%
• 估算折溢價差：{sign_div}{ev['divergence']}%

💡 操盤決策錦囊：
{ev['advice']}
"""
    return text.strip()

def run_radar_once(test_mode: bool = False, force_push: bool = False):
    """執行一次全標的掃描"""
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 啟動跨市場海外 ETF 雷達掃描...")
    
    for code in TARGET_CONFIG.keys():
        ev = analyze_and_detect_event(code)
        if not ev:
            continue

        print(f"[{code} {ev['name']}] 台股漲跌:{ev['tw_chg']}% | 海外漲跌:{ev['underlying_chg']}% | 理論:{ev['theo_chg']}% | 價差:{ev['divergence']}%")

        if ev["event_type"] or test_mode:
            event_key = f"{code}_{ev['event_type']}_{datetime.date.today()}"
            if event_key in SENT_EVENTS and not force_push and not test_mode:
                print(f"  └─ 事件 {ev['event_type']} 今日已推播，跳過防洗版")
                continue

            # 發送推播
            card_text = format_event_card(ev)
            print("\n" + "="*40 + "\n" + card_text + "\n" + "="*40)
            
            # 調用 line_sender 發送至預設 LINE 頻道
            success = send_to_line(card_text, force=True)
            if success:
                SENT_EVENTS.add(event_key)
                print(f"  └─ LINE 推播成功已送達！")
            else:
                print(f"  └─ LINE 推播未送達 (請檢核 .env 設定)")

def start_monitor_loop(interval: int = 60):
    """盤中常駐輪詢模式"""
    print(f"進入跨市場雷達長效監控模式 (每 {interval} 秒輪詢一次，Ctrl+C 結束)...")
    while True:
        try:
            now = datetime.datetime.now()
            # 僅在 08:30 ~ 13:35 之間運行
            current_time = now.hour * 100 + now.minute
            if 830 <= current_time <= 1335:
                run_radar_once()
            else:
                print(f"[{now.strftime('%H:%M:%S')}] 非盤前試撮或開盤時段 (08:30~13:35)，休眠等待中...")
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n停止監控。")
            break
        except Exception as e:
            print(f"輪詢發生例外: {e}")
            time.sleep(10)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="跨市場海外 ETF 事件性監控與折溢價推播雷達")
    parser.add_argument("--once", action="store_true", help="執行單次即時掃描")
    parser.add_argument("--monitor", action="store_true", help="啟動盤中持續輪詢監控")
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔秒數 (預設 60 秒)")
    parser.add_argument("--test-push", action="store_true", help="模擬發送一則 009812 虛胖開高警報測試 LINE 連線")
    args = parser.parse_args()

    if args.test_push:
        mock_ev = {
            "code": "009812",
            "name": "野村日本東證",
            "event_type": "PREMIUM_DIVERGENCE_TRAP",
            "level": "DANGER",
            "title": "🔴【野村日本東證 開盤嚴重溢價・小心假突破誘多】",
            "advice": "【操盤警訊】海外母資產（東證指數）實質跌 -0.8%，但台股因散戶搶買溢價高達 +1.8%！隨時面臨造市商拋單收斂賣壓，切勿盲目追高！",
            "divergence": 2.15,
            "tw_price": 13.04,
            "tw_chg": 2.68,
            "underlying_name": "日本東證指數 (TOPIX)",
            "underlying_chg": -0.85,
            "fx_chg": 1.38,
            "theo_chg": 0.53,
            "time": "09:02:15"
        }
        card_text = format_event_card(mock_ev)
        print("發送模擬測試卡片至 LINE：")
        print(card_text)
        send_to_line(card_text, force=True)
    elif args.monitor:
        start_monitor_loop(args.interval)
    else:
        run_radar_once()
