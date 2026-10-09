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
        # 正規化策略編號 (如 1 -> 01, "01_bollinger" -> "01")
        if strat_key in ["1", "2", "3"]:
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

def send_line_messaging_api(token: str, to_id: str, message: str, target_label: str = "") -> bool:
    """使用 LINE Messaging API (Push Message) 發送訊息至指定對話或群組"""
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
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
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                masked_id = f"{to_id[:5]}...{to_id[-4:]}" if len(to_id) > 10 else to_id
                label_info = f" [{target_label}]" if target_label else ""
                print(f"[✓] LINE Messaging API 推播發送成功！目標: {masked_id}{label_info}")
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

def send_to_line(message: str, strategy: str = None, target_id: str = None) -> bool:
    """
    通用 LINE 發送入口：
    支援以 strategy 代號 ("01", "02", "03") 定向投遞至獨立 LINE 視窗/群組！
    未設定專屬目標時自動 fallback 至 LINE_USER_ID。
    """
    env = load_env()
    line_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or env.get("LINE_CHANNEL_ACCESS_TOKEN")
    to_id, target_label = resolve_target_id(strategy=strategy, target_id=target_id, env=env)
    notify_token = os.environ.get("LINE_NOTIFY_TOKEN") or env.get("LINE_NOTIFY_TOKEN")

    if line_token and to_id:
        return send_line_messaging_api(line_token, to_id, message, target_label=target_label)
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
