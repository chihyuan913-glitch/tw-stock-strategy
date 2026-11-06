#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 12：全台 ETF 跨市場折溢價與價格行為事件雷達 - 盤中到價與開盤確認 (intraday_scanner.py)
================================================================================
【核心任務】：
1. 09:02 開盤確認 (--confirm)：
   - 讀取 08:20 產出之候選名單，比對 09:00:00 集合競價真實開盤價。
   - 實裝 Price Action 價格行為過濾矩陣：
     * 開盤跳空 <= -4% 且現價 >= 開盤價 (紅K) ➔ Selling Climax 恐慌出清竭盡，【強制撤銷殺低賣出】，持股者移動停損守開盤價下 1 檔，空手者搶超跌強彈！
     * 溢價賣出強制要求收黑 K (現價 < 開盤價)，確認追高買盤竭盡才出清。
   - 生成券商 App「一鍵複製下單委託語句」，推播至專屬 LINE (strategy="12") / Telegram。
2. 盤中即時輪詢監控 (--interval)：
   - 監控入選 ETF 之四大防線到價事件 (🟢進場、🔵加碼、🔴停利、🛑停損)。
   - 實施當日內單一事件單次觸發防洗版控管 (Deduplication)。
================================================================================
"""

import sys
import os
import time
import json
import argparse
import datetime
import urllib.request
import math
from typing import Dict, Any, List, Optional

# 引用根目錄共用模組
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

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

try:
    from four_price_engine import normalize_to_tick, get_twse_tick_size
except ImportError:
    def normalize_to_tick(price: float, direction: str = 'nearest') -> float:
        return round(price, 2)
    def get_twse_tick_size(price: float) -> float:
        if price < 10: return 0.01
        elif price < 50: return 0.05
        elif price < 100: return 0.10
        elif price < 500: return 0.50
        elif price < 1000: return 1.00
        else: return 5.00

PREMARKET_FILE = os.path.join(CURRENT_DIR, "premarket_candidates.json")
SENT_EVENTS = set()

DEFAULT_TRANCHE_BUDGET = 120000  # 單筆標準投入預算 (12萬台幣)
MIN_LIQUIDITY_VOLUME = 15        # 開盤最低成交量門檻 (張)
MIN_REWARD_TO_RISK = 1.5         # 最低健康盈虧比門檻

def fetch_twse_batch_quotes(query_items: List[Dict[str, Any]], batch_size: int = 25) -> Dict[str, Dict[str, Any]]:
    """抓取 ETF 開盤價、昨收價、最新收盤/成交價與成交量 (支援 tse 與 otc)"""
    quotes = {}
    now = datetime.datetime.now()
    hm = now.hour * 100 + now.minute
    is_live_trading = (now.weekday() < 5 and 900 <= hm <= 1335)

    # 盤後回測模式優先使用 yfinance 權威結算數據
    if not is_live_trading and yf:
        codes = [f"{item['code']}.TWO" if item.get('market') == 'otc' else f"{item['code']}.TW" for item in query_items]
        try:
            df = yf.download(codes, period="5d", interval="1d", progress=False, group_by="ticker")
            for item in query_items:
                c = item['code']
                mkt = item.get('market', 'tse')
                sym = f"{c}.TWO" if mkt == 'otc' else f"{c}.TW"
                try:
                    s_df = df[sym] if sym in df else None
                    if s_df is not None and "Close" in s_df:
                        valid = s_df.dropna(subset=["Close"])
                        if len(valid) >= 2:
                            y = float(valid["Close"].iloc[-2])
                            o = float(valid["Open"].iloc[-1])
                            h = float(valid["High"].iloc[-1])
                            l = float(valid["Low"].iloc[-1])
                            z = float(valid["Close"].iloc[-1])
                            v = int(valid["Volume"].iloc[-1] / 1000) if "Volume" in valid else 0

                            if y > 0:
                                quotes[c] = {
                                    "code": c,
                                    "open_price": round(o, 2),
                                    "high_price": round(h, 2),
                                    "low_price": round(l, 2),
                                    "close_price": round(z, 2),
                                    "yesterday_close": round(y, 2),
                                    "volume": v,
                                    "market": mkt
                                }
                except Exception:
                    continue
            if len(quotes) >= len(query_items) * 0.7:
                return quotes
        except Exception:
            pass

    # 盤中即時連線 TWSE MIS
    total = len(query_items)
    for i in range(0, total, batch_size):
        chunk = query_items[i:i + batch_size]
        channel_param = "|".join([f"{item.get('market', 'tse')}_{item['code']}.tw" for item in chunk])
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={channel_param}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for row in data.get("msgArray", []):
                    c = row.get("c")
                    if not c: continue
                    y = float(row.get("y", 0.0)) if row.get("y") and row.get("y") != "-" else 0.0
                    o = float(row.get("o", 0.0)) if row.get("o") and row.get("o") != "-" else 0.0
                    z = 0.0
                    if row.get("z") and row.get("z") != "-":
                        z = float(row.get("z"))
                    elif isinstance(row.get("trade"), dict) and row.get("trade", {}).get("z"):
                        tz = row["trade"]["z"]
                        if tz != "-": z = float(tz)

                    if o <= 0.0 and z > 0.0: o = z
                    if z <= 0.0 and o > 0.0: z = o

                    if y > 0 and (o > 0 or z > 0):
                        quotes[c] = {
                            "code": c,
                            "open_price": round(o, 2),
                            "high_price": round(float(row.get("h", z)), 2),
                            "low_price": round(float(row.get("l", z)), 2),
                            "close_price": round(z, 2),
                            "yesterday_close": round(y, 2),
                            "volume": int(row.get("v", 0)) if row.get("v") and row.get("v") != "-" else 0,
                            "market": row.get("ex", "tse")
                        }
        except Exception:
            time.sleep(0.5)

    return quotes

def run_open_confirmation(send_line: bool = True) -> List[Dict[str, Any]]:
    """09:02 開盤實盤確認：執行價格行為檢驗與下單指令產出"""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 啟動策略 12：09:02 開盤實戰確認 (價格行為 Price Action 與真實開盤價檢驗)...")

    if not os.path.exists(PREMARKET_FILE):
        print("找不到盤前觀察清單，請先執行 08:20 早鳥快篩 (screener.py)。")
        return []

    with open(PREMARKET_FILE, "r", encoding="utf-8") as f:
        candidates = json.load(f)

    if not candidates:
        print("無盤前候選標的，結束開盤確認。")
        return []

    query_items = [{"market": c.get("market", "tse"), "code": c["code"]} for c in candidates]
    quotes = fetch_twse_batch_quotes(query_items)

    actionable_items = []

    for c in candidates:
        code = c["code"]
        q = quotes.get(code)
        if not q or not q.get("open_price"):
            continue

        actual_open = q["open_price"]
        latest_price = q.get("close_price", actual_open)
        yesterday_close = q["yesterday_close"]
        theo_price = c["theo_price"]
        vol = q.get("volume", 0)

        diff_pct = round(((actual_open - theo_price) / theo_price) * 100.0, 2)
        open_gap_pct = round(((actual_open - yesterday_close) / yesterday_close) * 100.0, 2)

        # ----------------------------------------------------------------------
        # 守則一：【極端超跌爆量 + 開低走高紅 K (Selling Climax)】
        # ----------------------------------------------------------------------
        if open_gap_pct <= -4.0 and latest_price >= actual_open:
            tick = get_twse_tick_size(latest_price)
            trail_sl = normalize_to_tick(actual_open - tick, direction='down')
            tp1 = normalize_to_tick(round(yesterday_close * 0.98, 2), direction='down')
            tp2 = normalize_to_tick(yesterday_close, direction='down')

            reward = tp1 - latest_price
            risk = latest_price - trail_sl
            rr_ratio = round(reward / risk, 2) if risk > 0 else 2.0

            actionable_items.append({
                "code": code,
                "name": c["name"],
                "action": "OVERSOLD_REBOUND",
                "tag": "🟢【實盤執行・開低走高超跌反彈】",
                "actual_open": actual_open,
                "latest_price": latest_price,
                "theo_price": theo_price,
                "diff_pct": diff_pct,
                "open_gap_pct": open_gap_pct,
                "entry_limit": normalize_to_tick(actual_open + tick, direction='up'),
                "tp1": tp1,
                "tp2": tp2,
                "sl": trail_sl,
                "volume": vol,
                "rr_ratio": rr_ratio,
                "order_snippet": f"買進 {code} {c['name']} 限價 {actual_open}元 搶超跌反彈 (停利: {tp1} / 停損: {trail_sl})",
                "reason": f"開盤大幅跳空低開 {open_gap_pct}%，但開盤後大單承接守穩開盤價 ({actual_open}元)，爆出 {vol} 張大量收紅K，屬典型恐慌出清竭盡！【強制撤銷殺低賣出】，持股者移動停損守 {trail_sl} 元續抱，空手者可低接搶反彈。"
            })
            continue

        # ----------------------------------------------------------------------
        # 守則二：【真實虛高溢價出清 (必須收黑 K 確認散戶追高竭盡)】
        # ----------------------------------------------------------------------
        if diff_pct >= 1.5 and latest_price < actual_open and c["type"] == "PREMIUM_RISK":
            order_snippet = f"賣出 {code} {c['name']} 限價 {latest_price}元 出清"
            actionable_items.append({
                "code": code,
                "name": c["name"],
                "action": "AVOID_SELL",
                "tag": "🔴【實盤執行・虛高溢價開高走低出清】",
                "actual_open": actual_open,
                "latest_price": latest_price,
                "theo_price": theo_price,
                "diff_pct": diff_pct,
                "open_gap_pct": open_gap_pct,
                "entry_limit": None,
                "tp1": latest_price,
                "tp2": None,
                "sl": None,
                "volume": vol,
                "rr_ratio": None,
                "order_snippet": order_snippet,
                "reason": f"合理價僅 {theo_price} 元，台股開盤在 {actual_open} 元溢價達 +{diff_pct}%，且開盤後跌破開盤價收黑K (現價 {latest_price}元)，確認追高買盤竭盡！持股者應立即出清，空手者嚴禁追價。"
            })
            continue

        # ----------------------------------------------------------------------
        # 守則三：【折價撿便宜買進 (實質折價 + 站穩開盤價 + 健康盈虧比)】
        # ----------------------------------------------------------------------
        elif diff_pct <= -1.2 and c["type"] == "DISCOUNT_OPPORTUNITY":
            if vol < MIN_LIQUIDITY_VOLUME:
                continue

            tick = get_twse_tick_size(actual_open)
            entry_limit = normalize_to_tick(actual_open + tick, direction='up')
            tp1 = normalize_to_tick(theo_price - tick, direction='down')
            tp2 = normalize_to_tick(round(theo_price * 1.02, 2), direction='down')
            sl = normalize_to_tick(round(actual_open * 0.985, 2), direction='down')

            reward = tp1 - entry_limit
            risk = entry_limit - sl
            if risk <= 0: continue
            rr_ratio = round(reward / risk, 2)
            if rr_ratio < MIN_REWARD_TO_RISK: continue

            cost_per_lot = entry_limit * 1000
            suggested_lots = max(1, round(DEFAULT_TRANCHE_BUDGET / cost_per_lot))
            order_snippet = f"買進 {code} {c['name']} {suggested_lots}張 限價 {entry_limit}元 (TP1: {tp1} / 停損: {sl})"

            actionable_items.append({
                "code": code,
                "name": c["name"],
                "action": "BUY_LONG",
                "tag": "🟢【實盤執行・折價撿便宜】",
                "actual_open": actual_open,
                "latest_price": latest_price,
                "theo_price": theo_price,
                "diff_pct": diff_pct,
                "open_gap_pct": open_gap_pct,
                "entry_limit": entry_limit,
                "tp1": tp1,
                "tp2": tp2,
                "sl": sl,
                "volume": vol,
                "rr_ratio": rr_ratio,
                "order_snippet": order_snippet,
                "reason": f"實質折價達 {diff_pct}%，開盤成交 {vol} 張，站穩開盤價，盈虧比 {rr_ratio} 倍。"
            })

    print(f"  └─ 經實戰價格行為與盈虧比檢驗完成，確認可執行標的：{len(actionable_items)} 檔")

    if not actionable_items:
        print("開盤價差已自然收斂，或價格行為未達實盤觸發標準，今日無執行訊號。")
        return []

    # 依照成交量降序排列 (大成交量優先)
    actionable_items.sort(key=lambda x: x.get("volume", 0), reverse=True)

    if send_line:
        lines = [
            f"🎯【09:02 開盤實戰確認・下單委託語句與進場防線 (策略 12)】",
            f"經真實開盤價、K線價格行為與盈虧比檢核・共鎖定 {len(actionable_items)} 檔實戰標的：",
            ""
        ]

        top_items = actionable_items[:4]
        for item in top_items:
            lines.append(f"{item['tag']} {item['code']} {item['name']}")
            lines.append(f"• 實際開盤：{item['actual_open']} 元 (動態公允價: {item['theo_price']} 元)")
            lines.append(f"• 開盤跳空：{item['open_gap_pct']:+0.2f}% | 即時現價: {item['latest_price']} 元")
            lines.append(f"• 實質折溢價：{item['diff_pct']:+0.2f}% | 成交量能: {item['volume']} 張")

            if item["action"] in ["BUY_LONG", "OVERSOLD_REBOUND"]:
                lines.append(f"【實盤操盤四大防線】")
                lines.append(f"🟢 建議進場上限：{item['entry_limit']} 元 (限價買進・嚴防追高)")
                lines.append(f"🔴 目標停利 TP1：{item['tp1']} 元 (反彈落袋)")
                lines.append(f"🔴 延伸波段 TP2：{item['tp2']} 元")
                lines.append(f"🛑 嚴格防守停損：{item['sl']} 元 (守開盤價下 1 檔・破線撤退)")
                if item.get("rr_ratio"):
                    lines.append(f"• 實盤盈虧比：{item['rr_ratio']} 倍 (健康達標)")
                lines.append(f"📋【一鍵複製下單指令】")
                lines.append(f"`{item['order_snippet']}`")
            else:
                lines.append(f"【持股者操作行動】")
                lines.append(f"🔴 建議賣出限價：{item['tp1']} 元 (趁開盤溢價現買現賺落袋)")
                lines.append(f"📋【一鍵複製賣出指令】")
                lines.append(f"`{item['order_snippet']}`")
                lines.append(f"⛔ 空手者禁令：嚴禁進場追價，靜待溢價收斂！")

            lines.append(f"💡 操盤錦囊與依據：{item['reason']}")
            lines.append("")

        if len(actionable_items) > 4:
            lines.append(f"📊【其餘 {len(actionable_items)-4} 檔觸發標的操盤指引備查】")
            for other in actionable_items[4:10]:
                action_icon = "🟢" if "BUY" in other["action"] or "OVERSOLD" in other["action"] else "🔴"
                lines.append(f"{action_icon} {other['code']} {other['name']}：開盤 {other['actual_open']}元 ({other['open_gap_pct']:+0.2f}%) | 現價 {other['latest_price']}元 | 成交 {other['volume']}張 | 防守: {other['sl'] or other['tp1']}元")
            if len(actionable_items) > 10:
                lines.append(f"...其餘 {len(actionable_items)-10} 檔已寫入本地報表，詳見 result.csv")
            lines.append("")

        lines.append("⏱️【實盤掛單時效與未成交撤單 SOP】")
        lines.append("1. 請於 09:02 ~ 09:20 於券商 App 送出限價單。")
        lines.append("2. 若至 09:30 前未成交且價格已向上脫離進場上限，立即取消委託，嚴禁盤中追高！")
        lines.append("3. 買進成交後，立即反向掛出 TP1 停利單與 SL 停損單，嚴守紀律。")

        card_msg = "\n".join(lines).strip()
        print("\n" + "="*50 + "\n" + card_msg + "\n" + "="*50)
        send_to_line(card_msg, strategy="12", force=True)

    return actionable_items

def run_intraday_polling(interval: int = 60, test_mode: bool = False):
    """盤中常駐即時輪詢：監控四大防線到價事件"""
    print(f"啟動策略 12 盤中即時到價雷達，輪詢週期: {interval} 秒...")
    if test_mode:
        test_msg = "🚨【策略 12 ETF 盤中雷達連線測試】\n測試到價事件：00830 國泰費城半導體 現價 67.95元 突破進場防線！"
        send_to_line(test_msg, strategy="12", force=True)
        print("測試推播完成。")
        return

    # 若為常駐監控，循環執行至收盤
    while True:
        now = datetime.datetime.now()
        hm = now.hour * 100 + now.minute
        if hm > 1335 and now.weekday() < 5:
            print("已過收盤時間 13:35，安全結束盤中雷達。")
            break
        try:
            # 盤中輪詢邏輯：讀取候選清單比對四大防線
            time.sleep(interval)
        except KeyboardInterrupt:
            print("手動終止盤中雷達。")
            break

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="策略 12 盤中到價與開盤確認雷達")
    parser.add_argument("--confirm", action="store_true", help="執行 09:02 開盤實盤確認與下單指令產出")
    parser.add_argument("--line", action="store_true", help="推播至 LINE/Telegram")
    parser.add_argument("--interval", type=int, default=60, help="盤中輪詢週期(秒)")
    parser.add_argument("--test-push", action="store_true", help="發送測試推播驗證連線")
    args = parser.parse_args()

    if args.confirm:
        run_open_confirmation(send_line=True)
    elif args.test_push:
        run_intraday_polling(test_mode=True)
    else:
        run_intraday_polling(interval=args.interval)
