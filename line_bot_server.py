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

import collections
import datetime

# 雲端即時記憶體日誌隊列 (供在線除錯診斷)
SERVER_LOGS = collections.deque(maxlen=200)

def log_debug(msg: str):
    """帶時間戳的除錯日誌，同時輸出至 stdout 與記憶體日誌隊列"""
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    ts = datetime.datetime.now(tz_tw).strftime("%H:%M:%S")
    formatted = f"[{ts}] {msg}"
    print(formatted, flush=True)
    SERVER_LOGS.append(formatted)

def reply_line_message(reply_token: str, text: str):
    """呼叫 LINE Messaging API Reply Token 回覆訊息 (免費無上限)"""
    if not CHANNEL_ACCESS_TOKEN:
        log_debug("[!] 錯誤：未設定 LINE_CHANNEL_ACCESS_TOKEN 環境變數")
        return
    if not reply_token:
        log_debug("[!] 警告：reply_token 為空，無法回覆")
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
        with urllib.request.urlopen(req, timeout=8) as resp:
            log_debug(f"[✓] 成功回覆 LINE 訊息 (狀態碼: {resp.status})")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='ignore')
        log_debug(f"[!] LINE Reply 失敗 (HTTP {e.code}): {err_body}")
    except Exception as e:
        log_debug(f"[!] 回覆 LINE 訊息例外: {e}")

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
    log_debug(f"[*] 收到來自 {from_desc} 的訊息: \"{user_text}\"")

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
        clear_radar_channel,
        get_radar_status,
        generate_test_alert,
        reset_today_alerts
    )
    target_dest = group_id or user_id

    # 統一文字去空白並轉小寫做意圖比對
    u_norm = user_text.replace(" ", "").lower()

    # 0. 設定獨立雷達視窗指令 (繁簡體與意圖全面涵蓋)
    is_bind_cmd = (
        u_norm in {'設定雷達視窗', '設為雷達視窗', '綁定雷達', '雷達視窗', 
                   '设定雷达视窗', '设为雷达视窗', '绑定雷达', '雷达视窗', 'bindradar'} or
        ('雷達' in user_text and any(k in user_text for k in ('綁定', '設定', '視窗', '群組'))) or
        ('雷达' in user_text and any(k in user_text for k in ('绑定', '设定', '视窗', '群组')))
    )

    if is_bind_cmd:
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
•「監控清單」查看目前盯盤標的與防線
•「雷達測試」發送模擬到價警報卡片驗證"""
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

    # 測試推播 (繁簡相容)
    if u_norm in ('雷達測試', '測試推播', '雷达测试', '测试推送', '測試雷達', '测试雷达', 'testpush', 'test'):
        res = generate_test_alert(CHANNEL_ACCESS_TOKEN, target_dest)
        reply_line_message(reply_token, res)
        return

    # 查詢雷達狀態 (繁簡相容)
    if u_norm in ('雷達狀態', '監控狀態', '雷达状态', '监控状态', '系統狀態', '系统状态', 'status'):
        res = get_radar_status()
        reply_line_message(reply_token, res)
        return

    # 重置今日警報 (繁簡相容)
    if u_norm in ('重置警報', '重置警报', '重設警報', '重设警报', 'resetalerts', 'reset'):
        res = reset_today_alerts()
        reply_line_message(reply_token, res)
        return

    # 解除綁定指令 (繁簡相容)
    if u_norm in ('解除雷達視窗', '解綁雷達', '解除雷达视窗', '解绑雷达', 'unbind'):
        clear_radar_channel()
        reply_line_message(reply_token, "🗑️ 已解除雷達專屬視窗綁定，到價警報將恢復預設發送。")
        return

    # 方案二：全自動連動每日選股戰報 (繁簡相容)
    if u_norm in ('同步選股', '同步策略', '同步日報', '同步戰報', '同步选股', '同步策略', '同步日报', '同步战报', 'sync'):
        res = sync_screener_to_radar(target_id=target_dest)
        reply_line_message(reply_token, res)
        return

    # 方案四：查詢熱門題材清單 (繁簡相容)
    if u_norm in ('族群清單', '題材清單', '概念股清單', '熱門題材', '族群清单', '题材清单', '概念股清单', '热门题材', 'themes'):
        res = get_themes_summary()
        reply_line_message(reply_token, res)
        return

    # 清空所有監控 (繁簡相容)
    if u_norm in ('清空監控', '全部清空', '全部刪除', '清空监控', '全部删除', 'clear'):
        res = clear_all_radar()
        reply_line_message(reply_token, res)
        return

    # 查詢監控清單 (繁簡相容)
    if u_norm in ('監控清單', '清單', '查監控', '雷達清單', '监控清单', '清单', '查监控', '雷达清单', 'watchlist'):
        res = get_radar_summary()
        reply_line_message(reply_token, res)
        return

    # 方案一、三、四：掛入監控 (支援多檔批次貼上、題材打包、單檔，繁簡體與 + 號)
    if re.match(r'^(監控|加入|追蹤|盯盤|监控|追踪|盯盘|\+)\s*', user_text) or any(k in user_text for k in ('加入監控', '加入监控', '掛入雷達', '挂入雷达')):
        clean_target = re.sub(r'^(監控|加入|追蹤|盯盤|监控|追踪|盯盘|\+)\s*', '', user_text)
        res = batch_add_to_radar(clean_target, target_id=target_dest)
        reply_line_message(reply_token, res)
        return

    # 移除監控指令 (繁簡相容)
    if re.match(r'^(刪除|取消|移除|删除|\-)\s*', user_text) or any(k in user_text for k in ('取消監控', '取消监控', '移除監控', '移除监控')):
        clean_target = re.sub(r'^(刪除|取消|移除|删除|\-)\s*', '', user_text)
        res = remove_stock_from_radar(clean_target)
        reply_line_message(reply_token, res)
        return

    # 一般即時個股查詢 (支援代碼或中文名稱)
    from stock_analyzer import resolve_stock_input
    stock_code, stock_name = resolve_stock_input(user_text)
    if stock_code:
        log_debug(f"[*] 正在分析標的 【{stock_code} {stock_name}】...")
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
        log_debug("[✓] 盤中到價雷達背景巡邏引擎已啟動 (09:00 ~ 13:35 自動盯盤)")
        notified_morning = ""
        notified_closing = ""

        while True:
            try:
                tz_tw = datetime.timezone(datetime.timedelta(hours=8))
                now_tw = datetime.datetime.now(tz_tw)
                today_str = now_tw.strftime('%Y-%m-%d')
                weekday = now_tw.weekday()

                # 週一至週五且時段在 08:55 ~ 13:35
                if weekday < 5:
                    t_int = now_tw.hour * 100 + now_tw.minute
                    
                    # 1. 開盤晨報 (08:58 提醒今日監控標的)
                    if 858 <= t_int <= 900 and notified_morning != today_str:
                        from radar_manager import load_watchlist, get_radar_channel
                        w = load_watchlist()
                        target_ch = get_radar_channel()
                        if w and target_ch and CHANNEL_ACCESS_TOKEN:
                            m_card = f"""🔔【台股開盤前夕・盤中到價雷達全自動啟動】
━━━━━━━━━━━━━━━
今日共鎖定 {len(w)} 檔焦點標的！
雲端即時盯盤引擎已就緒，盤中將每 60 秒比對四大防線價位。
一旦觸及「🟢進場、🔵加碼、🔴停利、🛑停損」，將立即在此視窗為您即時推播！
祝福今日操盤順利，嚴守紀律！"""
                            push_url = "https://api.line.me/v2/bot/message/push"
                            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}
                            body = json.dumps({"to": target_ch, "messages": [{"type": "text", "text": m_card}]}, ensure_ascii=False).encode('utf-8')
                            try:
                                urllib.request.urlopen(urllib.request.Request(push_url, data=body, headers=headers, method="POST"), timeout=6)
                                log_debug(f"[✓] 成功發送開盤晨報至雷達視窗")
                            except Exception as e:
                                log_debug(f"[!] 開盤晨報推播失敗: {e}")
                        notified_morning = today_str

                    # 2. 盤中即時盯盤比對 (09:00 ~ 13:35)
                    if 858 <= t_int <= 1335:
                        default_uid = os.environ.get("LINE_USER_ID", "")
                        alerts = scan_and_generate_alerts(CHANNEL_ACCESS_TOKEN, default_uid)
                        if alerts:
                            log_debug(f"[⚡] 盤中到價雷達觸發事件: {', '.join(alerts)}")

                    # 3. 收盤總結 (13:31)
                    if 1331 <= t_int <= 1335 and notified_closing != today_str:
                        from radar_manager import get_radar_channel
                        target_ch = get_radar_channel()
                        if target_ch and CHANNEL_ACCESS_TOKEN:
                            c_card = f"""🏁【今日台股盤中到價雷達監控圓滿結束】
━━━━━━━━━━━━━━━
盤中交易已於 13:30 順利收盤！
今日到價事件推播完畢，雲端雷達轉入盤後待機模式。
💡 傍晚可輸入「同步選股」一鍵更新今日三大策略最新黑馬名單！"""
                            push_url = "https://api.line.me/v2/bot/message/push"
                            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}
                            body = json.dumps({"to": target_ch, "messages": [{"type": "text", "text": c_card}]}, ensure_ascii=False).encode('utf-8')
                            try:
                                urllib.request.urlopen(urllib.request.Request(push_url, data=body, headers=headers, method="POST"), timeout=6)
                                log_debug(f"[✓] 成功發送收盤總結至雷達視窗")
                            except Exception as e:
                                log_debug(f"[!] 收盤總結推播失敗: {e}")
                        notified_closing = today_str

            except Exception as e:
                log_debug(f"[!] 盤中雷達輪詢例外: {e}")

            await asyncio.sleep(60)

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

    asyncio.create_task(intraday_radar_worker())
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

@app.get("/logs")
async def get_logs():
    """查看雲端伺服器最近 200 筆即時日誌 (除錯專用)"""
    return {
        "count": len(SERVER_LOGS),
        "token_set": bool(CHANNEL_ACCESS_TOKEN),
        "token_prefix": CHANNEL_ACCESS_TOKEN[:10] + "..." if CHANNEL_ACCESS_TOKEN else "None",
        "logs": list(SERVER_LOGS)
    }

@app.post("/callback")
async def callback(request: Request, background_tasks: BackgroundTasks):
    """LINE Webhook 接收端點"""
    try:
        raw_body = await request.body()
        body = json.loads(raw_body.decode('utf-8'))
    except Exception as e:
        log_debug(f"[!] /callback 收到無效 JSON: {e}")
        return {"status": "invalid_json"}

    events = body.get('events', [])
    log_debug(f"[/callback] 收到 Webhook 請求，包含 {len(events)} 個事件")
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
