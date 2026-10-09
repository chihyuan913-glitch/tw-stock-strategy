#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE 群組 ID 擷取小助手 (LINE Group ID Fetcher)
用途：協助您取得各策略專屬群組的 Group ID (以 C 開頭的 33 字元代碼)，
      並可一鍵自動寫入 .env 設定檔！

使用步驟：
1. 在 LINE App 內建立 3 個專屬群組（例如「01-超跌抄底」、「02-法人起漲」、「03-做空避險」）。
2. 將您的 LINE 官方帳號機器人邀請進該群組。
   (請確認 manager.line.biz 的帳號設定中已啟用「允許加入群組」)。
3. 使用任何免費內網穿透工具 (如 ngrok 或 pinggy) 將本機 5000 埠對外公開：
   - 指令範例: ngrok http 5000  (或 ssh -p 443 -R0:localhost:5000 a.pinggy.io)
4. 將產生的公開網址貼到 LINE Developers -> Messaging API -> Webhook URL (例如 https://xxxx/webhook)。
5. 在群組內隨意打個字（例如「id」），本程式將自動抓取群組 ID 並提示您綁定！
"""

import os
import sys
import json
import urllib.request
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# Windows 命令列編碼保護
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

PORT = 5000
ENV_PATH = Path(__file__).resolve().parent / ".env"

def update_env_variable(key: str, value: str):
    """安全更新或新增 .env 中的變數"""
    lines = []
    found = False
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"\n[✓] 成功將 {key}={value} 儲存至 .env！")

class LineWebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 靜音常規日誌
        return

    def do_POST(self):
        content_len = int(self.headers.get('Content-Length', 0))
        post_body = self.rfile.read(content_len)

        try:
            data = json.loads(post_body.decode('utf-8'))
            events = data.get('events', [])
            for event in events:
                src = event.get('source', {})
                src_type = src.get('type')
                group_id = src.get('groupId')
                user_id = src.get('userId')
                reply_token = event.get('replyToken')

                if group_id:
                    print("\n" + "=" * 65)
                    print(f"🎉【成功偵測到 LINE 群組訊號！】")
                    print(f"📌 群組 Group ID: {group_id}")
                    if user_id:
                        print(f"👤 發言者 User ID: {user_id}")
                    print("=" * 65)
                    print(f"\n請選擇要將此群組綁定至哪一項策略？")
                    print(f"  [1] LINE_TARGET_STRATEGY_01 (布林超跌抄底)")
                    print(f"  [2] LINE_TARGET_STRATEGY_02 (法人籌碼起漲)")
                    print(f"  [3] LINE_TARGET_STRATEGY_03 (股期做空避險)")
                    print(f"  [Enter] 僅顯示，不自動寫入")

                    # 若環境中有 TOKEN，可自動在群組內回覆
                    # (可選回覆)
                elif user_id and src_type == "user":
                    print(f"\n[i] 收到個人私訊訊號，您的個人 User ID 為: {user_id}")

        except Exception as e:
            print(f"[!] 解析 Webhook 發生錯誤: {e}")

        # 回應 200 給 LINE 平台
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"OK")

def main():
    print("=" * 65)
    print(" 🛠️  LINE 群組 ID 擷取助手 (支援一鍵自動寫入 .env)")
    print(f" 🌐 本機監聽埠位: http://localhost:{PORT}/")
    print("=" * 65)
    print("【操作指引】：")
    print("1. 請先確保已將 Bot 邀請進目標 LINE 群組。")
    print("2. 透過 ngrok 開啟穿透： ngrok http 5000")
    print("3. 將 ngrok 網址貼至 LINE Developers 的 Webhook URL 並點擊 Verify。")
    print("4. 在該 LINE 群組內發送任意訊息（例如輸入 'id'），此處將即時顯示 Group ID！\n")
    print("[*] 正在等待 LINE Webhook 訊號傳入中... (按 Ctrl+C 可結束)")

    server = HTTPServer(('0.0.0.0', PORT), LineWebhookHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[✓] 已停止服務。")

if __name__ == '__main__':
    main()
