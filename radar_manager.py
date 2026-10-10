#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股自選個股盤中到價雷達核心管理模組 V2.0 (Intraday Multi-Channel Radar Manager)
支援四大進階匯入方案：
1. 【方案一：多檔批次貼上】：一鍵貼上多檔代碼（例如 "監控 2330 2476 3221 5328 2603"），整批掛入。
2. 【方案二：自動連動每日選股戰報】：輸入 "同步選股"，自動讀取四大策略最新 result.csv 精華標的。
3. 【方案三：券商自選清單匯入】：支援貼上任意雜亂券商看盤清單文字或本地檔案 (my_stocks.txt) 一鍵擷取。
4. 【方案四：熱門題材一鍵打包】：輸入 "監控 矽光子"、"監控 機器人"、"監控 散熱" 等，整組核心概念股整包掛入。
"""

import os
import sys
import re
import json
import glob
import datetime
import urllib.request
from pathlib import Path

# Windows 命令列編碼保護
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = ROOT_DIR / "watchlist.json"
RADAR_CHANNEL_FILE = ROOT_DIR / "radar_channel.json"
TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

def get_radar_channel() -> str:
    """取得獨立雷達專屬推播目標 ID (Group ID)"""
    env_target = os.environ.get("LINE_TARGET_RADAR", "")
    if env_target:
        return env_target
    if RADAR_CHANNEL_FILE.exists():
        try:
            with open(RADAR_CHANNEL_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d.get("target_id", "")
        except Exception:
            pass
    return ""

def set_radar_channel(target_id: str, group_name: str = "") -> str:
    """設定獨立雷達專屬推播視窗"""
    d = {
        "target_id": target_id,
        "group_name": group_name or "台股盤中自動監控雷達專屬視窗",
        "updated_at": datetime.datetime.now(TZ_TW).strftime('%Y-%m-%d %H:%M:%S')
    }
    with open(RADAR_CHANNEL_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return target_id

def clear_radar_channel() -> bool:
    """解除雷達專屬視窗綁定"""
    if RADAR_CHANNEL_FILE.exists():
        try:
            os.remove(RADAR_CHANNEL_FILE)
            return True
        except Exception:
            pass
    return False

# 方案四：熱門題材核心概念股字典 (繁簡雙向支援)
THEMES = {
    "矽光子": ["3450", "6442", "3163", "3081", "3363", "6451"],
    "硅光子": ["3450", "6442", "3163", "3081", "3363", "6451"],
    "CPO": ["3450", "6442", "3163", "3081", "3363", "6451"],
    "機器人": ["2359", "6188", "4562", "8374", "6215"],
    "机器人": ["2359", "6188", "4562", "8374", "6215"],
    "散熱": ["3017", "3324", "8996", "2421", "3653"],
    "散热": ["3017", "3324", "8996", "2421", "3653"],
    "COWOS": ["3583", "3131", "6187", "2467", "6640"],
    "先進封裝": ["3583", "3131", "6187", "2467", "6640"],
    "先进封装": ["3583", "3131", "6187", "2467", "6640"],
    "AI伺服器": ["3231", "2382", "2376", "6669", "2356"],
    "AI服务器": ["3231", "2382", "2376", "6669", "2356"],
    "AI": ["3231", "2382", "2376", "6669", "2356"],
    "低軌衛星": ["3491", "6285", "3138", "3221"],
    "低轨卫星": ["3491", "6285", "3138", "3221"],
    "衛星": ["3491", "6285", "3138", "3221"],
    "卫星": ["3491", "6285", "3138", "3221"],
    "重電": ["1519", "1503", "1513", "1514"],
    "重电": ["1519", "1503", "1513", "1514"],
    "綠能": ["6806", "1519", "1503", "1513", "1514"],
    "绿能": ["6806", "1519", "1503", "1513", "1514"],
}

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

def calculate_stock_defense(code: str) -> dict:
    """為單一股票計算四大操盤防線"""
    from stock_analyzer import fetch_realtime_mis, fetch_history_and_technicals, load_stock_dict
    
    stock_dict = load_stock_dict()
    realtime = fetch_realtime_mis(code)
    stock_name = realtime['name'] or stock_dict.get(code, f"台股_{code}")
    
    tech = fetch_history_and_technicals(code)
    if not tech:
        return None

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

    # 停損
    if is_above_ma20 and tech['low_5d'] >= ma20:
        sl_base = min(tech['low_5d'] * 0.995, current_price * 0.955)
    elif is_above_ma20:
        sl_base = min(ma20 * 0.985, current_price * 0.955)
    else:
        sl_base = min(tech['low_5d'] * 0.985, current_price * 0.95)
    stop_loss = round(sl_base, 2)

    # 進場
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

    # 加碼
    high_ref = max(tech['last_high'], current_price)
    addon_price = round(max(high_ref * 1.01, current_price * 1.025), 2)

    # 停利
    tp1_price = round(max(tech['high_5d'], current_price * 1.07), 2)
    tp2_price = round(max(tech['high_20d'] * 1.05, current_price * 1.15), 2)

    return {
        'code': code,
        'name': stock_name,
        'base_price': current_price,
        'entry_low': entry_low,
        'entry_high': entry_high,
        'addon_price': addon_price,
        'tp1_price': tp1_price,
        'tp2_price': tp2_price,
        'sl_price': stop_loss
    }

def batch_add_to_radar(user_text: str, target_id: str = "") -> str:
    """
    通用進階掛入入口：
    支援：
    1. 題材概念股一鍵打包 (例如 "監控 矽光子", "監控 機器人")
    2. 多檔代碼批次貼上 (例如 "監控 2330 2476 3221 5328 2603")
    3. 單檔代碼 (例如 "監控 2476")
    """
    clean_text = user_text.upper().strip()
    
    # 檢查是否命中題材庫 (方案四)
    theme_hit = None
    for theme_name, theme_codes in THEMES.items():
        if theme_name in clean_text:
            theme_hit = (theme_name, theme_codes)
            break

    codes_to_add = []
    header_title = ""

    if theme_hit:
        t_name, t_codes = theme_hit
        codes_to_add = t_codes
        header_title = f"🏷️【已成功打包【{t_name}】概念股群組進雷達！】"
    else:
        # 從文字中擷取所有 4 碼數字 (方案一 & 方案三)
        codes_to_add = list(set(re.findall(r'\b\d{4}\b', user_text)))
        
        # 若沒找到數字，檢查是否有中文股名
        if not codes_to_add:
            from stock_analyzer import resolve_stock_input
            c, n = resolve_stock_input(user_text)
            if c:
                codes_to_add = [c]

    if not codes_to_add:
        return """⚠️ 未識別出有效的股票代碼或題材名稱！

💡 您可以這樣輸入：
• 批次掛入：監控 2330 2476 3221 5328
• 題材打包：監控 矽光子 / 監控 機器人 / 監控 散熱
• 單檔掛入：監控 2476"""

    watchlist = load_watchlist()
    now_str = datetime.datetime.now(TZ_TW).strftime('%Y-%m-%d %H:%M:%S')
    added_list = []

    for code in codes_to_add:
        res = calculate_stock_defense(code)
        if not res:
            continue

        res['target_id'] = target_id or watchlist.get(code, {}).get('target_id', '')
        res['added_at'] = now_str
        res['alerted_today'] = {}
        watchlist[code] = res
        added_list.append(res)

    if not added_list:
        return f"⚠️ 嘗試掛入 {len(codes_to_add)} 檔標的，但皆無市場交易數據，請檢查代碼。"

    save_watchlist(watchlist)

    # 若只有 1 檔，回傳詳細單檔卡片
    if len(added_list) == 1:
        it = added_list[0]
        return f"""✅【已成功掛入盤中自動到價雷達！】
━━━━━━━━━━━━━━━
標的：{it['code']} {it['name']}
基準價：{it['base_price']:.2f} 元

🎯 鎖定四大操盤防線：
🟢 建議進場：{it['entry_low']:.2f} ~ {it['entry_high']:.2f} 元
🔵 動能加碼：{it['addon_price']:.2f} 元
🔴 第一停利(TP1)：{it['tp1_price']:.2f} 元
🔴 波段滿足(TP2)：{it['tp2_price']:.2f} 元
🛑 嚴格停損：{it['sl_price']:.2f} 元
────────────────
🕒 盤中交易時段 (09:00 ~ 13:35) 每 60 秒自動雷達巡邏！
⚡ 盤中價格一旦觸及四大防線，將立即在此視窗自動發送到價推播！"""

    # 若為多檔或題材打包，回傳結構化整批清單
    if not header_title:
        header_title = f"✅【已成功整批掛入 {len(added_list)} 檔標的進雷達！】"

    msg_lines = [
        header_title,
        "━━━━━━━━━━━━━━━"
    ]
    for idx, it in enumerate(added_list, 1):
        msg_lines.append(f"{idx}. 【{it['code']} {it['name']}】現價 {it['base_price']:.1f}")
        msg_lines.append(f"   🟢進: {it['entry_low']:.1f}~{it['entry_high']:.1f} | 🔵加: {it['addon_price']:.1f}")
        msg_lines.append(f"   🔴利: {it['tp1_price']:.1f} | 🛑損: {it['sl_price']:.1f}")

    msg_lines.append("────────────────")
    msg_lines.append(f"🕒 盤中交易時段 (09:00 ~ 13:35) 每 60 秒自動盯盤！")
    msg_lines.append("⚡ 任何一檔觸及進場、加碼、停利或停損，將即時在此視窗推播！")
    return "\n".join(msg_lines)

def sync_screener_to_radar(target_id: str = "") -> str:
    """
    方案二：全自動連動「每日量化選股戰報」
    讀取四大策略目錄下的最新 result.csv，將精選黑馬股自動同步至雷達清單
    """
    import pandas as pd
    strategies_pattern = str(ROOT_DIR / "strategies" / "*" / "result.csv")
    csv_files = glob.glob(strategies_pattern)

    if not csv_files:
        return "ℹ️ 目前策略目錄下尚未產出 result.csv 選股報表，請確認日報篩選是否已執行。"

    watchlist = load_watchlist()
    now_str = datetime.datetime.now(TZ_TW).strftime('%Y-%m-%d %H:%M:%S')
    synced_stocks = []

    strat_names = {
        "01_bollinger_reversal": "布林超跌",
        "02_institutional_momentum": "法人起漲",
        "03_short_momentum": "弱勢破線",
        "04_cb_pricing_ambush": "可轉債伏擊"
    }

    for path in csv_files:
        folder_name = Path(path).parent.name
        tag = strat_names.get(folder_name, folder_name)
        try:
            df = pd.read_csv(path, encoding='utf-8-sig')
            if df.empty:
                continue

            # 取各策略前 3 檔精選標的
            top_df = df.head(3)
            for _, row in top_df.iterrows():
                # 代碼欄位辨識
                code = str(row.get('證券代號', row.get('代號', ''))).strip()
                if not code or not code.isdigit() or len(code) != 4:
                    continue
                name = str(row.get('證券名稱', row.get('名稱', code))).strip()
                close_p = float(row.get('收盤價', row.get('現價', 0)))
                
                # 計算或提取價位
                res = calculate_stock_defense(code)
                if not res:
                    continue

                res['name'] = f"{res['name']}[{tag}]"
                res['target_id'] = target_id or watchlist.get(code, {}).get('target_id', '')
                res['added_at'] = now_str
                res['alerted_today'] = {}
                watchlist[code] = res
                synced_stocks.append(res)
        except Exception as e:
            print(f"[!] 讀取 {path} 失敗: {e}")

    if not synced_stocks:
        return "⚠️ 查無可同步之精選標的。"

    save_watchlist(watchlist)

    msg_lines = [
        f"🤖【已全自動同步 {len(synced_stocks)} 檔量化選股標的至雷達！】",
        "━━━━━━━━━━━━━━━",
        "已自各策略最新選股日報中提取精選標的："
    ]
    for idx, it in enumerate(synced_stocks, 1):
        msg_lines.append(f"{idx}. 【{it['code']} {it['name']}】現價 {it['base_price']:.1f}")
        msg_lines.append(f"   🟢進: {it['entry_low']:.1f}~{it['entry_high']:.1f} | 🛑損: {it['sl_price']:.1f}")

    msg_lines.append("────────────────")
    msg_lines.append("🕒 盤中開盤即自動啟動到價雷達巡邏，到價即推播！")
    return "\n".join(msg_lines)

def remove_stock_from_radar(user_input: str) -> str:
    """自盤中監控雷達移除個股 (支援代碼或中文名稱)"""
    from stock_analyzer import resolve_stock_input
    
    # 支援一次移除多檔
    codes = re.findall(r'\b\d{4}\b', user_input)
    if not codes:
        c, n = resolve_stock_input(user_input)
        if c:
            codes = [c]

    if not codes:
        return "⚠️ 請輸入要移除的 4 碼股票代碼或股名（例如：刪除 2476、-3221 5328）。"

    watchlist = load_watchlist()
    removed = []
    for c in codes:
        if c in watchlist:
            name = watchlist[c].get('name', c)
            del watchlist[c]
            removed.append(f"{c} {name}")

    if removed:
        save_watchlist(watchlist)
        return f"🗑️ 已成功自盤中監控雷達移除：\n" + "\n".join([f"• {x}" for x in removed])
    else:
        return "ℹ️ 監控清單中查無指定的標的。"

def get_radar_summary() -> str:
    """查詢目前掛在雷達上的自選股清單"""
    watchlist = load_watchlist()
    if not watchlist:
        return """📋【盤中到價監控雷達・目前無監控標的】
────────────────
💡 如何批次加入標的？
在 LINE 聊天室直接輸入：
• 批次掛入：監控 2330 2476 3221 5328 2603
• 自動連動：同步選股
• 題材打包：監控 矽光子 / 監控 機器人 / 監控 散熱

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
        msg_lines.append(f"\n{idx}. 【{code} {name}】基準: {bp:.1f}元")
        msg_lines.append(f"   🟢進: {el:.1f}~{eh:.1f} | 🔵加: {ad:.1f}")
        msg_lines.append(f"   🔴利: {tp1:.1f} | 🛑損: {sl:.1f}")

    msg_lines.append("────────────────")
    msg_lines.append("🕒 盤中交易時段 (09:00 ~ 13:35) 自動輪詢到價推播")
    msg_lines.append("💡 輸入「刪除 2476」可取消單檔，輸入「清空監控」可全部移除")
    return "\n".join(msg_lines)

def clear_all_radar() -> str:
    """清空所有監控標的"""
    save_watchlist({})
    return "🗑️ 已清空盤中監控雷達的所有標的！"

def get_themes_summary() -> str:
    """取得支援的一鍵題材概念股清單"""
    lines = [
        "🏷️【支援之一鍵打包題材概念股清單】",
        "━━━━━━━━━━━━━━━"
    ]
    seen = set()
    for t_name, t_codes in THEMES.items():
        key = tuple(t_codes)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"•「監控 {t_name}」➜ 包含代碼: {', '.join(t_codes)}")
    lines.append("────────────────")
    lines.append("💡 在 LINE 輸入「監控 矽光子」即可將整組族群一次掛入雷達！")
    return "\n".join(lines)

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
    # 批次向 mis.twse 查詢現價 (每 50 檔一包)
    quotes = {}
    chunk_size = 50
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i:i + chunk_size]
        channel_str = "|".join([f"tse_{c}.tw|otc_{c}.tw" for c in chunk])
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={channel_str}&json=1&delay=0"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
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
        radar_channel_id = get_radar_channel()
        target_dest = radar_channel_id or item.get('target_id') or default_user_id

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
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'
    if cmd == 'add':
        t = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else '2476 3221'
        print(batch_add_to_radar(t, "test"))
    elif cmd == 'sync':
        print(sync_screener_to_radar("test"))
    elif cmd == 'themes':
        print(get_themes_summary())
    elif cmd == 'list':
        print(get_radar_summary())
