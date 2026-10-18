#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE 推播發送模組 (進階版：支援多策略專屬視窗/獨立群組定向投遞)
支援：
1. LINE Messaging API (官方標準 Push Message，支援個人對話與群組 Group ID)
2. 獨立視窗分流 (策略一、策略二、策略三定向推送至各自群組，防錯防混淆)
3. 自動 Fallback 機制 (未設定各別群組時自動發送至全域 LINE_USER_ID)
4. LINE Notify (向後相容)
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.parse
from pathlib import Path

# Windows 命令列編碼保護
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

# 讀取同目錄下的 .env 檔案
ENV_PATH = Path(__file__).resolve().parent / ".env"

def load_env():
    """手動解析 .env，無需額外套件依賴"""
    env_vars = {}
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key.strip()] = val.strip().strip("'\"")
    return env_vars

def resolve_target_id(strategy: str = None, target_id: str = None, env: dict = None) -> tuple:
    """
    依據策略代號解析發送目標 (User ID 或 Group ID):
    支援 strategy="01", "02", "03" 等。
    回傳: (target_id, target_label)
    """
    if target_id:
        return target_id, "自訂指定目標"

    if env is None:
        env = load_env()

    if strategy:
        strat_key = str(strategy).strip()
        if strat_key.isdigit() and len(strat_key) == 1:
            strat_num = f"0{strat_key}"
        elif len(strat_key) >= 2 and strat_key[:2].isdigit():
            strat_num = strat_key[:2]
        else:
            strat_num = strat_key

        candidates = [
            f"LINE_TARGET_STRATEGY_{strat_num}",
            f"LINE_GROUP_ID_{strat_num}",
            f"LINE_TARGET_{strat_num}",
            f"LINE_TARGET_STRATEGY_{strat_key}",
        ]
        for c in candidates:
            val = os.environ.get(c) or env.get(c)
            if val and val.strip():
                return val.strip(), f"策略 {strat_num} 專屬視窗/群組 ({c})"

    # 若未指定或無特定策略群組，Fallback 至全域預設 User ID
    default_id = os.environ.get("LINE_USER_ID") or env.get("LINE_USER_ID")
    return default_id, "預設個人聊天視窗 (LINE_USER_ID)"

def resolve_tokens(strategy: str = None, env: dict = None) -> list:
    """取得優先嘗試的 Token 清單 (專屬 Token -> 主 Token -> 備援 Token)"""
    tokens = []
    if env is None:
        env = load_env()

    if strategy:
        strat_key = str(strategy).strip()
        strat_num = f"0{strat_key}" if strat_key.isdigit() and len(strat_key) == 1 else strat_key[:2]
        st = os.environ.get(f"LINE_CHANNEL_ACCESS_TOKEN_{strat_num}") or env.get(f"LINE_CHANNEL_ACCESS_TOKEN_{strat_num}")
        if st and st.strip():
            tokens.append(st.strip())

    main_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or env.get("LINE_CHANNEL_ACCESS_TOKEN")
    if main_token and main_token.strip() and main_token.strip() not in tokens:
        tokens.append(main_token.strip())

    backup_candidates = [
        "LINE_CHANNEL_ACCESS_TOKEN_BACKUP",
        "LINE_CHANNEL_ACCESS_TOKEN_02",
        "LINE_CHANNEL_ACCESS_TOKEN_01",
        "LINE_CHANNEL_ACCESS_TOKEN_03",
        "LINE_CHANNEL_ACCESS_TOKEN_04",
        "LINE_CHANNEL_ACCESS_TOKEN_05",
        "LINE_CHANNEL_ACCESS_TOKEN_06",
        "LINE_CHANNEL_ACCESS_TOKEN_07",
        "LINE_CHANNEL_ACCESS_TOKEN_08",
    ]
    for bc in backup_candidates:
        bt = os.environ.get(bc) or env.get(bc)
        if bt and bt.strip() and bt.strip() not in tokens:
            tokens.append(bt.strip())

    return tokens

def send_telegram(token: str, chat_id: str, message: str) -> bool:
    """使用 Telegram Bot API 發送即時戰報 (100% 永久免費・無每月則數上限)"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                print(f"[✓] Telegram 備援推播發送成功！目標頻道: {chat_id}")
                return True
    except Exception as e:
        print(f"[!] Telegram 發送失敗: {e}")
    return False

def send_line_messaging_api(tokens: list, to_id: str, message: str, target_label: str = "") -> bool:
    """使用 LINE Messaging API 發送訊息，內建 429 額度超額自動切換備援 Token 機制"""
    if isinstance(tokens, str):
        tokens = [tokens]

    url = "https://api.line.me/v2/bot/message/push"
    if isinstance(message, dict) and "type" in message:
        msg_obj = message
    else:
        msg_obj = {
            "type": "text",
            "text": str(message)
        }

    payload = {
        "to": to_id,
        "messages": [msg_obj]
    }
    data = json.dumps(payload).encode("utf-8")

    for idx, token in enumerate(tokens):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    masked_id = f"{to_id[:5]}...{to_id[-4:]}" if len(to_id) > 10 else to_id
                    label_info = f" [{target_label}]" if target_label else ""
                    token_info = f" (Token #{idx+1})" if len(tokens) > 1 else ""
                    print(f"[✓] LINE Messaging API 推播發送成功！目標: {masked_id}{label_info}{token_info}")
                    return True
                else:
                    print(f"[!] LINE 推播失敗，狀態碼: {resp.status}")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='ignore')
            if e.code == 429:
                print(f"[⚠️] LINE Token #{idx+1} 本月免費額度已達上限 (429: reached monthly limit)")
                if idx + 1 < len(tokens):
                    print(f"[⚡] 正在無縫切換至備援 Token #{idx+2} 重試發送...")
                    continue
                else:
                    print(f"[X] 所有可用的 LINE Token 本月額度皆已耗盡！")
                    return False
            else:
                print(f"[X] LINE Messaging API HTTP 錯誤 {e.code}: {err_body}")
                return False
        except Exception as e:
            print(f"[X] 發送 LINE 訊息異常: {e}")
            return False

    return False

def send_line_notify(token: str, message: str) -> bool:
    """使用 LINE Notify 發送 (相容舊版或第三方轉接服務)"""
    url = "https://notify-api.line.me/api/notify"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    payload = urllib.parse.urlencode({"message": message}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print("[✓] LINE Notify 發送成功！")
                return True
    except Exception as e:
        print(f"[X] LINE Notify 發送失敗: {e}")
        return False
    return False

def send_to_line(message: str, strategy: str = None, target_id: str = None) -> bool:
    """
    通用即時推播發送入口：
    1. 支援多策略獨立 LINE 視窗/群組定向投遞 (strategy="01", "02", "03", "04")
    2. 支援多 Token 自動容錯輪替 (主 Token 額度超額自動切換至備援 Token)
    3. 支援 Telegram Bot 雙軌平行備援 (永不受限)
    """
    env = load_env()
    tokens = resolve_tokens(strategy=strategy, env=env)
    to_id, target_label = resolve_target_id(strategy=strategy, target_id=target_id, env=env)
    notify_token = os.environ.get("LINE_NOTIFY_TOKEN") or env.get("LINE_NOTIFY_TOKEN")

    # Telegram 雙軌備援
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        tg_text = message.get("altText") if isinstance(message, dict) else str(message)
        send_telegram(tg_token, tg_chat, tg_text)

    if tokens and to_id:
        success = send_line_messaging_api(tokens, to_id, message, target_label=target_label)
        default_user_id = os.environ.get("LINE_USER_ID") or env.get("LINE_USER_ID")
        if not success and default_user_id and to_id != default_user_id:
            print(f"[⚡] 專屬群組推播未成功 (機器人可能尚未加入該群組)，自動觸發 Fallback 轉送至個人聊天室...")
            return send_line_messaging_api(tokens, default_user_id, message, target_label="個人聊天室 (Fallback 備援)")
        return success
    elif notify_token:
        return send_line_notify(notify_token, message)
    else:
        print("\n" + "!" * 65)
        print("【尚未設定 LINE 推播憑證或目標 ID】")
        print(f"請在 {ENV_PATH} 中設定：")
        print("LINE_CHANNEL_ACCESS_TOKEN=你的機器人存取憑證")
        print("LINE_USER_ID=你的LINE個人ID (U開頭字串，作為預設)")
        print("LINE_TARGET_STRATEGY_01=策略一專屬群組ID (C開頭字串)")
        print("LINE_TARGET_STRATEGY_02=策略二專屬群組ID (C開頭字串)")
        print("LINE_TARGET_STRATEGY_03=策略三專屬群組ID (C開頭字串)")
        print("!" * 65 + "\n")
        return False

# =====================================================================
# 旗艦視覺化 UI 建構器 (大字體、高對比、零引線、操盤四大防線一目了然)
# =====================================================================

def resolve_strategy_meta(strategy_id: str) -> dict:
    """解析策略元數據（名稱、四大防線專屬用詞、主色系）"""
    import datetime
    sid = str(strategy_id).strip() if strategy_id else "01"
    if sid.isdigit() and len(sid) == 1:
        sid = f"0{sid}"
    elif len(sid) >= 2 and sid[:2].isdigit():
        sid = sid[:2]

    if sid == "01":
        return {
            "id": "01",
            "name": "布林超跌反轉",
            "entry_label": "🟢 進場價位",
            "addon_label": "🔵 加碼價位",
            "tp_label": "🔴 停利目標",
            "sl_label": "🛑 停損防守",
            "entry_desc": "轉折支撐區",
            "addon_desc": "突破日高續攻",
            "tp_desc": "TP1中軌 / TP2上軌",
            "sl_desc": "跌破下軌-5%",
            "color_theme": "#16A34A"
        }
    elif sid == "02":
        return {
            "id": "02",
            "name": "法人起漲動能",
            "entry_label": "🟢 進場價位",
            "addon_label": "🔵 加碼價位",
            "tp_label": "🔴 停利目標",
            "sl_label": "🛑 停損防守",
            "entry_desc": "起漲安全區",
            "addon_desc": "突破日高續攻",
            "tp_desc": "TP1 +12% / TP2 +20%",
            "sl_desc": "跌破月線-2%",
            "color_theme": "#2563EB"
        }
    elif sid == "03":
        return {
            "id": "03",
            "name": "弱勢破線做空",
            "entry_label": "🟢 空單進場",
            "addon_label": "🔵 加空價位",
            "tp_label": "🔴 停利回補",
            "sl_label": "🛑 停損回補",
            "entry_desc": "起跌進場點",
            "addon_desc": "破低加空追擊",
            "tp_desc": "TP1 -10% / TP2 -16%",
            "sl_desc": "站上月線反壓",
            "color_theme": "#DC2626"
        }
    elif sid == "04":
        return {
            "id": "04",
            "name": "可轉債定價伏擊",
            "entry_label": "🟢 伏擊進場",
            "addon_label": "🔵 帶量加碼",
            "tp_label": "🔴 停利目標",
            "sl_label": "🛑 停損防守",
            "entry_desc": "伏擊建倉區",
            "addon_desc": "放量突破加碼",
            "tp_desc": "TP1轉換價 / TP2頸線",
            "sl_desc": "跌破防守線",
            "color_theme": "#7C3AED"
        }
    elif sid == "05":
        return {
            "id": "05",
            "name": "個股期貨波段多方",
            "entry_label": "🟢 多單進場",
            "addon_label": "🔵 突破加碼",
            "tp_label": "🔴 停利目標",
            "sl_label": "🛑 停損防守",
            "entry_desc": "期貨起漲建倉",
            "addon_desc": "突破日高續強",
            "tp_desc": "TP1 +10% / TP2 +16%",
            "sl_desc": "破20MA或-3.5%",
            "color_theme": "#0D9488"
        }
    elif sid == "06":
        return {
            "id": "06",
            "name": "盤中爆量起漲動能",
            "entry_label": "🟢 爆量進場",
            "addon_label": "🔵 動能加碼",
            "tp_label": "🔴 停利目標",
            "sl_label": "🛑 嚴格停損",
            "entry_desc": "爆量起漲買點",
            "addon_desc": "突破日高加碼",
            "tp_desc": "TP1 +8% / TP2 +15%",
            "sl_desc": "跌破爆量低點或-3.5%",
            "color_theme": "#EA580C"
        }
    elif sid == "07":
        return {
            "id": "07",
            "name": "高融資斷頭與浮額清洗",
            "entry_label": "🟢 伏擊/放空進場",
            "addon_label": "🔵 突破/破底加碼",
            "tp_label": "🔴 停利目標",
            "sl_label": "🛑 嚴格防守",
            "entry_desc": "雙向狀態建倉價",
            "addon_desc": "動能確認加碼",
            "tp_desc": "TP1初段 / TP2滿足",
            "sl_desc": "嚴格停損風控線",
            "color_theme": "#B91C1C"
        }
    elif sid in ("08", "08_group_window_dressing"):
        return {
            "id": "08",
            "name": "集團作帳與母子聯動",
            "entry_label": "🟢 作帳伏擊進場",
            "addon_label": "🔵 共振突破加碼",
            "tp_label": "🔴 作帳停利目標",
            "sl_label": "🛑 破線嚴格防守",
            "entry_desc": "母子共振建倉價",
            "addon_desc": "領頭羊突破續強",
            "tp_desc": "TP1作帳目標 / TP2波段",
            "sl_desc": "跌破起漲防守線",
            "color_theme": "#D97706"
        }
    elif sid == "RADAR":
        return {
            "id": "RADAR",
            "name": "盤中即時監控雷達",
            "entry_label": "🟢 建議進場",
            "addon_label": "🔵 動能加碼",
            "tp_label": "🔴 停利目標",
            "sl_label": "🛑 嚴格停損",
            "entry_desc": "買進上限價",
            "addon_desc": "突破續強點",
            "tp_desc": "TP1反壓 / TP2滿足",
            "sl_desc": "關鍵防守線",
            "color_theme": "#0D9488"
        }
    else:
        return {
            "id": sid,
            "name": f"策略 {sid}",
            "entry_label": "🟢 進場價位",
            "addon_label": "🔵 加碼價位",
            "tp_label": "🔴 停利目標",
            "sl_label": "🛑 停損防守",
            "entry_desc": "關鍵建倉價",
            "addon_desc": "突破續強價",
            "tp_desc": "目標停利價",
            "sl_desc": "風控停損價",
            "color_theme": "#0F172A"
        }

def format_price_val(val) -> str:
    """安全格式化股價數值"""
    if val is None or val == "" or val == "-":
        return "--"
    try:
        f = float(val)
        return f"{f:,.2f}"
    except (ValueError, TypeError):
        return str(val)

def build_price_alert_card(
    target: dict,
    rt: dict,
    event_type: str,
    event_title: str,
    event_desc: str,
    strategy_id: str = "01"
) -> dict:
    """建構單檔盤中到價高對比 Flex Message 卡片 (大字體、零引線、四大價位一目了然)"""
    import datetime
    meta = resolve_strategy_meta(strategy_id)
    code = str(target.get('code') or target.get('證券代號') or target.get('代號') or '')
    name = str(target.get('name') or target.get('證券名稱') or target.get('名稱') or '')
    stars = str(target.get('stars') or target.get('星級推薦') or target.get('星級') or '★★★★☆')
    price = rt.get('price') or target.get('收盤價') or 0.0
    high_p = rt.get('high') or price
    low_p = rt.get('low') or price
    vol = rt.get('volume') or target.get('當日成交量(張)') or target.get('日成交量(張)') or 0
    time_str = rt.get('time') or datetime.datetime.now().strftime('%H:%M:%S')

    entry_val = target.get('entry_price') or target.get('進場價位') or target.get('空單進場')
    addon_val = target.get('addon_price') or target.get('加碼價位') or target.get('加空價位')
    tp1_val = target.get('tp1_price') or target.get('停利TP1')
    tp2_val = target.get('tp2_price') or target.get('停利TP2')
    sl_val = target.get('sl_price') or target.get('停損價位') or target.get('停損回補')
    og_risk = target.get('隔日沖風控') or target.get('og_risk') or ''

    fut_contract = target.get('期貨契約') or target.get('contract_sym') or target.get('contract') or ''
    fut_month = target.get('期貨月份') or target.get('month') or ''
    fut_price = target.get('期貨收盤價') or target.get('futures_close') or ''
    basis_desc = target.get('價差(Basis)') or target.get('basis_desc') or target.get('basis_tag') or ''
    margin_1lot = target.get('1口保證金(約)') or target.get('margin_1lot')

    title_name = f"{code} {name} ({fut_contract})" if fut_contract and fut_contract not in ('--', '--F', 'None') else f"{code} {name}"

    entry_str = format_price_val(entry_val)
    addon_str = format_price_val(addon_val)
    tp_str = f"TP1 {format_price_val(tp1_val)} / TP2 {format_price_val(tp2_val)}" if tp2_val else format_price_val(tp1_val)
    sl_str = format_price_val(sl_val)

    # 依事件類型調整徽章配色
    if event_type == "STOP_LOSS":
        pill_bg = "#7F1D1D"
        pill_text = "#FECACA"
    elif event_type in ["TP1", "TP2"]:
        pill_bg = "#78350F"
        pill_text = "#FDE68A"
    elif event_type == "ADDON":
        pill_bg = "#1E3A8A"
        pill_text = "#BFDBFE"
    elif event_type == "ENTRY":
        pill_bg = "#14532D"
        pill_text = "#BBF7D0"
    else:
        pill_bg = "#1E293B"
        pill_text = "#94A3B8"

    bubble = {
        "type": "bubble",
        "size": "giga",
        "header": {
            "type": "box",
            "layout": "vertical",
            "paddingTop": "16px",
            "paddingBottom": "12px",
            "paddingStart": "20px",
            "paddingEnd": "20px",
            "backgroundColor": "#0F172A",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": "🚨 盤中到價雷達", "size": "xs", "color": "#38BDF8", "weight": "bold", "flex": 0},
                        {"type": "text", "text": f"策略 {meta['id']} {meta['name']}", "size": "xs", "color": "#94A3B8", "align": "end"}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": title_name, "size": "xxl", "weight": "bold", "color": "#F8FAFC", "flex": 0},
                        {"type": "text", "text": stars, "size": "xs", "color": "#FBBF24", "align": "end"}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "sm",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": "現價", "size": "sm", "color": "#94A3B8", "flex": 0},
                        {"type": "text", "text": f" {format_price_val(price)}", "size": "3xl", "weight": "bold", "color": "#F8FAFC", "flex": 0},
                        {"type": "text", "text": f" (高 {format_price_val(high_p)} / 低 {format_price_val(low_p)})", "size": "xs", "color": "#94A3B8", "margin": "sm"}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "backgroundColor": pill_bg,
                    "cornerRadius": "6px",
                    "paddingAll": "6px",
                    "contents": [
                        {"type": "text", "text": f"🔥 觸發事件：【{event_title}】", "size": "xs", "color": pill_text, "weight": "bold", "align": "center"}
                    ]
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "18px",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "🎯 實戰操盤四大防線", "size": "sm", "weight": "bold", "color": "#334155"},
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 1,
                            "backgroundColor": "#F0FDF4",
                            "borderColor": "#86EFAC",
                            "borderWidth": "1px",
                            "cornerRadius": "8px",
                            "paddingAll": "10px",
                            "contents": [
                                {"type": "text", "text": meta['entry_label'], "size": "xs", "color": "#166534", "weight": "bold"},
                                {"type": "text", "text": entry_str, "size": "xl", "weight": "bold", "color": "#15803D", "margin": "xs"},
                                {"type": "text", "text": meta['entry_desc'], "size": "xxs", "color": "#16A34A", "margin": "xs"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 1,
                            "backgroundColor": "#EFF6FF",
                            "borderColor": "#93C5FD",
                            "borderWidth": "1px",
                            "cornerRadius": "8px",
                            "paddingAll": "10px",
                            "contents": [
                                {"type": "text", "text": meta['addon_label'], "size": "xs", "color": "#1E40AF", "weight": "bold"},
                                {"type": "text", "text": addon_str, "size": "xl", "weight": "bold", "color": "#1D4ED8", "margin": "xs"},
                                {"type": "text", "text": meta['addon_desc'], "size": "xxs", "color": "#2563EB", "margin": "xs"}
                            ]
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 1,
                            "backgroundColor": "#FEF2F2",
                            "borderColor": "#FCA5A5",
                            "borderWidth": "1px",
                            "cornerRadius": "8px",
                            "paddingAll": "10px",
                            "contents": [
                                {"type": "text", "text": meta['tp_label'], "size": "xs", "color": "#991B1B", "weight": "bold"},
                                {"type": "text", "text": tp_str, "size": "md", "weight": "bold", "color": "#DC2626", "margin": "xs"},
                                {"type": "text", "text": meta['tp_desc'], "size": "xxs", "color": "#DC2626", "margin": "xs"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 1,
                            "backgroundColor": "#FFF1F2",
                            "borderColor": "#FDA4AF",
                            "borderWidth": "1px",
                            "cornerRadius": "8px",
                            "paddingAll": "10px",
                            "contents": [
                                {"type": "text", "text": meta['sl_label'], "size": "xs", "color": "#881337", "weight": "bold"},
                                {"type": "text", "text": sl_str, "size": "xl", "weight": "bold", "color": "#BE123C", "margin": "xs"},
                                {"type": "text", "text": meta['sl_desc'], "size": "xxs", "color": "#E11D48", "margin": "xs"}
                            ]
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "sm",
                    "spacing": "xs",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "盤中量能", "size": "xs", "color": "#64748B", "flex": 3},
                                {"type": "text", "text": f"{vol:,} 張" if isinstance(vol, (int, float)) else str(vol), "size": "xs", "weight": "bold", "color": "#1E293B", "flex": 7}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "隔日沖風控", "size": "xs", "color": "#64748B", "flex": 3},
                                {"type": "text", "text": og_risk if og_risk else "🟢 主力籌碼沉澱 (低風險)", "size": "xs", "weight": "bold", "color": "#15803D" if "沉澱" in str(og_risk) or not og_risk else "#DC2626", "flex": 7}
                            ]
                        }
                    ] + ([
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "期貨合約", "size": "xs", "color": "#64748B", "flex": 3},
                                {"type": "text", "text": f"{fut_contract} {fut_month} ｜ {basis_desc}" if basis_desc else f"{fut_contract} {fut_month}", "size": "xs", "weight": "bold", "color": "#0284C7", "flex": 7}
                            ]
                        }
                    ] + ([
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "期貨未平倉", "size": "xs", "color": "#64748B", "flex": 3},
                                {"type": "text", "text": f"{int(target.get('futures_oi') or target.get('期貨未平倉(OI)') or 0):,} 口", "size": "xs", "weight": "bold", "color": "#0F766E", "flex": 7}
                            ]
                        }
                    ] if (target.get('futures_oi') or target.get('期貨未平倉(OI)')) else []) + ([
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "結算提醒", "size": "xs", "color": "#64748B", "flex": 3},
                                {"type": "text", "text": str(target.get('settlement_tag') or target.get('結算倒數') or ''), "size": "xs", "weight": "bold", "color": "#D97706" if "⚠️" in str(target.get('settlement_tag') or target.get('結算倒數') or '') else "#0F766E", "flex": 7}
                            ]
                        }
                    ] if (target.get('settlement_tag') or target.get('結算倒數')) else []) + ([
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "次月新倉參考", "size": "xs", "color": "#64748B", "flex": 3},
                                {"type": "text", "text": f"{target.get('next_month') or target.get('次月合約')}月 ｜ 進場約 ${format_price_val(target.get('next_entry') or target.get('次月進場參考'))}", "size": "xs", "weight": "bold", "color": "#2563EB", "flex": 7}
                            ]
                        }
                    ] if (target.get('next_month') or target.get('次月合約')) and (target.get('next_entry') or target.get('次月進場參考')) else []) + [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "1口保證金", "size": "xs", "color": "#64748B", "flex": 3},
                                {"type": "text", "text": f"約 ${int(margin_1lot):,} 元 (一口=2張現貨)" if margin_1lot else "依期交所標準級距", "size": "xs", "weight": "bold", "color": "#1E293B", "flex": 7}
                            ]
                        }
                    ] if fut_contract and fut_contract not in ('--', '--F', 'None') else [])
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#F8FAFC",
                    "cornerRadius": "6px",
                    "paddingAll": "10px",
                    "margin": "sm",
                    "contents": [
                        {"type": "text", "text": f"💡【實戰指引】{event_desc}", "size": "xs", "color": "#334155", "wrap": True}
                    ]
                },
                {
                    "type": "text",
                    "text": f"⏰ 偵測時間：{time_str} ｜ 操盤請嚴守紀律，部位分批進出",
                    "size": "xxs",
                    "color": "#94A3B8",
                    "align": "center",
                    "margin": "xs"
                }
            ]
        }
    }

    clean_alt = f"🚨【盤中雷達】{code} {name} 觸發【{event_title}】：現價 {format_price_val(price)} 元"
    return {
        "type": "flex",
        "altText": clean_alt,
        "contents": bubble
    }

def format_clean_price_alert_text(
    target: dict,
    rt: dict,
    event_type: str,
    event_title: str,
    event_desc: str,
    strategy_id: str = "01"
) -> str:
    """產出零引線、高對比的純文字版本（用於 Telegram 備援與文字終端）"""
    import datetime
    meta = resolve_strategy_meta(strategy_id)
    code = str(target.get('code') or target.get('證券代號') or target.get('代號') or '')
    name = str(target.get('name') or target.get('證券名稱') or target.get('名稱') or '')
    stars = str(target.get('stars') or target.get('星級推薦') or target.get('星級') or '★★★★☆')
    price = rt.get('price') or target.get('收盤價') or 0.0
    high_p = rt.get('high') or price
    low_p = rt.get('low') or price
    vol = rt.get('volume') or target.get('當日成交量(張)') or target.get('日成交量(張)') or 0
    time_str = rt.get('time') or datetime.datetime.now().strftime('%H:%M:%S')

    entry_val = target.get('entry_price') or target.get('進場價位') or target.get('空單進場')
    addon_val = target.get('addon_price') or target.get('加碼價位') or target.get('加空價位')
    tp1_val = target.get('tp1_price') or target.get('停利TP1')
    tp2_val = target.get('tp2_price') or target.get('停利TP2')
    sl_val = target.get('sl_price') or target.get('停損價位') or target.get('停損回補')
    og_risk = target.get('隔日沖風控') or target.get('og_risk') or ''

    entry_str = format_price_val(entry_val)
    addon_str = format_price_val(addon_val)
    tp_str = f"TP1 {format_price_val(tp1_val)}  TP2 {format_price_val(tp2_val)}" if tp2_val else format_price_val(tp1_val)
    sl_str = format_price_val(sl_val)

    lines = [
        f"🚨【盤中雷達】{meta['name']}到價即時快訊",
        f"📍 監控標的：{code} {name} ｜ {stars}",
        f"🔥 觸發事件：【{event_title}】",
        f"💵 即時現價：{format_price_val(price)} 元 (今日高 {format_price_val(high_p)} / 低 {format_price_val(low_p)})",
        "",
        "🎯 實戰操盤四大防線：",
        f"{meta['entry_label']}  {entry_str} 元  ({meta['entry_desc']})",
        f"{meta['addon_label']}  {addon_str} 元  ({meta['addon_desc']})",
        f"{meta['tp_label']}  {tp_str}  ({meta['tp_desc']})",
        f"{meta['sl_label']}  {sl_str} 元  ({meta['sl_desc']})",
        "",
        "📊 即時量能與指標：",
        f"• 盤中量能：{vol:,} 張" if isinstance(vol, (int, float)) else f"• 盤中量能：{vol}",
    ]
    if og_risk:
        lines.append(f"• 隔日沖風控：{og_risk}")
    
    fut_contract = target.get('期貨契約') or target.get('contract_sym') or target.get('contract') or ''
    fut_month = target.get('期貨月份') or target.get('month') or ''
    basis_desc = target.get('價差(Basis)') or target.get('basis_desc') or target.get('basis_tag') or ''
    margin_1lot = target.get('1口保證金(約)') or target.get('margin_1lot')
    if fut_contract and fut_contract not in ('--', '--F', 'None'):
        lines.append(f"• 期貨合約：{fut_contract} {fut_month} ｜ 價差：{basis_desc} ｜ 1口保證金約 ${int(margin_1lot):,} 元")

    lines.extend([
        "",
        f"💡【實戰指引】{event_desc}",
        f"⏰ 偵測時間：{time_str}"
    ])
    return "\n".join(lines)

def build_aggregated_alert_card(
    alerts: list,
    strategy_id: str = "01",
    now_str: str = ""
) -> dict:
    """建構盤中多檔合併快訊 Flex 卡片 (單則訊息發送多檔，節省額度且防洗版)"""
    import datetime
    meta = resolve_strategy_meta(strategy_id)
    if not now_str:
        now_str = datetime.datetime.now().strftime('%H:%M:%S')

    bubble_contents = [
        {"type": "text", "text": f"本輪共觸發 {len(alerts)} 檔標的到價事件：", "size": "xs", "color": "#64748B", "margin": "xs"}
    ]

    for item in alerts[:10]:
        t = item['target']
        rt = item['rt']
        ecode = item.get('event_code', 'ENTRY')
        code = str(t.get('code') or t.get('證券代號') or t.get('代號') or '')
        name = str(t.get('name') or t.get('證券名稱') or t.get('名稱') or '')
        p = rt.get('price') or t.get('收盤價') or 0.0
        p_str = format_price_val(p)
        
        entry_str = format_price_val(t.get('entry_price') or t.get('進場價位') or t.get('空單進場'))
        addon_str = format_price_val(t.get('addon_price') or t.get('加碼價位') or t.get('加空價位'))
        tp1_str = format_price_val(t.get('tp1_price') or t.get('停利TP1'))
        sl_str = format_price_val(t.get('sl_price') or t.get('停損價位') or t.get('停損回補'))

        tag_text = item.get('event_title', ecode)
        if len(tag_text) > 16:
            tag_text = tag_text[:16]

        stock_box = {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F8FAFC",
            "borderColor": "#E2E8F0",
            "borderWidth": "1px",
            "cornerRadius": "8px",
            "paddingAll": "10px",
            "margin": "md",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": f"{code} {name}", "size": "md", "weight": "bold", "color": "#0F172A", "flex": 0},
                        {"type": "text", "text": f"現價 {p_str}", "size": "md", "weight": "bold", "color": "#DC2626", "align": "end"}
                    ]
                },
                {
                    "type": "text",
                    "text": f"🔥 {tag_text}",
                    "size": "xs",
                    "color": "#2563EB",
                    "weight": "bold",
                    "margin": "xs"
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "sm",
                    "contents": [
                        {"type": "text", "text": f"🟢進場:{entry_str}", "size": "xxs", "color": "#16A34A", "weight": "bold"},
                        {"type": "text", "text": f"🔵加碼:{addon_str}", "size": "xxs", "color": "#2563EB", "weight": "bold"},
                        {"type": "text", "text": f"🔴TP:{tp1_str}", "size": "xxs", "color": "#DC2626", "weight": "bold"},
                        {"type": "text", "text": f"🛑停損:{sl_str}", "size": "xxs", "color": "#991B1B", "weight": "bold"}
                    ]
                }
            ]
        }
        bubble_contents.append(stock_box)

    bubble_contents.append({
        "type": "text",
        "text": "💡 操盤指引：請依操盤紀律分批佈局，並確實執行停損防守！",
        "size": "xs",
        "color": "#64748B",
        "margin": "lg",
        "align": "center"
    })

    bubble = {
        "type": "bubble",
        "size": "giga",
        "header": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "backgroundColor": "#0F172A",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": "🔔 盤中多檔到價快訊", "size": "sm", "color": "#38BDF8", "weight": "bold", "flex": 0},
                        {"type": "text", "text": f"策略 {meta['id']} {meta['name']}", "size": "xs", "color": "#94A3B8", "align": "end"}
                    ]
                },
                {
                    "type": "text",
                    "text": f"⏱️ 偵測時間：{now_str}",
                    "size": "xs",
                    "color": "#94A3B8",
                    "margin": "xs"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "contents": bubble_contents
        }
    }

    clean_alt = f"🔔【{meta['name']}】盤中多檔到價快訊 (共 {len(alerts)} 檔標的)"
    return {
        "type": "flex",
        "altText": clean_alt,
        "contents": bubble
    }

def format_clean_aggregated_text(
    alerts: list,
    strategy_id: str = "01",
    now_str: str = ""
) -> str:
    """產出零引線的多檔合併快訊純文字版本"""
    import datetime
    meta = resolve_strategy_meta(strategy_id)
    if not now_str:
        now_str = datetime.datetime.now().strftime('%H:%M:%S')

    lines = [
        f"🔔【策略 {meta['id']}：{meta['name']}・盤中多檔到價快訊】",
        f"⏱️ 偵測時間：{now_str} ｜ 本輪共觸發 {len(alerts)} 檔標的",
        ""
    ]

    for item in alerts:
        t = item['target']
        rt = item['rt']
        code = str(t.get('code') or t.get('證券代號') or t.get('代號') or '')
        name = str(t.get('name') or t.get('證券名稱') or t.get('名稱') or '')
        p = rt.get('price') or t.get('收盤價') or 0.0
        stars = str(t.get('stars') or t.get('星級推薦') or '')
        star_str = f" ({stars})" if stars else ""
        
        entry_str = format_price_val(t.get('entry_price') or t.get('進場價位') or t.get('空單進場'))
        addon_str = format_price_val(t.get('addon_price') or t.get('加碼價位') or t.get('加空價位'))
        tp1_str = format_price_val(t.get('tp1_price') or t.get('停利TP1'))
        sl_str = format_price_val(t.get('sl_price') or t.get('停損價位') or t.get('停損回補'))

        lines.extend([
            f"• {code} {name}：現價 {format_price_val(p)} 元{star_str}",
            f"  {meta['entry_label']} {entry_str}  {meta['addon_label']} {addon_str}  {meta['tp_label']} {tp1_str}  {meta['sl_label']} {sl_str}",
            ""
        ])

    lines.append("💡 操盤指引：請依操盤紀律分批佈局，並確實執行停損防守！")
    return "\n".join(lines)

def build_screener_carousel(
    results: list,
    date_str: str,
    strategy_id: str = "01",
    max_stocks: int = 6
) -> dict:
    """建構盤後選股日報 Flex Carousel 輪播卡 (前 3~6 檔每檔一張精美獨立卡片)"""
    meta = resolve_strategy_meta(strategy_id)
    sorted_res = sorted(results, key=lambda x: x.get('評分', x.get('score', 0)), reverse=True) if results else []

    if not sorted_res:
        bubble = {
            "type": "bubble",
            "size": "giga",
            "header": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "16px",
                "backgroundColor": "#0F172A",
                "contents": [
                    {"type": "text", "text": f"📊【盤後選股】{meta['name']}", "size": "sm", "color": "#38BDF8", "weight": "bold"},
                    {"type": "text", "text": f"📅 交易日期：{date_str}", "size": "xs", "color": "#94A3B8", "margin": "xs"}
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "20px",
                "contents": [
                    {"type": "text", "text": "今日經多重 4+1 濾網嚴格檢核，無符合條件標的。", "size": "sm", "color": "#334155", "wrap": True},
                    {"type": "text", "text": "💡 保持資金充裕與零虧損，耐心靜待下一次高勝率進場時機！", "size": "xs", "color": "#64748B", "margin": "md", "wrap": True}
                ]
            }
        }
        return {
            "type": "flex",
            "altText": f"📊【盤後選股】{meta['name']}：今日無符合條件標的",
            "contents": bubble
        }

    bubbles = []
    for r in sorted_res[:max_stocks]:
        code = str(r.get('證券代號') or r.get('代號') or '')
        name = str(r.get('證券名稱') or r.get('名稱') or '')
        stars = str(r.get('星級推薦') or r.get('星級') or '★★★★☆')
        score = r.get('評分') or r.get('score') or '--'
        price = r.get('收盤價') or r.get('現價') or 0.0

        entry_val = r.get('進場價位') or r.get('空單進場') or r.get('entry_price')
        addon_val = r.get('加碼價位') or r.get('加空價位') or r.get('addon_price')
        tp1_val = r.get('停利TP1') or r.get('tp1_price')
        tp2_val = r.get('停利TP2') or r.get('tp2_price')
        sl_val = r.get('停損價位') or r.get('停損回補') or r.get('sl_price')
        og_risk = r.get('隔日沖風控') or r.get('og_risk') or ''
        vol = r.get('當日成交量(張)') or r.get('日成交量(張)') or 0

        fut_contract = r.get('期貨契約') or r.get('contract_sym') or r.get('contract') or ''
        fut_month = r.get('期貨月份') or r.get('month') or ''
        fut_price = r.get('期貨收盤價') or r.get('futures_close') or ''
        basis_desc = r.get('價差(Basis)') or r.get('basis_desc') or r.get('basis_tag') or ''
        margin_1lot = r.get('1口保證金(約)') or r.get('margin_1lot')

        title_name = f"{code} {name} ({fut_contract})" if fut_contract and fut_contract not in ('--', '--F', 'None') else f"{code} {name}"

        entry_str = format_price_val(entry_val)
        addon_str = format_price_val(addon_val)
        tp_str = f"TP1 {format_price_val(tp1_val)} / TP2 {format_price_val(tp2_val)}" if tp2_val else format_price_val(tp1_val)
        sl_str = format_price_val(sl_val)

        b = {
            "type": "bubble",
            "size": "giga",
            "header": {
                "type": "box",
                "layout": "vertical",
                "paddingTop": "14px",
                "paddingBottom": "10px",
                "paddingStart": "18px",
                "paddingEnd": "18px",
                "backgroundColor": "#0F172A",
                "contents": [
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "alignItems": "center",
                        "contents": [
                            {"type": "text", "text": "📊 盤後量化日報", "size": "xs", "color": "#38BDF8", "weight": "bold", "flex": 0},
                            {"type": "text", "text": f"策略 {meta['id']} {meta['name']}", "size": "xs", "color": "#94A3B8", "align": "end"}
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "md",
                        "alignItems": "center",
                        "contents": [
                            {"type": "text", "text": title_name, "size": "xxl", "weight": "bold", "color": "#F8FAFC", "flex": 0},
                            {"type": "text", "text": f"{stars} ({score}分)" if score != '--' else stars, "size": "xs", "color": "#FBBF24", "align": "end"}
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "sm",
                        "alignItems": "center",
                        "contents": [
                            {"type": "text", "text": "收盤價", "size": "sm", "color": "#94A3B8", "flex": 0},
                            {"type": "text", "text": f" {format_price_val(price)}", "size": "3xl", "weight": "bold", "color": "#F8FAFC", "flex": 0}
                        ]
                    }
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "16px",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": "🎯 操盤四大防線", "size": "sm", "weight": "bold", "color": "#334155"},
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "md",
                        "contents": [
                            {
                                "type": "box",
                                "layout": "vertical",
                                "flex": 1,
                                "backgroundColor": "#F0FDF4",
                                "borderColor": "#86EFAC",
                                "borderWidth": "1px",
                                "cornerRadius": "8px",
                                "paddingAll": "10px",
                                "contents": [
                                    {"type": "text", "text": meta['entry_label'], "size": "xs", "color": "#166534", "weight": "bold"},
                                    {"type": "text", "text": entry_str, "size": "xl", "weight": "bold", "color": "#15803D", "margin": "xs"},
                                    {"type": "text", "text": meta['entry_desc'], "size": "xxs", "color": "#16A34A", "margin": "xs"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "vertical",
                                "flex": 1,
                                "backgroundColor": "#EFF6FF",
                                "borderColor": "#93C5FD",
                                "borderWidth": "1px",
                                "cornerRadius": "8px",
                                "paddingAll": "10px",
                                "contents": [
                                    {"type": "text", "text": meta['addon_label'], "size": "xs", "color": "#1E40AF", "weight": "bold"},
                                    {"type": "text", "text": addon_str, "size": "xl", "weight": "bold", "color": "#1D4ED8", "margin": "xs"},
                                    {"type": "text", "text": meta['addon_desc'], "size": "xxs", "color": "#2563EB", "margin": "xs"}
                                ]
                            }
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "md",
                        "contents": [
                            {
                                "type": "box",
                                "layout": "vertical",
                                "flex": 1,
                                "backgroundColor": "#FEF2F2",
                                "borderColor": "#FCA5A5",
                                "borderWidth": "1px",
                                "cornerRadius": "8px",
                                "paddingAll": "10px",
                                "contents": [
                                    {"type": "text", "text": meta['tp_label'], "size": "xs", "color": "#991B1B", "weight": "bold"},
                                    {"type": "text", "text": tp_str, "size": "md", "weight": "bold", "color": "#DC2626", "margin": "xs"},
                                    {"type": "text", "text": meta['tp_desc'], "size": "xxs", "color": "#DC2626", "margin": "xs"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "vertical",
                                "flex": 1,
                                "backgroundColor": "#FFF1F2",
                                "borderColor": "#FDA4AF",
                                "borderWidth": "1px",
                                "cornerRadius": "8px",
                                "paddingAll": "10px",
                                "contents": [
                                    {"type": "text", "text": meta['sl_label'], "size": "xs", "color": "#881337", "weight": "bold"},
                                    {"type": "text", "text": sl_str, "size": "xl", "weight": "bold", "color": "#BE123C", "margin": "xs"},
                                    {"type": "text", "text": meta['sl_desc'], "size": "xxs", "color": "#E11D48", "margin": "xs"}
                                ]
                            }
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "sm",
                        "spacing": "xs",
                        "contents": [
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "成交量能", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": f"{vol:,} 張" if isinstance(vol, (int, float)) else str(vol), "size": "xs", "weight": "bold", "color": "#1E293B", "flex": 7}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "隔日沖風控", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": og_risk if og_risk else "🟢 主力籌碼沉澱 (低風險)", "size": "xs", "weight": "bold", "color": "#15803D" if "沉澱" in str(og_risk) or not og_risk else "#DC2626", "flex": 7}
                                ]
                            }
                        ] + ([
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "期貨合約", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": f"{fut_contract} {fut_month} ｜ {basis_desc}" if basis_desc else f"{fut_contract} {fut_month}", "size": "xs", "weight": "bold", "color": "#0284C7", "flex": 7}
                                ]
                            }
                        ] + ([
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "期貨未平倉", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": f"{int(r.get('futures_oi') or r.get('期貨未平倉(OI)') or 0):,} 口", "size": "xs", "weight": "bold", "color": "#0F766E", "flex": 7}
                                ]
                            }
                        ] if (r.get('futures_oi') or r.get('期貨未平倉(OI)')) else []) + ([
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "結算提醒", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": str(r.get('settlement_tag') or r.get('結算倒數') or ''), "size": "xs", "weight": "bold", "color": "#D97706" if "⚠️" in str(r.get('settlement_tag') or r.get('結算倒數') or '') else "#0F766E", "flex": 7}
                                ]
                            }
                        ] if (r.get('settlement_tag') or r.get('結算倒數')) else []) + ([
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "次月新倉參考", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": f"{r.get('next_month') or r.get('次月合約')}月 ｜ 進場約 ${format_price_val(r.get('next_entry') or r.get('次月進場參考'))}", "size": "xs", "weight": "bold", "color": "#2563EB", "flex": 7}
                                ]
                            }
                        ] if (r.get('next_month') or r.get('次月合約')) and (r.get('next_entry') or r.get('次月進場參考')) else []) + [
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "1口保證金", "size": "xs", "color": "#64748B", "flex": 3},
                                    {"type": "text", "text": f"約 ${int(margin_1lot):,} 元 (一口=2張現貨)" if margin_1lot else "依期交所標準級距", "size": "xs", "weight": "bold", "color": "#1E293B", "flex": 7}
                                ]
                            }
                        ] if fut_contract and fut_contract not in ('--', '--F', 'None') else [])
                    },
                    {
                        "type": "text",
                        "text": f"📅 {date_str} 盤後結算 ｜ 嚴守紀律，到達防線確實執行",
                        "size": "xxs",
                        "color": "#94A3B8",
                        "align": "center",
                        "margin": "xs"
                    }
                ]
            }
        }
        bubbles.append(b)

    clean_alt = f"📊【盤後選股】{meta['name']}日報 (共 {len(sorted_res)} 檔，前 {len(bubbles)} 檔精華)"
    return {
        "type": "flex",
        "altText": clean_alt,
        "contents": {
            "type": "carousel",
            "contents": bubbles
        }
    }

def format_clean_screener_text(
    results: list,
    date_str: str,
    strategy_id: str = "01",
    max_stocks: int = 6
) -> str:
    """產出零引線的盤後選股純文字日報（用於 Telegram 備援與文字終端）"""
    meta = resolve_strategy_meta(strategy_id)
    sorted_res = sorted(results, key=lambda x: x.get('評分', x.get('score', 0)), reverse=True) if results else []

    lines = [
        f"📊【台股盤後】{meta['name']}量化日報",
        f"📅 交易日期：{date_str} 盤後結算",
        f"🔥 今日精選：共 {len(sorted_res)} 檔（嚴選前 {min(len(sorted_res), max_stocks)} 檔精華）",
        ""
    ]

    if not sorted_res:
        lines.append("今日無符合 4+1 嚴格條件標的，維持資金安全與零虧損。")
        return "\n".join(lines)

    for i, r in enumerate(sorted_res[:max_stocks], 1):
        code = str(r.get('證券代號') or r.get('代號') or '')
        name = str(r.get('證券名稱') or r.get('名稱') or '')
        stars = str(r.get('星級推薦') or r.get('星級') or '★★★★☆')
        score = r.get('評分') or r.get('score') or '--'
        price = r.get('收盤價') or r.get('現價') or 0.0

        entry_val = r.get('進場價位') or r.get('空單進場') or r.get('entry_price')
        addon_val = r.get('加碼價位') or r.get('加空價位') or r.get('addon_price')
        tp1_val = r.get('停利TP1') or r.get('tp1_price')
        tp2_val = r.get('停利TP2') or r.get('tp2_price')
        sl_val = r.get('停損價位') or r.get('停損回補') or r.get('sl_price')
        og_risk = r.get('隔日沖風控') or r.get('og_risk') or ''
        vol = r.get('當日成交量(張)') or r.get('日成交量(張)') or 0

        entry_str = format_price_val(entry_val)
        addon_str = format_price_val(addon_val)
        tp_str = f"TP1 {format_price_val(tp1_val)}  TP2 {format_price_val(tp2_val)}" if tp2_val else format_price_val(tp1_val)
        sl_str = format_price_val(sl_val)

        lines.extend([
            f"【{i:02d}】📍 {code} {name} ｜ {stars} (評分:{score})",
            f"💵 收盤現價：{format_price_val(price)} 元",
            "",
            "🎯 操盤四大防線：",
            f"{meta['entry_label']}  {entry_str} 元  ({meta['entry_desc']})",
            f"{meta['addon_label']}  {addon_str} 元  ({meta['addon_desc']})",
            f"{meta['tp_label']}  {tp_str}  ({meta['tp_desc']})",
            f"{meta['sl_label']}  {sl_str} 元  ({meta['sl_desc']})",
            "",
            "📈 量能與風控：",
            f"• 成交量能：{vol:,} 張" if isinstance(vol, (int, float)) else f"• 成交量能：{vol}",
        ])
        if og_risk:
            lines.append(f"• 隔日沖風控：{og_risk}")
        
        fut_contract = r.get('期貨契約') or r.get('contract_sym') or r.get('contract') or ''
        fut_month = r.get('期貨月份') or r.get('month') or ''
        basis_desc = r.get('價差(Basis)') or r.get('basis_desc') or r.get('basis_tag') or ''
        margin_1lot = r.get('1口保證金(約)') or r.get('margin_1lot')
        if fut_contract and fut_contract not in ('--', '--F', 'None'):
            fut_oi = r.get('期貨未平倉(OI)') or r.get('futures_oi') or 0
            settle_tag = r.get('結算倒數') or r.get('settlement_tag') or ''
            oi_str = f" ｜ 未平倉：{int(fut_oi):,}口" if fut_oi else ""
            settle_str = f"\n  {settle_tag}" if settle_tag else ""
            lines.append(f"• 期貨契約：{fut_contract} {fut_month} ｜ 價差：{basis_desc}{oi_str} ｜ 1口保證金約 ${int(margin_1lot):,} 元{settle_str}")

        lines.append("")

    if len(sorted_res) > max_stocks:
        lines.append(f"📋 其餘 {len(sorted_res) - max_stocks} 檔標的完整數據請查閱 result.csv")
        lines.append("")
    lines.append("💡【風控紀律】到達建倉區分批進場，跌破停損價無條件執行！")
    return "\n".join(lines)


def format_screener_markdown_cards(
    results: list,
    date_str: str,
    strategy_id: str = "01",
    max_stocks: int = 10
) -> str:
    """
    將選股結果格式化為符合最高總指揮中心規範之 Markdown 視覺化字卡報表 (Visual Card Typography)
    適用於 result.md、weekly_report.md 以及各策略盤後戰報輸出。
    """
    meta = resolve_strategy_meta(strategy_id)
    sorted_res = sorted(results, key=lambda x: x.get('評分', x.get('score', 0)), reverse=True) if results else []

    md_lines = [
        f"# 📊【策略 {meta['id']}：{meta['name']}】盤後量化戰報",
        "",
        f"- **📅 交易日期**：`{date_str}` 盤後結算",
        f"- **🔥 精選標的**：共 `{len(sorted_res)}` 檔（展示前 `{min(len(sorted_res), max_stocks)}` 檔核心精華字卡）",
        f"- **🎯 操盤原則**：嚴守 4+1 維度量化濾網 ｜ 四大操盤防線價位 ｜ 移動停利紀律",
        "",
        "---",
        ""
    ]

    if not sorted_res:
        md_lines.extend([
            "> [!NOTE]",
            f"> 今日經多重 4+1 濾網嚴格檢核，全市場無符合【{meta['name']}】條件之標的。",
            "> 建議維持資金充裕與零虧損，耐心靜待下一次高勝率反轉或起漲訊號！",
            ""
        ])
        return "\n".join(md_lines)

    for i, r in enumerate(sorted_res[:max_stocks], 1):
        code = str(r.get('證券代號') or r.get('代號') or '')
        name = str(r.get('證券名稱') or r.get('名稱') or '')
        stars = str(r.get('星級推薦') or r.get('星級') or '★★★★☆')
        score = r.get('評分') or r.get('score') or '--'
        price = r.get('收盤價') or r.get('現價') or 0.0

        entry_val = r.get('進場價位') or r.get('空單進場') or r.get('entry_price')
        addon_val = r.get('加碼價位') or r.get('加空價位') or r.get('addon_price')
        tp1_val = r.get('停利TP1') or r.get('tp1_price')
        tp2_val = r.get('停利TP2') or r.get('tp2_price')
        sl_val = r.get('停損價位') or r.get('停損回補') or r.get('sl_price')
        og_risk = r.get('隔日沖風控') or r.get('og_risk') or ''
        vol = r.get('當日成交量(張)') or r.get('日成交量(張)') or 0

        fut_contract = r.get('期貨契約') or r.get('contract_sym') or r.get('contract') or ''
        fut_month = r.get('期貨月份') or r.get('month') or ''
        basis_desc = r.get('價差(Basis)') or r.get('basis_desc') or r.get('basis_tag') or ''
        margin_1lot = r.get('1口保證金(約)') or r.get('margin_1lot')

        title_badge = f"{code} {name} ({fut_contract})" if fut_contract and fut_contract not in ('--', '--F', 'None') else f"{code} {name}"

        entry_str = format_price_val(entry_val)
        addon_str = format_price_val(addon_val)
        tp_str = f"TP1 `{format_price_val(tp1_val)}` 元 ｜ TP2 `{format_price_val(tp2_val)}` 元" if tp2_val else f"`{format_price_val(tp1_val)}` 元"
        sl_str = format_price_val(sl_val)

        status_tag = r.get('多空狀態') or ("🟢【偏多起漲・高勝率】" if meta['id'] in ("01", "02", "04", "05", "08") else "🔴【弱勢破線・避險放空】")

        md_lines.extend([
            f"### 🏷️ 【{i:02d}】{title_badge} {stars} ({score}分) {status_tag}",
            f"> 💵 **最新現價**：`{format_price_val(price)}` 元 ｜ **當日成交量**：`{vol:,}` 張" if isinstance(vol, (int, float)) else f"> 💵 **最新現價**：`{format_price_val(price)}` 元 ｜ **當日成交量**：`{vol}`",
            "> ",
            f"> 🎯 **操盤四大防線價位**：",
            f"> - {meta['entry_label']}：`{entry_str}` 元 ({meta['entry_desc']})",
            f"> - {meta['addon_label']}：`{addon_str}` 元 ({meta['addon_desc']})",
            f"> - {meta['tp_label']}：{tp_str} ({meta['tp_desc']})",
            f"> - {meta['sl_label']}：`{sl_str}` 元 ({meta['sl_desc']})",
            "> ",
            "> ⚖️ **量能與風控指標**：",
            f"> - 隔日沖風控：{og_risk if og_risk else '🟢 主力籌碼沉澱 (低風險)'}",
        ])

        if fut_contract and fut_contract not in ('--', '--F', 'None'):
            fut_oi = r.get('期貨未平倉(OI)') or r.get('futures_oi') or 0
            settle_tag = r.get('結算倒數') or r.get('settlement_tag') or ''
            oi_str = f" ｜ 未平倉：`{int(fut_oi):,}` 口" if fut_oi else ""
            settle_str = f" ｜ {settle_tag}" if settle_tag else ""
            margin_str = f" ｜ 1口保證金約 `${int(margin_1lot):,}` 元" if margin_1lot else ""
            md_lines.append(f"> - 股期規格：`{fut_contract} {fut_month}` ｜ 價差：`{basis_desc}`{oi_str}{margin_str}{settle_str}")

        if meta['id'] == "03":
            playbook_empty = f"反彈回測【{entry_str} 元】附近若承壓不破，為黃金空單切入點，嚴禁追低破線空。"
            playbook_hold = f"空單部位以【{sl_str} 元】為停損回補點；摜破【{addon_str} 元】順勢加空，抵達【{format_price_val(tp1_val)} 元】分批回補 1/2。"
        else:
            playbook_empty = f"位階安全未追高，【{entry_str} 元以下】皆為合適建倉點，嚴守紀律不追高。"
            playbook_hold = f"持股者以【{sl_str} 元】嚴守防守線；放量攻過【{addon_str} 元】順勢加碼，攻抵【{format_price_val(tp1_val)} 元】分批拔檔。"

        md_lines.extend([
            "> ",
            "> 💡 **雙向操盤錦囊**：",
            f"> - **【空手者】**：{playbook_empty}",
            f"> - **【持股者】**：{playbook_hold}",
            "",
            "---",
            ""
        ])

    return "\n".join(md_lines)


def build_stock_diagnosis_card(data: dict) -> dict:
    """建構單檔個股即時量化診斷旗艦 Flex Message 卡片 (高對比大字體、零引線、四大防線一目了然)"""
    code = str(data.get('code', ''))
    name = str(data.get('name', ''))
    current_price = float(data.get('current_price', 0.0))
    chg_val = float(data.get('chg_val', 0.0))
    chg_pct = float(data.get('chg_pct', 0.0))
    chg_icon = str(data.get('chg_icon', '🔺' if chg_val > 0 else ('🔻' if chg_val < 0 else '▫️')))
    stars = str(data.get('stars', '★★★★☆'))
    int_score = data.get('int_score', 4)
    eval_badge = str(data.get('eval_badge', '🟢【偏多優質・進場高勝率】'))
    time_str = str(data.get('time', '即時報價'))

    entry_price = float(data.get('entry_price', current_price))
    addon_price = float(data.get('addon_price', current_price * 1.02))
    tp1_price = float(data.get('tp1_price', current_price * 1.07))
    tp2_price = float(data.get('tp2_price', current_price * 1.15))
    stop_loss = float(data.get('stop_loss', current_price * 0.95))

    vol_sheets = data.get('vol_sheets', 0)
    vol_status = str(data.get('vol_status', '常態量能'))
    ma5 = float(data.get('ma5', current_price))
    ma20 = float(data.get('ma20', current_price))
    bias20 = float(data.get('bias20', 0.0))
    reward = float(data.get('reward', max(tp1_price - entry_price, 0.0)))
    risk = float(data.get('risk', max(entry_price - stop_loss, 0.1)))
    rr_ratio = float(data.get('rr_ratio', 2.0))

    playbook_empty = str(data.get('playbook_empty', f'位階安全未追高，【{entry_price:.2f} 元以下】皆為合適建倉點。'))
    playbook_hold = str(data.get('playbook_hold', f'持股者以【{stop_loss:.2f} 元】嚴守防守線；放量過【{addon_price:.2f} 元】可順勢加碼。'))

    price_color = "#EF4444" if chg_val > 0 else ("#22C55E" if chg_val < 0 else "#94A3B8")

    bubble = {
        "type": "bubble",
        "size": "giga",
        "header": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "backgroundColor": "#0F172A",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": "🔍 個股即時量化診斷", "size": "xs", "color": "#38BDF8", "weight": "bold", "flex": 0},
                        {"type": "text", "text": f"報價時間: {time_str}", "size": "xxs", "color": "#94A3B8", "align": "end"}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": f"{code} {name}", "size": "xxl", "weight": "bold", "color": "#F8FAFC", "flex": 0},
                        {"type": "text", "text": f"{stars} ({int_score}星)", "size": "xs", "color": "#FBBF24", "align": "end"}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "sm",
                    "alignItems": "baseline",
                    "contents": [
                        {"type": "text", "text": "現價", "size": "sm", "color": "#94A3B8", "flex": 0},
                        {"type": "text", "text": f" {current_price:.2f}", "size": "3xl", "weight": "bold", "color": "#F8FAFC", "flex": 0},
                        {"type": "text", "text": f"  {chg_icon} {chg_val:+.2f} ({chg_pct:+.2f}%)", "size": "sm", "weight": "bold", "color": price_color, "margin": "md"}
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "sm",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "vertical",
                            "backgroundColor": "#1E293B",
                            "cornerRadius": "6px",
                            "paddingStart": "8px",
                            "paddingEnd": "8px",
                            "paddingTop": "4px",
                            "paddingBottom": "4px",
                            "contents": [
                                {"type": "text", "text": eval_badge, "size": "xs", "color": "#38BDF8", "weight": "bold"}
                            ]
                        }
                    ]
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "🎯 操盤四大防線價位 (標準指引)", "size": "sm", "weight": "bold", "color": "#334155"},
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 1,
                            "backgroundColor": "#F0FDF4",
                            "borderColor": "#86EFAC",
                            "borderWidth": "1px",
                            "cornerRadius": "8px",
                            "paddingAll": "10px",
                            "contents": [
                                {"type": "text", "text": "🟢 建議進場", "size": "xs", "color": "#166534", "weight": "bold"},
                                {"type": "text", "text": f"{entry_price:.2f}", "size": "xl", "weight": "bold", "color": "#15803D", "margin": "xs"},
                                {"type": "text", "text": "買進上限 / 防追高", "size": "xxs", "color": "#16A34A", "margin": "xs"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 1,
                            "backgroundColor": "#EFF6FF",
                            "borderColor": "#93C5FD",
                            "borderWidth": "1px",
                            "cornerRadius": "8px",
                            "paddingAll": "10px",
                            "contents": [
                                {"type": "text", "text": "🔵 動能加碼", "size": "xs", "color": "#1E40AF", "weight": "bold"},
                                {"type": "text", "text": f"{addon_price:.2f}", "size": "xl", "weight": "bold", "color": "#1D4ED8", "margin": "xs"},
                                {"type": "text", "text": "放量過高右側確認", "size": "xxs", "color": "#2563EB", "margin": "xs"}
                            ]
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 1,
                            "backgroundColor": "#FEF2F2",
                            "borderColor": "#FCA5A5",
                            "borderWidth": "1px",
                            "cornerRadius": "8px",
                            "paddingAll": "10px",
                            "contents": [
                                {"type": "text", "text": "🔴 目標停利", "size": "xs", "color": "#991B1B", "weight": "bold"},
                                {"type": "text", "text": f"TP1 {tp1_price:.2f} / TP2 {tp2_price:.2f}", "size": "sm", "weight": "bold", "color": "#DC2626", "margin": "xs"},
                                {"type": "text", "text": "前高反壓 / 1:1滿足", "size": "xxs", "color": "#DC2626", "margin": "xs"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 1,
                            "backgroundColor": "#FFF1F2",
                            "borderColor": "#FDA4AF",
                            "borderWidth": "1px",
                            "cornerRadius": "8px",
                            "paddingAll": "10px",
                            "contents": [
                                {"type": "text", "text": "🛑 嚴格停損", "size": "xs", "color": "#881337", "weight": "bold"},
                                {"type": "text", "text": f"{stop_loss:.2f}", "size": "xl", "weight": "bold", "color": "#BE123C", "margin": "xs"},
                                {"type": "text", "text": "跌破防守無條件退", "size": "xxs", "color": "#E11D48", "margin": "xs"}
                            ]
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#F8FAFC",
                    "cornerRadius": "8px",
                    "paddingAll": "10px",
                    "contents": [
                        {"type": "text", "text": "⚖️ 量能與風控指標", "size": "xs", "weight": "bold", "color": "#475569"},
                        {"type": "text", "text": f"• 成交量能：{vol_sheets:,} 張 ({vol_status})" if isinstance(vol_sheets, (int, float)) else f"• 成交量能：{vol_sheets} ({vol_status})", "size": "xxs", "color": "#64748B", "margin": "xs"},
                        {"type": "text", "text": f"• 均線乖離：5MA {ma5:.2f} ｜ 20MA {ma20:.2f} (月線乖離 {bias20:+.1f}%)", "size": "xxs", "color": "#64748B", "margin": "xs"},
                        {"type": "text", "text": f"• 盈虧試算：獲利 +{reward:.2f} ｜ 風險 -{risk:.2f} ｜ 盈虧比 {rr_ratio:.1f}:1", "size": "xxs", "color": "#64748B", "margin": "xs"}
                    ]
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#F1F5F9",
                    "cornerRadius": "8px",
                    "paddingAll": "10px",
                    "contents": [
                        {"type": "text", "text": "💡 雙向操盤錦囊", "size": "xs", "weight": "bold", "color": "#334155"},
                        {"type": "text", "text": f"【空手者】{playbook_empty}", "size": "xxs", "color": "#475569", "wrap": True, "margin": "xs"},
                        {"type": "text", "text": f"【持股者】{playbook_hold}", "size": "xxs", "color": "#475569", "wrap": True, "margin": "xs"}
                    ]
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "horizontal",
            "paddingAll": "12px",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#10B981",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "➕ 掛入盤中雷達",
                        "text": f"監控 {code}"
                    }
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "🤖 同步選股",
                        "text": "同步選股"
                    }
                }
            ]
        }
    }

    return {
        "type": "flex",
        "altText": f"📊【{code} {name}】個股量化診斷戰報",
        "contents": bubble
    }


def format_clean_stock_diagnosis_text(data: dict) -> str:
    """產出零引線、高對比的個股診斷純文字字卡 (備援與窄螢幕無縫適應)"""
    code = str(data.get('code', ''))
    name = str(data.get('name', ''))
    current_price = float(data.get('current_price', 0.0))
    chg_val = float(data.get('chg_val', 0.0))
    chg_pct = float(data.get('chg_pct', 0.0))
    chg_icon = str(data.get('chg_icon', '🔺' if chg_val > 0 else ('🔻' if chg_val < 0 else '▫️')))
    stars = str(data.get('stars', '★★★★☆'))
    int_score = data.get('int_score', 4)
    eval_badge = str(data.get('eval_badge', '🟢【偏多優質・進場高勝率】'))
    time_str = str(data.get('time', '即時報價'))

    entry_price = float(data.get('entry_price', current_price))
    addon_price = float(data.get('addon_price', current_price * 1.02))
    tp1_price = float(data.get('tp1_price', current_price * 1.07))
    tp2_price = float(data.get('tp2_price', current_price * 1.15))
    stop_loss = float(data.get('stop_loss', current_price * 0.95))

    vol_sheets = data.get('vol_sheets', 0)
    vol_status = str(data.get('vol_status', '常態量能'))
    ma5 = float(data.get('ma5', current_price))
    ma20 = float(data.get('ma20', current_price))
    bias20 = float(data.get('bias20', 0.0))
    reward = float(data.get('reward', max(tp1_price - entry_price, 0.0)))
    risk = float(data.get('risk', max(entry_price - stop_loss, 0.1)))
    rr_ratio = float(data.get('rr_ratio', 2.0))

    playbook_empty = str(data.get('playbook_empty', f'位階安全未追高，【{entry_price:.2f} 元以下】皆為合適建倉點。'))
    playbook_hold = str(data.get('playbook_hold', f'持股者以【{stop_loss:.2f} 元】嚴守防守線；放量過【{addon_price:.2f} 元】可順勢加碼。'))

    vol_str = f"{vol_sheets:,} 張" if isinstance(vol_sheets, (int, float)) else str(vol_sheets)

    lines = [
        f"🏷️ 診斷標的：{code} {name}",
        f"💵 最新現價：{current_price:.2f} 元 ({chg_icon} {chg_val:+.2f} / {chg_pct:+.2f}%)",
        f"⭐ 量化評等：{stars} ({int_score}星) {eval_badge}",
        f"⏰ 報價時間：{time_str}",
        "",
        "🎯 實戰四大防線價位 (標準指引)：",
        f"• 🟢 建議進場：{entry_price:.2f} 元 (買進上限 / 防追高)",
        f"• 🔵 動能加碼：{addon_price:.2f} 元 (放量過高右側確認)",
        f"• 🔴 第一停利：{tp1_price:.2f} 元 (前高反壓分批落袋)",
        f"• 🔴 波段滿足：{tp2_price:.2f} 元 (1:1等幅滿足點)",
        f"• 🛑 嚴格停損：{stop_loss:.2f} 元 (跌破防守無條件退)",
        "",
        "⚖️ 量能與風控指標：",
        f"• 成交量能：{vol_str} ({vol_status})",
        f"• 均線乖離：5MA {ma5:.2f} ｜ 20MA {ma20:.2f} (月線乖離 {bias20:+.1f}%)",
        f"• 盈虧試算：預期 +{reward:.2f} ｜ 風險 -{risk:.2f} ｜ 盈虧比 {rr_ratio:.1f}:1",
        "",
        "💡 雙向操盤錦囊：",
        f"【空手者】{playbook_empty}",
        f"【持股者】{playbook_hold}",
        "",
        f"💡 盤中盯盤：輸入「監控 {code}」即刻掛入雷達，到價自動推播！"
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LINE 推播測試工具")
    parser.add_argument("message", nargs="?", default="🔔 這是一則來自台股選股策略的 LINE 測試通知！", help="測試訊息文字")
    parser.add_argument("--strategy", type=str, default=None, help="指定策略編號 (01, 02, 03)")
    parser.add_argument("--target", type=str, default=None, help="直接指定發送目標 ID (User ID 或 Group ID)")
    args = parser.parse_args()

    send_to_line(args.message, strategy=args.strategy, target_id=args.target)
