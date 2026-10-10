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

    # 1. 處理機器人被加入新群組 (獨立視窗建立)
    if event_type == 'join':
        source = event.get('source', {})
        group_id = source.get('groupId', '')
        print(f"[+] 機器人已成功加入新群組！群組 ID: {group_id}", flush=True)
        welcome_msg = f"""🎉 成功建立【台股即時量化操盤指引】獨立專屬視窗！

📱 本視窗已連線雲端量化引擎，盤中請隨時在此輸入任一台股 4 碼代碼（例如：3221、2330、2603），機器人將在此專屬視窗為您即時秒回四大防線價位操盤指引！

💡 本群組專屬 Group ID：
{group_id}"""
        reply_line_message(reply_token, welcome_msg)
        return

    if event_type != 'message':
        return

    msg = event.get('message', {})
    if msg.get('type') != 'text':
        return

    user_text = msg.get('text', '').strip()
    source = event.get('source', {})
    group_id = source.get('groupId', '')
    user_id = source.get('userId', '未知用戶')
    from_desc = f"群組 [{group_id[:8]}...]" if group_id else f"個人 [{user_id[:8]}...]"
    print(f"[*] 收到來自 {from_desc} 的訊息: \"{user_text}\"", flush=True)

    # 導入雷達管理模組
    from radar_manager import add_stock_to_radar, remove_stock_from_radar, get_radar_summary
    target_dest = group_id or user_id

    # 1. 監控指令：例如 "監控 2476", "+2476", "加入 2476", "監控 鉅祥"
    if user_text.startswith(('監控 ', '加入 ', '追蹤 ', '盯盤 ', '+')) or '加入監控' in user_text:
        clean_target = re.sub(r'^(監控|加入|追蹤|盯盤|\+)\s*', '', user_text)
        res = add_stock_to_radar(clean_target, target_id=target_dest)
        reply_line_message(reply_token, res)
        return

    # 2. 查詢監控清單：例如 "監控清單", "清單", "查監控", "雷達清單"
    if user_text in ('監控清單', '清單', '查監控', '雷達清單', 'watchlist'):
        res = get_radar_summary()
        reply_line_message(reply_token, res)
        return

    # 3. 移除監控指令：例如 "刪除 2476", "取消 2476", "-2476", "移除 2476"
    if user_text.startswith(('刪除 ', '取消 ', '移除 ', '-')) or '取消監控' in user_text:
        clean_target = re.sub(r'^(刪除|取消|移除|\-)\s*', '', user_text)
        res = remove_stock_from_radar(clean_target)
        reply_line_message(reply_token, res)
        return

    # 4. 一般即時查詢：支援代碼或中文股名辨識 (例如 "2476", "分析 2330", "華容", "台積電")
    from stock_analyzer import resolve_stock_input
    stock_code, stock_name = resolve_stock_input(user_text)
    if stock_code:
        print(f"[*] 正在分析標的 【{stock_code} {stock_name}】...", flush=True)
        analysis_result = analyze_stock(user_text)
        analysis_result += f"\n💡 盤中盯盤：輸入「監控 {stock_code}」即刻掛入雷達，到價自動推播！"
        reply_line_message(reply_token, analysis_result)
        return

    # 若非指令或股名，回覆友善使用說明
    help_text = """👋 歡迎使用【台股即時量化操盤小助手】！

📱 功能一：盤中即時操盤指引
傳送台股 4 碼代碼或中文名稱（例如：5328、華容、2330），秒回四大防線價位與勝率評等！

⚡ 功能二：盤中自動到價推播雷達
• 輸入「監控 2476」或「+2476」：掛入自動盯盤雷達
• 輸入「監控清單」：查看目前盯盤標的與防線
• 輸入「刪除 2476」：移除監控

🕒 盤中交易時段 (09:00 ~ 13:35) 雲端全自動監控！
價格一旦觸及「🟢進場、🔵加碼、🔴停利、🛑停損」，立即在此視窗推播提醒！"""

    reply_line_message(reply_token, help_text)

@app.on_event("startup")
async def startup_event():
    """啟動盤中到價雷達背景巡邏任務 (09:00 ~ 13:35)"""
    import asyncio
    import datetime
    from radar_manager import scan_and_generate_alerts

    async def intraday_radar_worker():
        print("[✓] 盤中到價雷達背景巡邏引擎已啟動 (09:00 ~ 13:35 自動盯盤)", flush=True)
        while True:
            try:
                tz_tw = datetime.timezone(datetime.timedelta(hours=8))
                now_tw = datetime.datetime.now(tz_tw)
                # 週一至週五且時段在 08:58 ~ 13:35
                if now_tw.weekday() < 5:
                    t_int = now_tw.hour * 100 + now_tw.minute
                    if 858 <= t_int <= 1335:
                        default_uid = os.environ.get("LINE_USER_ID", "")
                        alerts = scan_and_generate_alerts(CHANNEL_ACCESS_TOKEN, default_uid)
                        if alerts:
                            print(f"[⚡] 盤中到價雷達觸發事件: {alerts}", flush=True)
            except Exception as e:
                print(f"[!] 盤中雷達輪詢例外: {e}", flush=True)
    async def render_keep_alive_worker():
        """Render 免費版防休眠背景心跳任務 (每 10 分鐘自動保活，防止 15 分鐘無流量休眠)"""
        external_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("SERVER_PUBLIC_URL", "")
        if not external_url:
            print("[i] 未設定 RENDER_EXTERNAL_URL，跳過內部自動保活心跳 (建議搭配外部 Ping 或開盤 GitHub 工作流)。", flush=True)
            return

        if not external_url.startswith("http"):
            external_url = f"https://{external_url}"
        ping_url = f"{external_url.rstrip('/')}/health"

        print(f"[✓] Render 防休眠心跳任務啟動，目標: {ping_url} (每 10 分鐘自動保活)", flush=True)
        await asyncio.sleep(60)  # 伺服器啟動後先等待 1 分鐘穩定

        while True:
            try:
                def _do_ping():
                    req = urllib.request.Request(ping_url, headers={'User-Agent': 'RenderKeepAlive/1.0'})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        return resp.status
                status = await asyncio.to_thread(_do_ping)
                print(f"[💓] Render 防休眠心跳觸發成功 (HTTP {status})", flush=True)
            except Exception as e:
                print(f"[!] Render 防休眠心跳例外: {e}", flush=True)
            await asyncio.sleep(600)  # 每 10 分鐘 ping 一次 (Render 休眠門檻為 15 分鐘)

    asyncio.create_task(render_keep_alive_worker())

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "台股盤中隨傳隨回即時 LINE Bot 伺服器",
        "line_token_configured": bool(CHANNEL_ACCESS_TOKEN),
        "intraday_radar_active": True
    }

@app.get("/health")
@app.get("/ping")
async def health():
    """專供 Render 防休眠檢測之輕量端點"""
    import datetime
    return {
        "status": "ok",
        "service": "tw-stock-line-bot",
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
