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
        welcome_msg = f"""🎉 成功加入新群組！

💡 若要將本群組設定為【盤中自動監控雷達・獨立專屬視窗】：
請在此群組內直接輸入：
👉「設定雷達視窗」

綁定後，所有盤中到價警報（進場、加碼、停利、停損）將全數定點發送至此群組，與個人隨問隨答視窗徹底分流！"""
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

    # 導入雷達管理模組 (支援四大進階匯入方案與獨立視窗分流)
    from radar_manager import (
        batch_add_to_radar,
        remove_stock_from_radar,
        get_radar_summary,
        sync_screener_to_radar,
        clear_all_radar,
        get_themes_summary,
        set_radar_channel,
        get_radar_channel,
        clear_radar_channel
    )
    target_dest = group_id or user_id

    # 0. 設定獨立雷達視窗指令
    if user_text in ('設定雷達視窗', '設為雷達視窗', '綁定雷達', '雷達視窗'):
        if group_id:
            set_radar_channel(group_id, "盤中自動監控雷達專屬視窗")
            msg = f"""🎯【成功綁定：盤中到價自動監控雷達 獨立專屬視窗！】
━━━━━━━━━━━━━━━
本群組已正式設定為「到價警報獨立專屬視窗」！

✅ 雙視窗徹底隔離生效：
1. 📱 個人隨問隨答視窗：維持安靜純淨，僅在您主動輸入代碼時秒回分析，絕無任何自動推播干擾。
2. ⚡ 本雷達專屬視窗：所有盤中自動到價推播（🟢進場、🔵加碼、🔴停利、🛑停損）全數在此獨立發布！

💡 在此視窗您可直接使用四大免逐檔指令：
•「同步選股」一鍵掛入今日策略黑馬股
•「監控 2476 3221」批次掛入自選股
•「監控 矽光子」熱門概念股一鍵打包
•「監控清單」查看目前盯盤標的與防線"""
            reply_line_message(reply_token, msg)
            return
        else:
            msg = """💡【如何新設雷達獨立專屬視窗？】
────────────────
若要將「盤中自動到價雷達」與「隨問隨答助手」分開：

1. 在 LINE App 建立一個新群組（例如命名為：【台股盤中自動監控雷達】）。
2. 把本官方帳號（機器人）邀請進入該新群組。
3. 在該群組內發送一句「設定雷達視窗」或「綁定雷達」。

系統即會將該新群組綁定為專屬雷達視窗！所有盤中到價推播只會在該群組發出，絕不干擾個人聊天室！"""
            reply_line_message(reply_token, msg)
            return

    if user_text in ('解除雷達視窗', '解綁雷達'):
        clear_radar_channel()
        reply_line_message(reply_token, "🗑️ 已解除雷達專屬視窗綁定，到價警報將恢復預設發送。")
        return

    # 方案二：全自動連動每日選股戰報
    if user_text in ('同步選股', '同步策略', '同步日報', '同步戰報', 'sync'):
        res = sync_screener_to_radar(target_id=target_dest)
        reply_line_message(reply_token, res)
        return

    # 方案四：查詢熱門題材清單
    if user_text in ('族群清單', '題材清單', '概念股清單', '熱門題材', 'themes'):
        res = get_themes_summary()
        reply_line_message(reply_token, res)
        return

    # 清空所有監控
    if user_text in ('清空監控', '全部清空', '全部刪除', 'clear'):
        res = clear_all_radar()
        reply_line_message(reply_token, res)
        return

    # 查詢監控清單
    if user_text in ('監控清單', '清單', '查監控', '雷達清單', 'watchlist'):
        res = get_radar_summary()
        reply_line_message(reply_token, res)
        return

    # 方案一、三、四：掛入監控 (支援多檔批次貼上、題材打包、單檔)
    if user_text.startswith(('監控 ', '加入 ', '追蹤 ', '盯盤 ', '+')) or '加入監控' in user_text:
        clean_target = re.sub(r'^(監控|加入|追蹤|盯盤|\+)\s*', '', user_text)
        res = batch_add_to_radar(clean_target, target_id=target_dest)
        reply_line_message(reply_token, res)
        return

    # 移除監控指令
    if user_text.startswith(('刪除 ', '取消 ', '移除 ', '-')) or '取消監控' in user_text:
        clean_target = re.sub(r'^(刪除|取消|移除|\-)\s*', '', user_text)
        res = remove_stock_from_radar(clean_target)
        reply_line_message(reply_token, res)
        return

    # 一般即時個股查詢 (支援代碼或中文名稱)
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

📱 功能一：個股即時操盤指引
傳送台股 4 碼代碼或中文名稱（例如：5328、華容、2330），秒回四大防線價位與勝率評等！

⚡ 功能二：盤中自動到價雷達 (四大免逐檔匯入方案)
1️⃣【多檔批次貼上】：輸入「監控 2330 2476 3221 5328 2603」整批鎖定
2️⃣【連動選股日報】：輸入「同步選股」一鍵載入策略最新黑馬股
3️⃣【熱門題材打包】：輸入「監控 矽光子」或「監控 散熱」整組概念股打包
4️⃣【查看現有盯盤】：輸入「監控清單」或「族群清單」

🕒 盤中交易時段 (09:00 ~ 13:35) 雲端全自動盯盤！
價格觸及「🟢進場、🔵加碼、🔴停利、🛑停損」立即在此視窗推播！"""

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
