#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE 推播發送模組
支援：
1. LINE Messaging API (LINE 官方機器人 Push Message，2025/2026 官方標準方案，免費)
2. LINE Notify (向後相容/第三方轉接)
"""

import os
import sys
import json
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

def send_line_messaging_api(token: str, user_id: str, message: str) -> bool:
    """使用 LINE Messaging API (Push Message) 發送訊息"""
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print("[OK] LINE Messaging API 推播發送成功！")
                return True
            else:
                print(f"[!] LINE 推播失敗，狀態碼: {resp.status}")
                return False
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='ignore')
        print(f"[X] LINE Messaging API HTTP 錯誤 {e.code}: {err_body}")
        return False
    except Exception as e:
        print(f"[X] 發送 LINE 訊息異常: {e}")
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

def send_to_line(message: str) -> bool:
    """
    通用 LINE 發送入口：
    優先使用 LINE Messaging API (Channel Access Token + User ID)
    其次嘗試 LINE Notify
    """
    env = load_env()
    line_token = env.get("LINE_CHANNEL_ACCESS_TOKEN") or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    line_user_id = env.get("LINE_USER_ID") or os.environ.get("LINE_USER_ID")
    notify_token = env.get("LINE_NOTIFY_TOKEN") or os.environ.get("LINE_NOTIFY_TOKEN")

    if line_token and line_user_id:
        return send_line_messaging_api(line_token, line_user_id, message)
    elif notify_token:
        return send_line_notify(notify_token, message)
    else:
        print("\n" + "!" * 60)
        print("【尚未設定 LINE 推播憑證】")
        print(f"請在 {ENV_PATH} 中設定：")
        print("LINE_CHANNEL_ACCESS_TOKEN=你的機器人存取憑證")
        print("LINE_USER_ID=你的LINE使用者ID (U開頭字串)")
        print("!" * 60 + "\n")
        return False

if __name__ == "__main__":
    test_msg = sys.argv[1] if len(sys.argv) > 1 else "🔔 這是一則來自台股選股策略的 LINE 測試通知！"
    send_to_line(test_msg)
