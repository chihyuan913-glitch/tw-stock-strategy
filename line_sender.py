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
                        {"type": "text", "text": f"{code} {name}", "size": "xxl", "weight": "bold", "color": "#F8FAFC", "flex": 0},
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
                    ]
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
                            {"type": "text", "text": f"{code} {name}", "size": "xxl", "weight": "bold", "color": "#F8FAFC", "flex": 0},
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
                        ]
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
        lines.append("")

    if len(sorted_res) > max_stocks:
        lines.append(f"📋 其餘 {len(sorted_res) - max_stocks} 檔標的完整數據請查閱 result.csv")
        lines.append("")
    lines.append("💡【風控紀律】到達建倉區分批進場，跌破停損價無條件執行！")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LINE 推播測試工具")
    parser.add_argument("message", nargs="?", default="🔔 這是一則來自台股選股策略的 LINE 測試通知！", help="測試訊息文字")
    parser.add_argument("--strategy", type=str, default=None, help="指定策略編號 (01, 02, 03)")
    parser.add_argument("--target", type=str, default=None, help="直接指定發送目標 ID (User ID 或 Group ID)")
    args = parser.parse_args()

    send_to_line(args.message, strategy=args.strategy, target_id=args.target)
