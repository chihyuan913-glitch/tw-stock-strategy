#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股即時隨傳隨回 LINE 操盤機器人伺服器 (Interactive LINE Bot Server)
功能：
1. 接收來自手機 LINE 聊天室的訊息 Webhook (POST /callback)。
2. 使用者在 LINE 輸入任何台股代碼（例如 "3221"、"2330" 或 "分析 2603"）。
3. 背景非同步調用量化分析引擎 (stock_analyzer.py)，精算操盤四大防線價位：
   - 🟢 建議進場價位 (Entry Price)
   - 🔵 動能加碼價位 (Add-on Price)
   - 🔴 第一停利與波段滿足 (TP1 / TP2)
   - 🛑 嚴格停損價位 (Stop-Loss Price)
   - ⚖️ 盈虧比 (Risk / Reward) 與操盤指引
4. 透過 LINE Reply API (免費無限額度) 秒級回傳完整卡片到使用者的 LINE 視窗！
"""

import os
import sys
import re
import json
import urllib.request
from pathlib import Path
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn

# 導入同目錄下的股票分析引擎
from stock_analyzer import analyze_stock

# Windows 命令列編碼保護
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 讀取 .env 取得 LINE Channel Access Token
ENV_PATH = Path(__file__).resolve().parent / ".env"
def load_env():
    env_vars = {}
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip().strip("'\"")
    return env_vars

ENV = load_env()
CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or ENV.get("LINE_CHANNEL_ACCESS_TOKEN", "")

app = FastAPI(title="台股盤中即時隨傳隨回 LINE Bot")

def reply_line_message(reply_token: str, text: str):
    """呼叫 LINE Messaging API Reply Token 回覆訊息 (免費無上限)"""
    if not CHANNEL_ACCESS_TOKEN:
        print("[!] 錯誤：未設定 LINE_CHANNEL_ACCESS_TOKEN 環境變數", flush=True)
        return
    if not reply_token:
        return

    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            print(f"[✓] 成功回覆 LINE 訊息 (狀態碼: {resp.status})", flush=True)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='ignore')
        print(f"[!] LINE Reply 失敗 (HTTP {e.code}): {err_body}", flush=True)
    except Exception as e:
        print(f"[!] 回覆 LINE 訊息例外: {e}", flush=True)

def process_event_task(event: dict):
    """背景處理 LINE 訊息事件並進行即時個股分析"""
    event_type = event.get('type')
    reply_token = event.get('replyToken')

    if event_type != 'message':
        return

    msg = event.get('message', {})
    if msg.get('type') != 'text':
        return

    user_text = msg.get('text', '').strip()
    user_id = event.get('source', {}).get('userId', '未知用戶')
    print(f"[*] 收到來自 [{user_id[:8]}...] 的訊息: \"{user_text}\"", flush=True)

    # 檢查是否為台股 4 碼股票代碼 (例如 "3221", "分析 2330", "2603 可以買嗎")
    match = re.search(r'\b(\d{4})\b', user_text)
    if match:
        stock_code = match.group(1)
        print(f"[*] 正在分析股票代碼 【{stock_code}】...", flush=True)
        analysis_result = analyze_stock(stock_code)
        reply_line_message(reply_token, analysis_result)
        return

    # 若非 4 碼代碼，回覆友善使用說明
    help_text = """👋 歡迎使用【台股即時量化操盤小助手】！

📱 盤中隨時傳送任一台股 4 碼代碼，系統將立即在 3 秒內為您精算【操盤四大防線價位】：

範例輸入：
• 3221
• 2330
• 分析 2603

🎯 回傳包含：
🟢 建議進場價位（黃金回測低接區）
🔵 動能加碼價位（突破確認追擊點）
🔴 停利目標（TP1 前高反壓 / TP2 波段滿足）
🛑 嚴格停損價位（風控撤退防線）
⚖️ 即時盈虧比（Risk / Reward）與盤面建議！"""

    reply_line_message(reply_token, help_text)

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "台股盤中隨傳隨回即時 LINE Bot 伺服器",
        "line_token_configured": bool(CHANNEL_ACCESS_TOKEN)
    }

@app.post("/callback")
async def callback(request: Request, background_tasks: BackgroundTasks):
    """LINE Webhook 接收端點"""
    try:
        body = await request.json()
    except Exception:
        return {"status": "invalid_json"}

    events = body.get('events', [])
    for event in events:
        background_tasks.add_task(process_event_task, event)

    return {"status": "ok", "events_queued": len(events)}

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    print(f"=====================================================")
    print(f"  台股即時隨傳隨回 LINE 操盤機器人伺服器啟動中...")
    print(f"  本機端點: http://127.0.0.1:{port}/callback")
    print(f"  LINE Token 設定狀態: {'已就緒' if CHANNEL_ACCESS_TOKEN else '未設定'}")
    print(f"=====================================================")
    uvicorn.run("line_bot_server:app", host="0.0.0.0", port=port, reload=False)
