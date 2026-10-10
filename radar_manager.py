#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股自選個股盤中到價雷達核心管理模組 (Intraday Custom Radar Manager)
功能：
1. 管理盤中自選個股監控名單 (watchlist.json)。
2. 支援動態指令：
   - ➕ 加入監控：鎖定四大操盤防線價位 (🟢進場、🔵加碼、🔴停利、🛑停損)。
   - ➖ 取消監控：自雷達清單移除。
   - 📋 查詢監控：檢視目前所有盯盤標的與即時防線。
3. 盤中即時到價掃描與事件觸發：
   - 🛑 停損警戒 (STOP_LOSS)：現價摜破停損防線 (最高優先級)。
   - 🔴 停利滿足 (TP1 / TP2)：現價抵達第一反壓或波段目標。
   - 🔵 突破加碼 (ADDON)：帶量突破關鍵續強加碼點。
   - 🟢 建倉進場 (ENTRY)：現價回測進入黃金建倉區間。
4. 嚴格防洗版機制 (Deduplication)：同一標的同事件類型，當日僅推播一次。
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

WATCHLIST_FILE = Path(__file__).resolve().parent / "watchlist.json"
TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

def load_watchlist() -> dict:
    """載入自選監控清單"""
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_watchlist(data: dict):
    """儲存自選監控清單"""
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_stock_to_radar(user_input: str, target_id: str = "") -> str:
    """將個股加入盤中監控雷達，鎖定四大價位"""
    from stock_analyzer import resolve_stock_input, fetch_realtime_mis, fetch_history_and_technicals

    code, stock_name_cached = resolve_stock_input(user_input)
    if not code:
        return "⚠️ 請輸入正確的台股 4 碼股票代碼或股票名稱（例如：監控 2476、監控 鉅祥、+3221）。"

    realtime = fetch_realtime_mis(code)
    stock_name = realtime['name'] or stock_name_cached or f"台股_{code}"
    tech = fetch_history_and_technicals(code)
    if not tech:
        return f"⚠️ 查無股票 【{code} {stock_name}】 之市場數據，無法掛入監控。"

    if realtime['price'] and realtime['price'] > 0:
        current_price = realtime['price']
    else:
        current_price = tech['last_close']

    ma5 = tech['ma5']
    ma10 = tech['ma10']
    ma20 = tech['ma20']
    bb_dn = tech['bb_dn']
    bias20 = ((current_price - ma20) / ma20 * 100) if ma20 else 0.0
    is_above_ma20 = (current_price >= ma20)

    # 精算四大操盤防線價位
    # A. 停損
    if is_above_ma20 and tech['low_5d'] >= ma20:
        sl_base = min(tech['low_5d'] * 0.995, current_price * 0.955)
    elif is_above_ma20:
        sl_base = min(ma20 * 0.985, current_price * 0.955)
    else:
        sl_base = min(tech['low_5d'] * 0.985, current_price * 0.95)
    stop_loss = round(sl_base, 2)

    # B. 進場
    if bias20 > 8.0:
        entry_low = round(min(ma10, current_price * 0.965), 2)
        entry_high = round(current_price * 0.985, 2)
    elif bias20 < -8.0:
        entry_low = round(max(bb_dn, current_price * 0.98), 2)
        entry_high = round(current_price, 2)
    else:
        entry_cand1 = min(ma10, current_price * 0.975) if current_price < ma10 else max(ma10, current_price * 0.975)
        entry_low = round(min(entry_cand1, current_price * 0.985), 2)
        entry_high = round(current_price, 2)

    if entry_low > entry_high:
        entry_low, entry_high = entry_high, entry_low

    # C. 加碼
    high_ref = max(tech['last_high'], current_price)
    addon_price = round(max(high_ref * 1.01, current_price * 1.025), 2)

    # D. 停利
    tp1_price = round(max(tech['high_5d'], current_price * 1.07), 2)
    tp2_price = round(max(tech['high_20d'] * 1.05, current_price * 1.15), 2)

    watchlist = load_watchlist()
    now_str = datetime.datetime.now(TZ_TW).strftime('%Y-%m-%d %H:%M:%S')

    watchlist[code] = {
        'code': code,
        'name': stock_name,
        'base_price': current_price,
        'entry_low': entry_low,
        'entry_high': entry_high,
        'addon_price': addon_price,
        'tp1_price': tp1_price,
        'tp2_price': tp2_price,
        'sl_price': stop_loss,
        'target_id': target_id or watchlist.get(code, {}).get('target_id', ''),
        'added_at': now_str,
        'alerted_today': {}
    }
    save_watchlist(watchlist)

    msg = f"""✅【已成功掛入盤中自動到價雷達！】
━━━━━━━━━━━━━━━
標的：{code} {stock_name}
基準價：{current_price:.2f} 元

🎯 鎖定四大操盤防線：
🟢 建議進場：{entry_low:.2f} ~ {entry_high:.2f} 元
🔵 動能加碼：{addon_price:.2f} 元
🔴 第一停利(TP1)：{tp1_price:.2f} 元
🔴 波段滿足(TP2)：{tp2_price:.2f} 元
🛑 嚴格停損：{stop_loss:.2f} 元
────────────────
🕒 盤中交易時段 (09:00 ~ 13:35) 每 60 秒自動雷達巡邏！
⚡ 盤中價格一旦觸及四大防線，將立即在此視窗自動發送到價推播！"""

    return msg.strip()

def remove_stock_from_radar(user_input: str) -> str:
    """自盤中監控雷達移除個股"""
    from stock_analyzer import resolve_stock_input
    code, stock_name = resolve_stock_input(user_input)
    if not code:
        return "⚠️ 請輸入要移除的 4 碼股票代碼或股名（例如：刪除 2476、-3221）。"

    watchlist = load_watchlist()
    if code in watchlist:
        name = watchlist[code].get('name', stock_name or code)
        del watchlist[code]
        save_watchlist(watchlist)
        return f"🗑️ 已成功自盤中監控雷達移除【{code} {name}】！"
    else:
        return f"ℹ️ 監控清單中查無標的 【{code} {stock_name or ''}】。"

def get_radar_summary() -> str:
    """查詢目前掛在雷達上的自選股清單"""
    watchlist = load_watchlist()
    if not watchlist:
        return """📋【盤中到價監控雷達・目前無監控標的】
────────────────
💡 如何加入標的？
在 LINE 聊天室直接輸入：
• 監控 2476
• 監控 華容
• +3221

系統即刻自動鎖定四大價位並開始即時盯盤！"""

    msg_lines = [
        "📋【盤中到價監控雷達・清單一覽】",
        "━━━━━━━━━━━━━━━",
        f"目前共監控 {len(watchlist)} 檔焦點標的："
    ]

    for idx, (code, item) in enumerate(watchlist.items(), 1):
        name = item.get('name', code)
        bp = item.get('base_price', 0)
        el = item.get('entry_low', 0)
        eh = item.get('entry_high', 0)
        ad = item.get('addon_price', 0)
        tp1 = item.get('tp1_price', 0)
        sl = item.get('sl_price', 0)
        msg_lines.append(f"\n{idx}. 【{code} {name}】基準: {bp:.2f}元")
        msg_lines.append(f"   🟢進場: {el:.2f}~{eh:.2f} | 🔵加碼: {ad:.2f}")
        msg_lines.append(f"   🔴停利: {tp1:.2f} | 🛑停損: {sl:.2f}")

    msg_lines.append("────────────────")
    msg_lines.append("🕒 盤中交易時段 (09:00 ~ 13:35) 自動輪詢到價推播")
    msg_lines.append("💡 輸入「刪除 2476」可取消單檔監控")
    return "\n".join(msg_lines)

def scan_and_generate_alerts(token: str, default_user_id: str = "") -> list:
    """
    執行單次盤中到價巡邏，偵測觸發事件並自動推送 LINE
    回傳觸發事件清單
    """
    watchlist = load_watchlist()
    if not watchlist:
        return []

    now_tw = datetime.datetime.now(TZ_TW)
    today_str = now_tw.strftime('%Y-%m-%d')
    time_str = now_tw.strftime('%H:%M:%S')

    codes = list(watchlist.keys())
    # 批次向 mis.twse 查詢現價
    channel_str = "|".join([f"tse_{c}.tw|otc_{c}.tw" for c in codes])
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={channel_str}&json=1&delay=0"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    quotes = {}
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for item in data.get('msgArray', []):
                c = item.get('c', '')
                if c in watchlist:
                    z = item.get('z', '-')
                    y = item.get('y', '-')
                    p = float(z) if (z and z != '-') else (float(y) if (y and y != '-') else None)
                    if p:
                        quotes[c] = {
                            'price': p,
                            'name': item.get('n', watchlist[c].get('name', c)),
                            'time': item.get('t', time_str)
                        }
    except Exception as e:
        print(f"[!] 批次查詢現價失敗: {e}")
        return []

    triggered_alerts = []
    watchlist_updated = False

    for code, item in watchlist.items():
        if code not in quotes:
            continue

        q = quotes[code]
        curr_p = q['price']
        name = item.get('name', q['name'])
        entry_low = item.get('entry_low', 0)
        entry_high = item.get('entry_high', 0)
        addon_p = item.get('addon_price', 0)
        tp1_p = item.get('tp1_price', 0)
        tp2_p = item.get('tp2_price', 0)
        sl_p = item.get('sl_price', 0)

        alerted = item.setdefault('alerted_today', {}).setdefault(today_str, [])
        target_dest = item.get('target_id') or default_user_id

        # 事件判定 (優先級：停損 > 停利 > 加碼 > 進場)
        event_info = None

        # 1. 停損警戒 (現價 <= 停損線)
        if sl_p > 0 and curr_p <= sl_p and 'STOP_LOSS' not in alerted:
            event_info = {
                'type': 'STOP_LOSS',
                'badge': '🛑【最高風控警報・破線停損警戒觸發】',
                'detail': f'現價 {curr_p:.2f} 元 已跌破停損防線 {sl_p:.2f} 元！多頭結構破壞，請無條件依紀律嚴格停損撤退，鎖定風險。'
            }
        # 2. 停利滿足 (TP2 或 TP1)
        elif tp2_p > 0 and curr_p >= tp2_p and 'TP2' not in alerted:
            event_info = {
                'type': 'TP2',
                'badge': '🔴【波段滿足警示・TP2 目標強勢攻抵】',
                'detail': f'現價 {curr_p:.2f} 元 已攻抵波段等幅滿足點 {tp2_p:.2f} 元！波段獲利豐碩，建議獲利全數入袋或緊縮移動防守。'
            }
        elif tp1_p > 0 and curr_p >= tp1_p and 'TP1' not in alerted:
            event_info = {
                'type': 'TP1',
                'badge': '🔴【獲利了結通知・TP1 前高反壓觸發】',
                'detail': f'現價 {curr_p:.2f} 元 已衝抵第一目標反壓區 {tp1_p:.2f} 元！前方逢解套賣壓，建議分批獲利減碼 1/2。'
            }
        # 3. 突破加碼 (現價 >= 加碼價)
        elif addon_p > 0 and curr_p >= addon_p and 'ADDON' not in alerted:
            event_info = {
                'type': 'ADDON',
                'badge': '🔵【動能追擊通知・突破加碼防線觸發】',
                'detail': f'現價 {curr_p:.2f} 元 放量突破關鍵續強點 {addon_p:.2f} 元！主升段動能確立，右側可順勢加碼 1/3。'
            }
        # 4. 建倉進場 (現價回測進入進場區間)
        elif entry_low <= curr_p <= entry_high and 'ENTRY' not in alerted:
            event_info = {
                'type': 'ENTRY',
                'badge': '🟢【黃金買點通知・拉回進場區間觸發】',
                'detail': f'現價 {curr_p:.2f} 元 已回測落入黃金建倉區間 ({entry_low:.2f} ~ {entry_high:.2f} 元)！守穩短均線，可分批佈局。'
            }

        if event_info and target_dest and token:
            card = f"""⚡【盤中到價即時雷達警報】
━━━━━━━━━━━━━━━
標的：{code} {name}
現價：{curr_p:.2f} 元
觸發：{event_info['badge']}

🎯 操盤四大防線現況：
🟢 建議進場：{entry_low:.2f} ~ {entry_high:.2f} 元
🔵 動能加碼：{addon_p:.2f} 元
🔴 第一停利：{tp1_p:.2f} 元
🛑 嚴格停損：{sl_p:.2f} 元
────────────────
💡 行動方針：
{event_info['detail']}
━━━━━━━━━━━━━━━
⏰ 觸發時間：{time_str}
（盤中智慧防洗版：同一事件當日僅推播一次）"""

            # 發送 LINE Push 訊息
            push_url = "https://api.line.me/v2/bot/message/push"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            }
            body = {
                "to": target_dest,
                "messages": [{"type": "text", "text": card}]
            }
            try:
                post_data = json.dumps(body, ensure_ascii=False).encode('utf-8')
                p_req = urllib.request.Request(push_url, data=post_data, headers=headers, method="POST")
                with urllib.request.urlopen(p_req, timeout=5) as p_resp:
                    if p_resp.status == 200:
                        print(f"[✓] 成功推送到價警訊至 [{target_dest[:8]}...]: {code} {event_info['type']}", flush=True)
                        alerted.append(event_info['type'])
                        watchlist_updated = True
                        triggered_alerts.append(f"{code} {name} ({event_info['type']})")
            except Exception as e:
                print(f"[!] 推送到價警訊失敗: {e}", flush=True)

    if watchlist_updated:
        save_watchlist(watchlist)

    return triggered_alerts

if __name__ == '__main__':
    # 測試
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'
    if cmd == 'add':
        t = sys.argv[2] if len(sys.argv) > 2 else '2476'
        print(add_stock_to_radar(t, "test_target"))
    elif cmd == 'list':
        print(get_radar_summary())
    elif cmd == 'remove':
        t = sys.argv[2] if len(sys.argv) > 2 else '2476'
        print(remove_stock_from_radar(t))
