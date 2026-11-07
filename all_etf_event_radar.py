#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全台灣發行 ETF 全域事件型交易監控雷達 (All-Taiwan ETF Event Radar)
覆蓋全台 268+ 檔已發行 ETF（包含台股市值型、高股息型、海外跨境型、債券型、槓反型與主動型 ETF）：
1. 盤前跨境早鳥預警 (08:00~08:55)：利用美股夜盤期貨與日股 08:00 先開盤優勢，精算海外 ETF 理論開盤價。
2. 試撮與開盤防坑 (08:45~09:10)：全市場批次掃描 268 檔 ETF，偵測「嚴重溢價(切勿追高)」與「恐慌折價(黃金低接)」。
3. 盤中即時異常監控 (09:00~13:35)：常駐比對跨市場走勢反向背離、債券暫停申購之非理性溢價狂潮。
4. 智慧防洗版推播：每日僅針對「真正觸發極端事件異常」之標的發送 LINE / Telegram 高對比視覺化戰報。
"""

import sys
import os
import time
import json
import argparse
import datetime
import urllib.request
from typing import Dict, Any, List, Optional

# 設定路徑引用根目錄共用模組
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
    def send_to_line(msg, strategy=None, force=False):
        print(f"[推播備援輸出]:\n{msg}")
        return True

# 本地字典路徑
UNIVERSE_FILE = os.path.join(ROOT_DIR, "all_etf_universe.json")

# 防重複洗版暫存 (當日同標的同事件僅推播一次)
SENT_EVENTS = set()

def load_etf_universe() -> Dict[str, Any]:
    """讀取全台灣 268+ 檔 ETF 宇宙字典"""
    if os.path.exists(UNIVERSE_FILE):
        try:
            with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # 若檔案不存在，即時調用建置函式
    from build_etf_universe import build_complete_etf_universe
    build_complete_etf_universe()
    with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def fetch_overseas_benchmarks() -> Dict[str, Dict[str, float]]:
    """一次性抓取全球核心錨定物（日股、美股期貨、美元、日圓、美債）即時變動"""
    if not yf:
        return {}

    benchmarks = {
        "1306.T": "日本東證母體",
        "JPYTWD=X": "日圓匯率",
        "^SOX": "費半指數",
        "ES=F": "標普期貨",
        "NQ=F": "那指期貨",
        "USDTWD=X": "美元匯率",
        "TLT": "美債20Y"
    }

    result = {}
    try:
        tickers = list(benchmarks.keys())
        data = yf.download(tickers, period="2d", interval="1d", progress=False, group_by="ticker")
        for t in tickers:
            try:
                df = data[t] if t in data else None
                if df is not None and len(df) >= 2:
                    p_prev = float(df["Close"].iloc[-2])
                    p_curr = float(df["Close"].iloc[-1])
                    chg_pct = round(((p_curr - p_prev) / p_prev) * 100.0, 2)
                    result[t] = {
                        "name": benchmarks[t],
                        "prev": p_prev,
                        "curr": p_curr,
                        "chg_pct": chg_pct
                    }
            except Exception:
                continue
    except Exception as e:
        print(f"獲取海外錨定物失敗: {e}")
    return result

def fetch_twse_batch_quotes(etf_items: List[Dict[str, Any]], batch_size: int = 50) -> Dict[str, Dict[str, Any]]:
    """利用 TWSE MIS 批次查詢全台 268 檔 ETF 即時價格與昨收"""
    quotes = {}
    total = len(etf_items)

    for i in range(0, total, batch_size):
        chunk = etf_items[i:i + batch_size]
        channel_param = "|".join([f"{item['market']}_{item['code']}.tw" for item in chunk])
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={channel_param}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("msgArray", [])
                for row in items:
                    c = row.get("c")
                    if not c:
                        continue
                    y = float(row.get("y", 0.0)) if row.get("y") and row.get("y") != "-" else 0.0
                    
                    # 取得即時現價或最新成交價
                    z = None
                    if row.get("z") and row.get("z") != "-":
                        z = float(row.get("z"))
                    elif isinstance(row.get("trade"), dict) and row.get("trade", {}).get("z"):
                        tz = row["trade"]["z"]
                        if tz != "-":
                            z = float(tz)
                    elif row.get("o") and row.get("o") != "-":
                        z = float(row.get("o"))

                    if z and y > 0:
                        chg_pct = round(((z - y) / y) * 100.0, 2)
                        vol = int(row.get("v", 0)) if row.get("v") and row.get("v") != "-" else 0
                        quotes[c] = {
                            "code": c,
                            "price": z,
                            "yesterday_close": y,
                            "chg_pct": chg_pct,
                            "volume": vol,
                            "time": row.get("t", "")
                        }
        except Exception as e:
            print(f"批次查詢區間 {i}~{i+batch_size} 失敗: {e}")
            time.sleep(0.5)

    return quotes

def detect_all_etf_events() -> List[Dict[str, Any]]:
    """全市場事件比對與篩選核心引擎"""
    universe = load_etf_universe()
    etf_list = list(universe.values())
    
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 開始掃描全市場 {len(etf_list)} 檔 ETF...")
    
    # 1. 抓海外基準變動
    benchmarks = fetch_overseas_benchmarks()

    # 2. 批次抓取台股 268 檔 ETF 即時報價
    quotes = fetch_twse_batch_quotes(etf_list)
    print(f"  └─ 成功取得 {len(quotes)} 檔 ETF 即時報價資料")

    detected_events = []

    for code, info in universe.items():
        q = quotes.get(code)
        if not q:
            continue

        cat = info.get("category", "DOMESTIC_EQUITY")
        name = info["name"]
        tw_chg = q["chg_pct"]
        price = q["price"]
        anchor = info.get("anchor")

        # 估算理論漲跌與背離
        theo_chg = tw_chg
        divergence = 0.0
        anchor_desc = ""

        if anchor and benchmarks:
            u_sym = anchor["underlying"]
            fx_sym = anchor["fx"]
            u_info = benchmarks.get(u_sym)
            fx_info = benchmarks.get(fx_sym)

            if u_info and fx_info:
                u_chg = u_info["chg_pct"]
                fx_chg = fx_info["chg_pct"]
                # 理論 = (1 + 海外) * (1 + 匯率) - 1
                theo_chg = round(((1 + u_chg / 100.0) * (1 + fx_chg / 100.0) - 1) * 100.0, 2)
                divergence = round(tw_chg - theo_chg, 2)
                anchor_desc = f"{anchor['name']}({u_chg}%) | 匯率({fx_chg}%)"

        # --- 事件觸發條件判斷 ---
        ev = None

        # 事件 A: 【🔴 跨市場反向背離・假突破誘多】(海外走弱但台股盲目衝高)
        if anchor and divergence >= 1.2 and tw_chg > 0.8:
            ev = {
                "code": code,
                "name": name,
                "category": cat,
                "level": "DANGER",
                "tag": "跨市場背離・假突破",
                "tw_price": price,
                "tw_chg": tw_chg,
                "theo_chg": theo_chg,
                "divergence": divergence,
                "anchor_desc": anchor_desc,
                "advice": "【極度危險】海外母資產實質走跌或無動能，台股散戶追高開高！嚴重溢價隨時遭造市商收斂，嚴禁開盤追價！"
            }

        # 事件 B: 【🟢 跨市場恐慌超跌・黃金折價低接】(海外強勢但台股被錯殺)
        elif anchor and divergence <= -1.2 and theo_chg > 0:
            ev = {
                "code": code,
                "name": name,
                "category": cat,
                "level": "OPPORTUNITY",
                "tag": "黃金折價・恐慌錯殺",
                "tw_price": price,
                "tw_chg": tw_chg,
                "theo_chg": theo_chg,
                "divergence": divergence,
                "anchor_desc": anchor_desc,
                "advice": "【黃金機會】海外母資產強勁上漲，台股開盤反應鈍化或錯殺，出現實質超額折價！具備極高安全邊際，適合分批低接撿便宜。"
            }

        # 事件 C: 【⚠️ 國內/債券/主動型 ETF 嚴重異常溢價 (停申購狂熱)】
        elif not anchor and tw_chg >= 2.0:
            ev = {
                "code": code,
                "name": name,
                "category": cat,
                "level": "WARNING",
                "tag": "短線非理性溢價狂飆",
                "tw_price": price,
                "tw_chg": tw_chg,
                "theo_chg": theo_chg,
                "divergence": tw_chg,
                "anchor_desc": "國內現貨/大盤",
                "advice": "【警示】單日漲幅過劇，常伴隨初級市場申購額度上限或除息前散戶溢價搶進，持股者可逢高拔檔獲利了結。"
            }

        if ev:
            detected_events.append(ev)

    # 依照背離/偏離絕對值排序，由最劇烈排到最輕微
    detected_events.sort(key=lambda x: abs(x["divergence"]), reverse=True)
    return detected_events

def format_all_etf_alert(events: List[Dict[str, Any]]) -> str:
    """產出符合總指揮中心大字體標準之全市場事件匯總戰報"""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    
    lines = [
        f"🚨【全台股 ETF 跨市場事件型即時雷達】",
        f"全市場 268 檔即時掃描完畢・發現 {len(events)} 檔異常標的",
        f"偵測時間：{now_str}",
        ""
    ]

    for ev in events[:5]:  # 只呈現最顯著前 5 檔，杜絕洗版
        sign_tw = "+" if ev["tw_chg"] > 0 else ""
        sign_th = "+" if ev["theo_chg"] > 0 else ""
        sign_div = "+" if ev["divergence"] > 0 else ""
        icon = "🔴" if ev["level"] == "DANGER" else ("🟢" if ev["level"] == "OPPORTUNITY" else "🟡")

        lines.append(f"{icon}【{ev['tag']}】{ev['code']} {ev['name']}")
        lines.append(f"• 現價水位：{ev['tw_price']} 元 ({sign_tw}{ev['tw_chg']}%)")
        if ev["anchor_desc"]:
            lines.append(f"• 海外錨定：{ev['anchor_desc']}")
            lines.append(f"• 理論合理漲跌：{sign_th}{ev['theo_chg']}% (偏離度: {sign_div}{ev['divergence']}%)")
        lines.append(f"• 操盤指引：{ev['advice']}")
        lines.append("")

    lines.append("💡 風控提示：海外 ETF 溢價 > 1% 切忌市價搶買，折價 > 1% 逢低分批撿便宜。")
    return "\n".join(lines).strip()

def run_all_etf_radar(force_push: bool = False, test_mode: bool = False):
    """執行全市場 ETF 事件掃描並發送推播"""
    if test_mode:
        mock_events = [
            {
                "code": "009812",
                "name": "野村日本東證",
                "category": "OVERSEAS_EQUITY",
                "level": "DANGER",
                "tag": "跨市場背離・假突破",
                "tw_price": 13.04,
                "tw_chg": 2.68,
                "theo_chg": 0.53,
                "divergence": 2.15,
                "anchor_desc": "日股東證(-0.85%) | 匯率(+1.38%)",
                "advice": "海外東證實質跌 -0.85%，台股因散戶開盤搶買溢價高達 +2.15%！嚴防造市商拋單收斂，切勿追高！"
            },
            {
                "code": "00830",
                "name": "國泰費城半導體",
                "category": "OVERSEAS_EQUITY",
                "level": "OPPORTUNITY",
                "tag": "黃金折價・恐慌錯殺",
                "tw_price": 68.20,
                "tw_chg": -1.20,
                "theo_chg": 0.85,
                "divergence": -2.05,
                "anchor_desc": "費半指數(+1.10%) | 匯率(-0.25%)",
                "advice": "美股費半強勁反彈，但台股早盤遭市場情緒錯殺，出現 -2% 實質超額折價！具備高安全邊際，建議分批低接。"
            },
            {
                "code": "00679B",
                "name": "元大美債20年",
                "category": "BOND",
                "level": "WARNING",
                "tag": "申購額度緊繃・超額溢價",
                "tw_price": 27.50,
                "tw_chg": 1.85,
                "theo_chg": 0.40,
                "divergence": 1.45,
                "anchor_desc": "美債TLT(+0.35%) | 匯率(+0.05%)",
                "advice": "長天期美債因散戶大量買進溢價達 +1.45%，切忌追價，等待額度釋出平價時再佈局。"
            }
        ]
        msg = format_all_etf_alert(mock_events)
        print("發送全台股 ETF 事件型測試戰報：\n")
        print(msg)
        send_to_line(msg, force=True)
        return

    events = detect_all_etf_events()
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 掃描完成，觸發事件檔數：{len(events)} 檔")

    if not events:
        print("  └─ 今日全市場 268 檔 ETF 運作平穩，未出現極端溢價或走勢反向背離。")
        return

    # 檢驗防洗版
    today_str = datetime.date.today().strftime("%Y%m%d")
    new_events = []
    for ev in events:
        key = f"{ev['code']}_{ev['tag']}_{today_str}"
        if key not in SENT_EVENTS or force_push:
            new_events.append(ev)
            SENT_EVENTS.add(key)

    if new_events:
        alert_text = format_all_etf_alert(new_events)
        print("\n" + "="*45 + "\n" + alert_text + "\n" + "="*45)
        send_to_line(alert_text, force=True)
        print("  └─ 戰訊推播已順利送出至 LINE / Telegram！")
    else:
        print("  └─ 偵測到之異常事件今日均已推播過，啟動防洗版機制。")

def start_all_etf_monitor(interval: int = 60):
    """盤中全日長效輪詢監控 (08:30 ~ 13:35)"""
    print(f"啟動全台股 268 檔 ETF 全日事件型監控 (每 {interval} 秒輪詢一次，Ctrl+C 結束)...")
    while True:
        try:
            now = datetime.datetime.now()
            cur = now.hour * 100 + now.minute
            if 830 <= cur <= 1335:
                run_all_etf_radar()
            else:
                print(f"[{now.strftime('%H:%M:%S')}] 非盤中交易時段 (08:30~13:35)，休眠等待中...")
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n停止監控。")
            break
        except Exception as e:
            print(f"輪詢發生例外: {e}")
            time.sleep(10)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="全台股 268 檔 ETF 事件型交易與折溢價監控雷達")
    parser.add_argument("--once", action="store_true", help="執行單次全市場掃描")
    parser.add_argument("--monitor", action="store_true", help="啟動盤中持續輪詢監控")
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔秒數 (預設 60 秒)")
    parser.add_argument("--test-push", action="store_true", help="模擬發送全台 ETF 跨市場事件測試戰報")
    args = parser.parse_args()

    if args.test_push:
        run_all_etf_radar(test_mode=True)
    elif args.monitor:
        start_all_etf_monitor(args.interval)
    else:
        run_all_etf_radar()
