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
    payload = {
        "to": to_id,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
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
        send_telegram(tg_token, tg_chat, message)

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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LINE 推播測試工具")
    parser.add_argument("message", nargs="?", default="🔔 這是一則來自台股選股策略的 LINE 測試通知！", help="測試訊息文字")
    parser.add_argument("--strategy", type=str, default=None, help="指定策略編號 (01, 02, 03)")
    parser.add_argument("--target", type=str, default=None, help="直接指定發送目標 ID (User ID 或 Group ID)")
    args = parser.parse_args()

    send_to_line(args.message, strategy=args.strategy, target_id=args.target)
