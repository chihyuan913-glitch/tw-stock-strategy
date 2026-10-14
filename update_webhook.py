#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE Official Account Webhook 自動管理與診斷工具
================================================================================
用於快速查詢、更新與測試 LINE 機器人之 Webhook Endpoint。
支援自動讀取 web_url.txt 或接收自訂 URL。
"""

import sys
import os
import json
import argparse
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tunnel_manager import sync_line_webhook, get_current_tunnel_url

def load_token() -> str:
    env_file = ROOT_DIR / ".env"
    token = ""
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("LINE_CHANNEL_ACCESS_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip("'\"")
                    break
    return token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

def get_webhook_endpoint(token: str):
    url = "https://api.line.me/v2/bot/channel/webhook/endpoint"
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("=====================================================")
            print("  📋 目前 LINE 官方帳號 Webhook 狀態")
            print("=====================================================")
            print(f"  • 端點網址 (Endpoint): {data.get('endpoint')}")
            print(f"  • 啟用狀態 (Active):   {data.get('active')}")
            print("=====================================================")
            return data
    except Exception as e:
        print(f"[!] 查詢失敗: {e}")
        return None

def test_webhook_endpoint(token: str, endpoint: str):
    url = "https://api.line.me/v2/bot/channel/webhook/test"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    body = json.dumps({"endpoint": endpoint}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("=====================================================")
            print("  ⚡ LINE Webhook 官方連線測試結果")
            print("=====================================================")
            print(f"  • 測試狀態 (Success): {data.get('success')}")
            print(f"  • HTTP 代碼 (Code):    {data.get('statusCode')}")
            print(f"  • 原因說明 (Reason):  {data.get('reason')}")
            print(f"  • 詳細資訊 (Detail):  {data.get('detail')}")
            print("=====================================================")
            return data
    except Exception as e:
        print(f"[!] 測試例外: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="LINE Webhook 自動管理與測試工具")
    parser.add_argument("--url", help="自訂欲綁定之 Webhook URL (例如: https://xxx.trycloudflare.com/callback)")
    parser.add_argument("--status", action="store_true", help="僅查詢目前設定狀態")
    parser.add_argument("--test", action="store_true", help="僅針對目前端點執行連線握手測試")
    args = parser.parse_args()

    token = load_token()
    if not token:
        print("[!] 錯誤：未設定 LINE_CHANNEL_ACCESS_TOKEN 環境變數。")
        sys.exit(1)

    if args.status:
        get_webhook_endpoint(token)
        return

    if args.test:
        curr = get_webhook_endpoint(token)
        if curr and curr.get("endpoint"):
            test_webhook_endpoint(token, curr.get("endpoint"))
        return

    target_url = args.url or get_current_tunnel_url()
    if not target_url:
        print("[!] 未指定 URL 且 web_url.txt 中無活躍網址。")
        print("    請指定 --url <網址> 或先啟動 tunnel_manager / run_line_bot.py")
        sys.exit(1)

    print(f"[*] 正在將 Webhook 綁定至: {target_url}")
    sync_line_webhook(target_url)

if __name__ == "__main__":
    main()
