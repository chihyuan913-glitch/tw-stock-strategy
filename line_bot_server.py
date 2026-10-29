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
import time
import datetime
import urllib.request
from pathlib import Path
import threading
from typing import Set, Optional
from fastapi import FastAPI, Request, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
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

try:
    from lab.web.router import router as lab_router
    app.include_router(lab_router)
except Exception as _err:
    pass

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

def reply_line_message(reply_token: str, message_payload, target_id: str = None, fallback_text: str = None):
    """
    呼叫 LINE Messaging API Reply Token 回覆訊息 (免費無上限)
    支援：純文字 (str) 或 旗艦 Flex Message 卡片 (dict)
    具備雙軌防呆：若 Reply Token 逾時或出錯，且具備 target_id，自動無縫退回 Push API 確保 100% 送達。
    """
    if not CHANNEL_ACCESS_TOKEN:
        log_debug("[!] 錯誤：未設定 LINE_CHANNEL_ACCESS_TOKEN 環境變數")
        return
    if not reply_token and not target_id:
        log_debug("[!] 警告：reply_token 與 target_id 皆為空，無法回覆")
        return

    # 組裝 messages 陣列
    if isinstance(message_payload, dict):
        if message_payload.get("type") == "flex":
            messages = [message_payload]
        elif message_payload.get("type") == "bubble":
            messages = [{
                "type": "flex",
                "altText": "📊 個股量化診斷戰報",
                "contents": message_payload
            }]
        else:
            messages = [message_payload]
    else:
        messages = [{"type": "text", "text": str(message_payload)}]

    reply_url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
    }

    # 1. 優先嘗試 Reply API
    reply_succeeded = False
    if reply_token:
        try:
            payload = {
                "replyToken": reply_token,
                "messages": messages
            }
            req = urllib.request.Request(
                reply_url,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                log_debug(f"[✓] 成功回覆 LINE 訊息 (狀態碼: {resp.status})")
                reply_succeeded = True
                return
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='ignore')
            log_debug(f"[!] LINE Reply 失敗 (HTTP {e.code}): {err_body}")
            # 若為 Flex 訊息失敗且有備援純文字，嘗試發送純文字 Reply
            if fallback_text:
                try:
                    alt_payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": fallback_text}]}
                    req = urllib.request.Request(reply_url, data=json.dumps(alt_payload, ensure_ascii=False).encode('utf-8'), headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        log_debug(f"[✓] 備援純文字 Reply 成功 (狀態碼: {resp.status})")
                        reply_succeeded = True
                        return
                except Exception:
                    pass
        except Exception as e:
            log_debug(f"[!] 回覆 LINE 訊息例外: {e}")

    # 2. 若 Reply 失敗或 Reply Token 過期，但有 target_id，自動無縫退回 Push API
    if not reply_succeeded and target_id:
        log_debug(f"[*] 嘗試透過 Push API 發送訊息至目標視窗 [{target_id[:8]}...]...")
        push_url = "https://api.line.me/v2/bot/message/push"
        try:
            push_payload = {
                "to": target_id,
                "messages": messages
            }
            req = urllib.request.Request(
                push_url,
                data=json.dumps(push_payload, ensure_ascii=False).encode('utf-8'),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                log_debug(f"[✓] 成功透過 Push API 發送至目標視窗 (狀態碼: {resp.status})")
        except Exception as e:
            log_debug(f"[!] Push API 發送失敗: {e}")


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

    # 0. 設定獨立雷達視窗指令 (繁簡體與意圖精準比對，避免普通問答被誤判)
    is_bind_cmd = (
        u_norm in {'設定雷達視窗', '設為雷達視窗', '綁定雷達', '雷達視窗', 
                   '设定雷达视窗', '设为雷达视窗', '绑定雷达', '雷达视窗', 'bindradar'} or
        (u_norm.startswith('綁定雷達') or u_norm.startswith('設定雷達') or 
         u_norm.startswith('绑定雷达') or u_norm.startswith('设定雷达') or
         u_norm == '設定為雷達視窗' or u_norm == '设定为雷达视窗')
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
            reply_line_message(reply_token, msg, target_id=target_dest)
            return
        else:
            msg = """💡【如何新設雷達獨立專屬視窗？】
────────────────
若要將「盤中自動到價雷達」與「隨問隨答助手」分開：

1. 在 LINE App 建立一個新群組（例如命名為：【台股盤中自動監控雷達】）。
2. 把本官方帳號（機器人）邀請進入該新群組。
3. 在該群組內發送一句「設定雷達視窗」或「綁定雷達」。

系統即會將該新群組綁定為專屬雷達視窗！所有盤中到價推播只會在該群組發出，絕不干擾個人聊天室！"""
            reply_line_message(reply_token, msg, target_id=target_dest)
            return

    # 測試推播 (繁簡相容)
    if u_norm in ('雷達測試', '測試推播', '雷达测试', '测试推送', '測試雷達', '测试雷达', 'testpush', 'test'):
        res = generate_test_alert(CHANNEL_ACCESS_TOKEN, target_dest)
        reply_line_message(reply_token, res, target_id=target_dest)
        return

    # 查詢雷達狀態 (繁簡相容)
    if u_norm in ('雷達狀態', '監控狀態', '雷达状态', '监控状态', '系統狀態', '系统状态', 'status'):
        res = get_radar_status()
        reply_line_message(reply_token, res, target_id=target_dest)
        return

    # 重置今日警報 (繁簡相容)
    if u_norm in ('重置警報', '重置警报', '重設警報', '重设警报', 'resetalerts', 'reset'):
        res = reset_today_alerts()
        reply_line_message(reply_token, res, target_id=target_dest)
        return

    # 解除綁定指令 (繁簡相容)
    if u_norm in ('解除雷達視窗', '解綁雷達', '解除雷达视窗', '解绑雷达', 'unbind'):
        clear_radar_channel()
        reply_line_message(reply_token, "🗑️ 已解除雷達專屬視窗綁定，到價警報將恢復預設發送。", target_id=target_dest)
        return

    # 方案二：全自動連動每日選股戰報 (繁簡相容)
    if u_norm in ('同步選股', '同步策略', '同步日報', '同步戰報', '同步选股', '同步策略', '同步日报', '同步战报', 'sync'):
        res = sync_screener_to_radar(target_id=target_dest)
        reply_line_message(reply_token, res, target_id=target_dest)
        return

    # 方案四：查詢熱門題材清單 (繁簡相容)
    if u_norm in ('族群清單', '題材清單', '概念股清單', '熱門題材', '族群清单', '题材清单', '概念股清单', '热门题材', 'themes'):
        res = get_themes_summary()
        reply_line_message(reply_token, res, target_id=target_dest)
        return

    # 清空所有監控 (繁簡相容)
    if u_norm in ('清空監控', '全部清空', '全部刪除', '清空监控', '全部删除', 'clear'):
        res = clear_all_radar()
        reply_line_message(reply_token, res, target_id=target_dest)
        return

    # 查詢監控清單 (繁簡相容)
    if u_norm in ('監控清單', '清單', '查監控', '雷達清單', '监控清单', '清单', '查监控', '雷达清单', 'watchlist'):
        res = get_radar_summary()
        reply_line_message(reply_token, res, target_id=target_dest)
        return

    # 方案一、三、四：掛入監控 (支援多檔批次貼上、題材打包、單檔，繁簡體與 + 號)
    if re.match(r'^(監控|加入|追蹤|盯盤|监控|追踪|盯盘|\+)\s*', user_text) or any(k in user_text for k in ('加入監控', '加入监控', '掛入雷達', '挂入雷达')):
        clean_target = re.sub(r'^(監控|加入|追蹤|盯盤|监控|追踪|盯盘|\+)\s*', '', user_text)
        res = batch_add_to_radar(clean_target, target_id=target_dest)
        reply_line_message(reply_token, res, target_id=target_dest)
        return

    # 移除監控指令 (繁簡相容)
    if re.match(r'^(刪除|取消|移除|删除|\-)\s*', user_text) or any(k in user_text for k in ('取消監控', '取消监控', '移除監控', '移除监控')):
        clean_target = re.sub(r'^(刪除|取消|移除|删除|\-)\s*', '', user_text)
        res = remove_stock_from_radar(clean_target)
        reply_line_message(reply_token, res, target_id=target_dest)
        return

    # 一般即時個股查詢 (支援代碼或中文名稱，以旗艦視覺化 Flex 卡片呈現)
    from stock_analyzer import resolve_stock_input, get_stock_analysis_data
    from line_sender import build_stock_diagnosis_card, format_clean_stock_diagnosis_text
    stock_code, stock_name = resolve_stock_input(user_text)
    if stock_code:
        log_debug(f"[*] 正在分析標的 【{stock_code} {stock_name}】並建構視覺化卡片...")
        data = get_stock_analysis_data(user_text)
        if data.get("success"):
            flex_card = build_stock_diagnosis_card(data)
            clean_text = format_clean_stock_diagnosis_text(data)
            reply_line_message(reply_token, flex_card, target_id=target_dest, fallback_text=clean_text)
        else:
            err_msg = data.get("error", f"⚠️ 查無股票代碼 【{stock_code}】 之歷史交易數據。")
            reply_line_message(reply_token, err_msg, target_id=target_dest)
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
                        # 盤前自動拉取雲端昨夜最新選股結果
                        try:
                            import subprocess
                            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=str(Path(__file__).resolve().parent), timeout=15)
                            log_debug("[✓] 盤前自動同步雲端最新盤後報表成功")
                        except Exception as e:
                            log_debug(f"[!] 盤前同步雲端失敗: {e}")

                        from radar_manager import load_watchlist, get_radar_channel
                        w = load_watchlist()
                        target_ch = get_radar_channel()
                        if w:
                            m_card = f"""🔔【台股開盤前夕・盤中到價雷達全自動啟動】
━━━━━━━━━━━━━━━
今日共鎖定 {len(w)} 檔焦點標的！
雲端即時盯盤引擎已就緒，盤中將每 60 秒比對四大防線價位。
一旦觸及「🟢進場、🔵加碼、🔴停利、🛑停損」，將立即在此視窗為您即時推播！
祝福今日操盤順利，嚴守紀律！"""
                            try:
                                from line_sender import send_to_line
                                send_to_line(m_card, strategy="RADAR", target_id=target_ch)
                                log_debug(f"[✓] 成功發送開盤晨報至雷達與 Telegram 視窗")
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
                        c_card = f"""🏁【今日台股盤中到價雷達監控圓滿結束】
━━━━━━━━━━━━━━━
盤中交易已於 13:30 順利收盤！
今日到價事件推播完畢，雲端雷達轉入盤後待機模式。
💡 傍晚可輸入「同步選股」一鍵更新今日三大策略最新黑馬名單！"""
                        try:
                            from line_sender import send_to_line
                            send_to_line(c_card, strategy="RADAR", target_id=target_ch)
                            log_debug(f"[✓] 成功發送收盤總結至雷達與 Telegram 視窗")
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

    if os.environ.get("LOCAL_WEB_MODE") == "1":
        server_start_time = time.time()
        def lifecycle_watchdog():
            time.sleep(20)
            while True:
                time.sleep(1.0)
                with shutdown_lock:
                    now = time.time()
                    if not has_ever_connected and (now - server_start_time) > 90:
                        print("\n[系統提醒] 啟動後 90 秒內未偵測到任何瀏覽器開啟連線，已自動結束伺服器釋放資源。", flush=True)
                        os._exit(0)
                    if has_ever_connected and (now - last_heartbeat_time) > 8.0 and len(active_websockets) == 0:
                        try:
                            print("\n" + "=" * 70, flush=True)
                            print(" [STOP] 偵測到所有瀏覽器視窗/分頁已關閉！", flush=True)
                            print(" [STOP] 系統已自動停止 Web 伺服器運作，並完全釋放 8989 連線埠與電腦記憶體。", flush=True)
                            print("=" * 70 + "\n", flush=True)
                        except Exception:
                            pass
                        try:
                            from tunnel_manager import stop_tunnel
                            stop_tunnel()
                        except Exception:
                            pass
                        os._exit(0)
        threading.Thread(target=lifecycle_watchdog, daemon=True).start()

    asyncio.create_task(intraday_radar_worker())
    asyncio.create_task(render_keep_alive_worker())

# ==============================================================================
# 本機 Web 應用程式生命週期管理 (LOCAL_WEB_APP_LIFECYCLE_RULE_V1)
# 核心規範：單一圖示啟動、關閉瀏覽器即自動釋放通訊埠與記憶體資源
# ==============================================================================
LOCAL_AUTO_SHUTDOWN = os.environ.get("LOCAL_WEB_MODE") == "1"
active_websockets: Set[WebSocket] = set()
shutdown_timer: Optional[threading.Timer] = None
shutdown_lock = threading.RLock()
has_ever_connected = False
last_heartbeat_time: float = time.time()

def schedule_server_shutdown(delay: float = 4.0):
    """排程關閉伺服器。若在 delay 秒內無新連線/心跳，則終止 Python 行程並釋放資源"""
    global shutdown_timer
    if not LOCAL_AUTO_SHUTDOWN:
        return
    with shutdown_lock:
        if shutdown_timer and shutdown_timer.is_alive():
            shutdown_timer.cancel()
        
        def _terminate():
            with shutdown_lock:
                if len(active_websockets) == 0 and (time.time() - last_heartbeat_time) >= 3.5:
                    try:
                        print("\n" + "=" * 70, flush=True)
                        print(" [STOP] 偵測到所有台股戰情儀表板視窗/分頁已關閉！", flush=True)
                        print(" [STOP] 系統已自動觸發自我終止，完全釋放 8989 連線埠與電腦記憶體。", flush=True)
                        print("=" * 70 + "\n", flush=True)
                    except Exception:
                        pass
                    try:
                        from tunnel_manager import stop_tunnel
                        stop_tunnel()
                    except Exception:
                        pass
                    os._exit(0)
        
        shutdown_timer = threading.Timer(delay, _terminate)
        shutdown_timer.daemon = True
        shutdown_timer.start()

def cancel_server_shutdown():
    """取消關閉伺服器排程（當有頁面重整、切換或新連線建立時）"""
    global shutdown_timer
    with shutdown_lock:
        if shutdown_timer and shutdown_timer.is_alive():
            shutdown_timer.cancel()
            shutdown_timer = None

@app.websocket("/ws/session")
async def websocket_session(websocket: WebSocket):
    """WebSocket 即時心跳監控 (維持生命週期)"""
    global has_ever_connected, last_heartbeat_time
    await websocket.accept()
    with shutdown_lock:
        active_websockets.add(websocket)
        has_ever_connected = True
        last_heartbeat_time = time.time()
        cancel_server_shutdown()
    try:
        while True:
            msg = await websocket.receive_text()
            with shutdown_lock:
                last_heartbeat_time = time.time()
                cancel_server_shutdown()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        with shutdown_lock:
            active_websockets.discard(websocket)
            if len(active_websockets) == 0 and LOCAL_AUTO_SHUTDOWN:
                # 重新整理保護：內建 4 秒寬限期防 F5 中斷
                schedule_server_shutdown(delay=4.0)

@app.post("/api/heartbeat")
def receive_heartbeat():
    """接收前端定期心跳，維護伺服器運作狀態"""
    global last_heartbeat_time, has_ever_connected
    with shutdown_lock:
        last_heartbeat_time = time.time()
        has_ever_connected = True
        cancel_server_shutdown()
    return {"status": "ok", "time": last_heartbeat_time}

@app.post("/api/browser-leaving")
def browser_leaving():
    """當瀏覽器頁面觸發 beforeunload / pagehide 時送出之信標 (Navigator.sendBeacon)"""
    if LOCAL_AUTO_SHUTDOWN:
        with shutdown_lock:
            if len(active_websockets) <= 1:
                schedule_server_shutdown(delay=3.0)
    return {"status": "leaving_acknowledged"}

WEB_INDEX = Path(__file__).resolve().parent / "web" / "index.html"

@app.get("/api/system/network_info")
def get_network_info_endpoint():
    """提供本機、區域網路與外網 Cloudflare 專屬連線資訊"""
    import socket
    try:
        from tunnel_manager import get_current_tunnel_url
        tunnel_url = get_current_tunnel_url()
    except Exception:
        tunnel_url = ""

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "127.0.0.1"

    port = os.environ.get("PORT", "8989")
    return {
        "local_url": f"http://127.0.0.1:{port}",
        "lan_url": f"http://{lan_ip}:{port}",
        "tunnel_url": tunnel_url
    }

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """提供深色科技風「台股量化選股策略最高總指揮中心」戰情儀表板"""
    if WEB_INDEX.exists():
        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        }
        return FileResponse(WEB_INDEX, headers=headers)
    return HTMLResponse("<h1>台股量化選股策略最高總指揮中心</h1><p>請確認 web/index.html 是否存在。</p>")

# ==============================================================================
# 全域加權指數 (^TWII) 即時報價與市場位階動態計算引擎
# ==============================================================================
_TWII_CACHE = {}
_TWII_CACHE_EXPIRE = 0.0

def fetch_twii_market_data(force_refresh: bool = False) -> dict:
    global _TWII_CACHE, _TWII_CACHE_EXPIRE
    import time
    now_t = time.time()
    
    if not force_refresh and _TWII_CACHE and (now_t < _TWII_CACHE_EXPIRE):
        return _TWII_CACHE

    market_res = None
    try:
        url = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            msg_list = data.get('msgArray', [])
            if msg_list:
                msg = msg_list[0]
                z_str = msg.get('z', '')
                y_str = msg.get('y', '')
                curr_p = float(z_str) if z_str and z_str != '-' else (float(y_str) if y_str else 0.0)
                prev_p = float(y_str) if y_str and y_str != '-' else curr_p
                if curr_p > 0:
                    ch = round(curr_p - prev_p, 2)
                    ch_pct = round((ch / prev_p * 100), 2) if prev_p > 0 else 0.0
                    market_res = {
                        'close_raw': curr_p,
                        'close': f'{curr_p:,.2f}',
                        'prev_close': prev_p,
                        'change': ch,
                        'change_pct': ch_pct,
                        'date': msg.get('d', ''),
                        'time': msg.get('t', '')
                    }
    except Exception as e:
        print(f'[!] 證交所大盤 API 抓取失敗: {e}')

    ma20_val = 0.0
    ma60_val = 0.0
    try:
        import yfinance as yf
        import pandas as pd
        df = yf.download('^TWII', period='4mo', interval='1d', progress=False)
        if not df.empty and len(df) >= 20:
            c_series = df['Close']
            if isinstance(c_series, pd.DataFrame):
                c_series = c_series.iloc[:, 0]
            c_series = c_series.dropna()
            
            if not market_res or market_res.get('close_raw', 0) <= 0:
                last_p = float(c_series.iloc[-1])
                prev_p = float(c_series.iloc[-2]) if len(c_series) >= 2 else last_p
                ch = round(last_p - prev_p, 2)
                ch_pct = round((ch / prev_p * 100), 2) if prev_p > 0 else 0.0
                market_res = {
                    'close_raw': last_p,
                    'close': f'{last_p:,.2f}',
                    'prev_close': prev_p,
                    'change': ch,
                    'change_pct': ch_pct,
                    'date': str(c_series.index[-1])[:10],
                    'time': '13:30:00'
                }

            ma20_val = float(c_series.rolling(20).mean().iloc[-1])
            if len(c_series) >= 60:
                ma60_val = float(c_series.rolling(60).mean().iloc[-1])
            else:
                ma60_val = ma20_val
    except Exception as e:
        print(f'[!] yfinance 大盤歷史計算失敗: {e}')

    if not market_res or market_res.get('close_raw', 0) <= 0:
        cfg_file = ROOT_DIR / 'regime_config.json'
        if cfg_file.exists():
            try:
                with open(cfg_file, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    mr = cfg.get('market_regime', {})
                    p = float(mr.get('close', 46292.93))
                    market_res = {
                        'close_raw': p,
                        'close': f'{p:,.2f}',
                        'prev_close': p,
                        'change': 0.0,
                        'change_pct': 0.0,
                        'date': '',
                        'time': ''
                    }
                    ma20_val = float(mr.get('ma20', 48170.13))
                    ma60_val = float(mr.get('ma60', 46113.09))
            except Exception:
                pass

    if not market_res:
        market_res = {
            'close_raw': 46292.93,
            'close': '46,292.93',
            'prev_close': 46822.08,
            'change': -529.15,
            'change_pct': -1.13,
            'date': '',
            'time': ''
        }
        ma20_val = 48170.13
        ma60_val = 46113.09

    if ma20_val <= 0:
        ma20_val = 48170.13
    if ma60_val <= 0:
        ma60_val = 46113.09

    curr_p = market_res['close_raw']
    bias_ma20 = round((curr_p - ma20_val) / ma20_val * 100, 2)
    market_res['ma20'] = f'{ma20_val:,.2f}'
    market_res['ma60'] = f'{ma60_val:,.2f}'
    market_res['ma20_raw'] = ma20_val
    market_res['ma60_raw'] = ma60_val
    market_res['bias_ma20'] = bias_ma20

    if bias_ma20 > 5.0:
        market_res['regime'] = '🔴【多頭強攻・短線過熱警戒】'
        market_res['trend'] = '強勢過熱'
        market_res['risk_advice'] = '月線正乖離過大，嚴防短線主力獲利調節，嚴禁盤中追高，聚焦回測守穩標的。'
    elif bias_ma20 < -5.0 or curr_p < ma60_val:
        market_res['regime'] = '🟣【空頭回檔修正・防禦避險】'
        market_res['trend'] = '空頭防禦'
        market_res['risk_advice'] = '指數摜破均線防守，提高現金水位至 50% 以上，嚴格執行停損，策略 03 避險放空優先。'
    elif bias_ma20 >= 0.0 and curr_p >= ma20_val:
        market_res['regime'] = '🟢【多頭良性主升波段】'
        market_res['trend'] = '多頭排列'
        market_res['risk_advice'] = '均線呈多頭排列，可積極佈局策略 02 法人起漲股、策略 04 可轉債與策略 05 股期波段。'
    else:
        market_res['regime'] = '🟡【高檔震盪整理・箱型洗盤】'
        market_res['trend'] = '箱型震盪'
        market_res['risk_advice'] = '指數跌破月線但守穩季線，類股快速輪動。採高出低進策略，重視布林下軌超跌反彈（策略 01）與低基期標的。'

    try:
        cfg_file = ROOT_DIR / 'regime_config.json'
        if cfg_file.exists():
            with open(cfg_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if 'market_regime' in cfg:
                cfg['market_regime']['close'] = market_res['close_raw']
                cfg['market_regime']['bias_ma20'] = market_res['bias_ma20']
                cfg['market_regime']['ma20'] = market_res['ma20_raw']
                cfg['market_regime']['ma60'] = market_res['ma60_raw']
                cfg['market_regime']['label'] = market_res['regime']
                cfg['market_regime']['trend'] = market_res['trend']
                cfg['market_regime']['risk_advice'] = market_res['risk_advice']
                cfg['updated_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with open(cfg_file, 'w', encoding='utf-8') as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    _TWII_CACHE = market_res
    _TWII_CACHE_EXPIRE = now_t + 60.0
    return market_res

@app.get("/api/tomorrow_advisor")
async def get_tomorrow_advisor():
    """提供明日 (T+1) 量化買賣建議決策卡與一鍵委託資訊"""
    import json
    import pandas as pd
    advisor_csv = Path(__file__).resolve().parent / "lab" / "data" / "tomorrow_trading_advisor.csv"
    if not advisor_csv.exists():
        return {"status": "empty", "items": [], "market_regime": {}}
    
    try:
        df = pd.read_csv(advisor_csv, encoding='utf-8-sig', dtype=str).fillna("")
        items = []
        for _, row in df.iterrows():
            sym = str(row.get("symbol", "")).strip()
            name = str(row.get("name", "")).strip()
            act = str(row.get("tomorrow_action", "")).strip()
            entry_p = str(row.get("entry_price", "")).strip()
            tp1_p = str(row.get("tp1_price", "")).strip()
            tp2_p = str(row.get("tp2_price", "")).strip()
            sl_p = str(row.get("stop_loss_price", "")).strip()
            addon_p = str(row.get("addon_price", "")).strip()
            shares = str(row.get("recommended_shares", "1000")).strip()
            
            # 格式化一鍵下單委託語句
            is_buy = "買進" in act or "BUY" in act
            order_verb = "限價買進" if is_buy else "限價放空/賣出"
            order_text = f"{name}({sym}) {order_verb} ${entry_p} 建議 {shares}股 (TP1: ${tp1_p} / 停損: ${sl_p})"

            items.append({
                "symbol": sym,
                "name": name,
                "tomorrow_action": act,
                "source_strategy": str(row.get("source_strategy", "")),
                "verdict": str(row.get("verdict", "")),
                "close_price": str(row.get("close_price", "")),
                "entry_price": entry_p,
                "addon_price": addon_p,
                "tp1_price": tp1_p,
                "tp2_price": tp2_p,
                "stop_loss_price": sl_p,
                "rr_ratio": str(row.get("rr_ratio", "")),
                "expectancy_pct": str(row.get("expectancy_pct", "")),
                "recommended_shares": shares,
                "hist_return_pct": str(row.get("hist_return_pct", "")),
                "hist_win_rate_pct": str(row.get("hist_win_rate_pct", "")),
                "hist_sharpe": str(row.get("hist_sharpe", "")),
                "overnight_risk": str(row.get("overnight_risk", "")),
                "action_plan_zh": str(row.get("action_plan_zh", "")),
                "order_snippet": order_text,
            })
        
        regime_info = {}
        regime_file = Path(__file__).resolve().parent / "regime_config.json"
        if regime_file.exists():
            try:
                with open(regime_file, "r", encoding="utf-8") as f:
                    regime_info = json.load(f)
            except Exception:
                pass
        
        return {
            "status": "success",
            "items": items,
            "total": len(items),
            "market_regime": regime_info
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "items": []}


@app.get("/api/market_pulse/score")
async def get_market_pulse_score(refresh: bool = False):
    """提供 FinLab 風格台股大盤 0~10 分多空計分板與走勢圖資料"""
    try:
        from market_pulse import GLOBAL_MARKET_PULSE
        return GLOBAL_MARKET_PULSE.get_market_pulse_data(force_refresh=refresh)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/market_pulse/treemap")
async def get_market_pulse_treemap(metric: str = "market_cap", color_mode: str = "tw"):
    """提供全市場產業板塊熱力樹狀圖 (Treemap) 與報酬率分佈長條圖資料"""
    try:
        from market_pulse import GLOBAL_MARKET_PULSE
        return GLOBAL_MARKET_PULSE.get_treemap_data(metric=metric, color_mode=color_mode)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/market_pulse/refresh")
async def refresh_market_pulse():
    """手動強制刷新大盤指標與產業板塊資料"""
    try:
        from market_pulse import GLOBAL_MARKET_PULSE
        data = GLOBAL_MARKET_PULSE.get_market_pulse_data(force_refresh=True)
        return {"status": "ok", "score": data.get("score"), "timestamp": data.get("timestamp")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/overview")
async def get_overview():
    """提供大盤現況與全策略入選總數概覽"""
    import pandas as pd
    report_file = Path(__file__).resolve().parent / "weekly_report.md"
    market_data = fetch_twii_market_data()

    # 計算五大策略總入選數
    total_candidates = 0
    strat_dirs = [
        "01_bollinger_reversal",
        "02_institutional_momentum",
        "03_short_momentum",
        "04_cb_pricing_ambush",
        "05_stock_futures_long",
        "06_volume_surge",
        "07_margin_call_ambush",
        "08_group_window_dressing",
        "09_treasury_stock_buyback",
        "10_investment_trust_sprint"
    ]
    for s in strat_dirs:
        csv_file = Path(__file__).resolve().parent / "strategies" / s / "result.csv"
        if csv_file.exists():
            try:
                df = pd.read_csv(csv_file, encoding='utf-8-sig')
                total_candidates += len(df)
            except Exception:
                pass

    from radar_manager import load_watchlist
    watchlist = load_watchlist()
    radar_count = len(watchlist)

    return {
        "status": "online",
        "market": market_data,
        "total_candidates": total_candidates,
        "radar_count": radar_count,
        "strategies": strat_dirs
    }

@app.get("/api/strategy/{strat_id}")
async def get_strategy_data(strat_id: str):
    """取得指定策略最新選股表格數據"""
    import pandas as pd
    strat_meta = {
        "01_bollinger_reversal": {
            "name": "策略 01：布林通道下軌超跌反轉策略",
            "desc": "鎖定觸及布林下軌、融資洗清、三大法人逆勢大額買超之高勝率反轉強撐股。"
        },
        "02_institutional_momentum": {
            "name": "策略 02：法人籌碼集中起漲動能策略",
            "desc": "鎖定外資與投信連續重金吃貨、站穩月線、籌碼集中度大增之起漲黑馬股。"
        },
        "03_short_momentum": {
            "name": "策略 03：弱勢破線做空與個股期貨避險策略",
            "desc": "鎖定均線空頭排列、外資法人瘋狂倒貨、摜破關鍵支撐之弱勢避險放空標的。"
        },
        "04_cb_pricing_ambush": {
            "name": "策略 04：可轉債 (CB) 定價伏擊與區間博弈策略",
            "desc": "捕捉可轉債定價日前後主力壓盤洗盤甜蜜點，伏擊右側拉抬紅利，嚴守3個月閉鎖解禁前清倉死線。"
        },
        "05_stock_futures_long": {
            "name": "策略 05：台股個股期貨法人強勢波段多方策略",
            "desc": "鎖定期交所 252 檔活躍個股期、外資投信重金吃貨、剛脫離月線起漲之強勢波段股，優先呈現期貨報價與未平倉量 (OI) 深度評估。"
        },
        "06_volume_surge": {
            "name": "策略 06：盤中成交量異常爆量起漲動能策略",
            "desc": "捕捉主力資金強勢點火、動態量能布林突破 (+3σ)、且符合實體紅K高勝率型態之起漲飆股，階梯式防洗版到價告警。"
        },
        "07_margin_call_ambush": {
            "name": "策略 07：高融資使用率×低維持率斷頭伏擊與浮額清洗策略",
            "desc": "鎖定高融資使用率與逼近 130% 追繳斷頭線之個股，透過「融資增減 × 法人動向」雙向狀態機自動分流【🔴斷頭助跌・放空獵殺】與【🟢浮額清洗・逆勢抄底】。"
        },
        "08_group_window_dressing": {
            "name": "策略 08：台股集團作帳與母子聯動伏擊策略",
            "desc": "鎖定 25 大核心集團季底與年底作帳行情，透過「母子聯動 × 集團共振」雙向狀態機自動分流【🟢作帳點火・母子共振】與【🔴集團結帳・順勢放空】。"
        },
        "09_treasury_stock_buyback": {
            "name": "策略 09：庫藏股宣告護盤與低估值事件策略",
            "desc": "鎖定 MOPS 法定庫藏股申報、過往 3 年執行率誠信檢核、4+1 維度低估值護盤與法人防對倒共振，三層動態降級容錯。"
        },
        "10_investment_trust_sprint": {
            "name": "策略 10：投信認養集中度爆發飆股策略",
            "desc": "鎖定投信連續重金吃貨認養、籌碼高度集中鎖碼、均線多頭起漲且嚴守乖離防追高之中小型爆發飆股。"
        }
    }

    meta = strat_meta.get(strat_id, {"name": strat_id, "desc": ""})
    csv_file = Path(__file__).resolve().parent / "strategies" / strat_id / "result.csv"
    if not csv_file.exists():
        return {
            "name": meta["name"],
            "desc": meta["desc"],
            "columns": [],
            "rows": []
        }

    try:
        df = pd.read_csv(csv_file, encoding='utf-8-sig')
        df = df.fillna('')
        cols = list(df.columns)
        rows = df.to_dict(orient='records')

        # 批次查詢即時行情並計算與進場價之差異
        from radar_manager import fetch_batch_realtime_quotes
        codes = []
        for r in rows:
            c = str(r.get('證券代號') or r.get('股票代碼') or r.get('代號') or r.get('代碼') or r.get('code') or '').strip()
            if c:
                codes.append(c)
        quotes = fetch_batch_realtime_quotes(codes) if codes else {}
        for r in rows:
            c = str(r.get('證券代號') or r.get('股票代碼') or r.get('代號') or r.get('代碼') or r.get('code') or '').strip()
            q = quotes.get(c, {})
            curr_p = q.get('price')
            if curr_p is not None:
                r['realtime_price'] = curr_p
                r['prev_close'] = q.get('prev_close', curr_p)
                r['realtime_change'] = q.get('change', 0.0)
                r['realtime_change_pct'] = q.get('change_pct', 0.0)
                r['quote_time'] = q.get('time', '')

            # 建議進場價評估
            entry_raw = str(r.get('進場價位') or r.get('進場買進上限') or r.get('建議進場價') or r.get('多單進場') or r.get('空單進場') or r.get('現貨進場') or '').replace(',', '').strip()
            try:
                ep = float(entry_raw)
            except Exception:
                ep = 0.0

            eval_p = curr_p if curr_p is not None else 0.0
            if not eval_p:
                try:
                    eval_p = float(str(r.get('收盤價') or r.get('最新現價') or r.get('現價') or r.get('期貨收盤價') or 0).replace(',', ''))
                except Exception:
                    eval_p = 0.0

            if ep > 0 and eval_p > 0:
                diff = round(eval_p - ep, 2)
                diff_pct = round((diff / ep) * 100, 2)
                r['diff_from_entry'] = diff
                r['diff_pct_from_entry'] = diff_pct
                if diff <= 0:
                    r['position_status'] = 'discount'
                    r['position_badge'] = '🟢 甜蜜進場區'
                    r['position_desc'] = f'折價 {abs(diff):.2f} 元 ({diff_pct:.2f}%)'
                else:
                    r['position_status'] = 'premium'
                    r['position_badge'] = '⚠️ 高於進場價'
                    r['position_desc'] = f'溢價 +{diff:.2f} 元 (+{diff_pct:.2f}%)'

            # 注入多階段梯隊操盤歷程與生命週期欄位 (向後相容補全)
            ent1 = r.get('第一次進場價位') or ep
            tp1_val = r.get('第一次停利價位') or r.get('停利TP1') or r.get('目標停利TP1') or r.get('現貨TP1') or ''
            ent2 = r.get('第二次進場價位') or r.get('動能加碼價') or r.get('加碼價位') or r.get('續強加碼價') or ''
            tp2_val = r.get('第二次停利價位') or r.get('停利TP2') or r.get('目標停利TP2') or r.get('現貨TP2') or ''
            stg = r.get('操盤階段') or ('第 2 階段 (續強加碼)' if '續強' in str(r.get('狀態標註', '')) else '第 1 階段 (初次建倉)')
            lifecycle = r.get('生命週期狀態') or ('PENDING_REMOVAL' if '待剔除' in str(r.get('狀態標註', '')) else 'ACTIVE')

            r['first_entry_price'] = ent1
            r['first_tp_price'] = tp1_val
            r['second_entry_price'] = ent2
            r['second_tp_price'] = tp2_val
            r['stage'] = stg
            r['lifecycle'] = lifecycle
            r['removal_reason'] = r.get('剔除原因', '')
            r['removal_trigger_date'] = r.get('剔除觸發日期', '')
            r['return_pct'] = r.get('操作損益率%', '')
            r['pnl_status'] = r.get('損益狀態', '')

        # 解析戰報交易日期與 5 日滾動統計
        date_str = ""
        if "選股日期" in df.columns and not df["選股日期"].dropna().empty:
            dates = sorted([str(d).strip() for d in df["選股日期"].dropna().unique() if str(d).strip()], reverse=True)
            if dates:
                date_str = dates[0]

        if not date_str:
            md_file = csv_file.parent / "result.md"
            if md_file.exists():
                try:
                    with open(md_file, "r", encoding="utf-8") as mf:
                        for line in mf:
                            if "交易日期" in line or "產出時間" in line:
                                import re
                                m = re.search(r'[`\s](\d{4}[-/]?\d{2}[-/]?\d{2})', line)
                                if m:
                                    date_str = m.group(1)
                                break
                except Exception:
                    pass
        if not date_str:
            import datetime
            mtime = datetime.datetime.fromtimestamp(csv_file.stat().st_mtime)
            date_str = mtime.strftime('%Y-%m-%d')

        today_rows = [r for r in rows if str(r.get('選股日期', '')).strip() == date_str or 'T+0' in str(r.get('入選天數', '')) or '今日' in str(r.get('狀態標註', ''))]
        pending_rows = [r for r in rows if str(r.get('lifecycle')) == 'PENDING_REMOVAL' or '待剔除' in str(r.get('狀態標註', ''))]
        today_count = len(today_rows)
        pending_count = len(pending_rows)
        tracking_count = len(rows) - today_count

        return {
            "name": meta["name"],
            "desc": meta["desc"],
            "date": date_str,
            "total_count": len(rows),
            "today_count": today_count,
            "tracking_count": tracking_count,
            "pending_removal_count": pending_count,
            "columns": cols,
            "rows": rows
        }
    except Exception as e:
        return {
            "name": meta["name"],
            "desc": meta["desc"],
            "error": str(e),
            "columns": [],
            "rows": []
        }

@app.get("/api/trade_history")

async def get_trade_history_api(strategy: Optional[str] = None):

    """取得歷史已結案操盤戰績與 KPI 統計總覽"""

    try:

        from trade_history_manager import GLOBAL_TRADE_HISTORY

        return GLOBAL_TRADE_HISTORY.get_performance_summary(strategy)

    except Exception as e:

        return {

            "error": str(e),

            "total_trades": 0,

            "winning_trades": 0,

            "losing_trades": 0,

            "flat_trades": 0,

            "win_rate_pct": 0.0,

            "avg_return_pct": 0.0,

            "total_return_pct": 0.0,

            "max_win_pct": 0.0,

            "max_loss_pct": 0.0,

            "avg_holding_days": 0.0,

            "total_pnl_amount": 0.0,

            "trades": []

        }



@app.get("/api/weekly_report")
async def get_weekly_report():
    """取得每週總體戰略週報內容"""
    report_file = Path(__file__).resolve().parent / "weekly_report.md"
    if report_file.exists():
        try:
            with open(report_file, "r", encoding="utf-8") as f:
                return {"content": f.read()}
        except Exception as e:
            return {"content": f"讀取報告失敗: {e}"}
    return {"content": "目前尚無每週戰報，請執行週日優化作業。"}

@app.post("/api/analyze")
async def analyze_endpoint(request: Request):
    """個股即時操盤指引診斷 API (同時回傳結構化卡片數據 data 與純文字 result)"""
    body = await request.json()
    query = body.get("query", "").strip()
    if not query:
        return {"success": False, "result": "請輸入有效之台股代碼或股票名稱。"}
    from stock_analyzer import get_stock_analysis_data, analyze_stock
    data = get_stock_analysis_data(query)
    if data.get("success"):
        return {
            "success": True,
            "data": data,
            "result": analyze_stock(query)
        }
    else:
        err = data.get("error", "分析失敗，查無資料。")
        return {
            "success": False,
            "error": err,
            "result": err
        }


@app.post("/api/radar/push_stock_to_line")
async def push_stock_to_line_endpoint(request: Request):
    """將指定個股之診斷字卡推播至 LINE 視窗"""
    body = await request.json()
    query = body.get("code") or body.get("query", "")
    if not query:
        return {"success": False, "message": "請指定股票代碼或名稱"}
    from stock_analyzer import get_stock_analysis_data
    from line_sender import build_stock_diagnosis_card, format_clean_stock_diagnosis_text, send_to_line
    from radar_manager import get_radar_channel

    data = get_stock_analysis_data(query)
    if not data.get("success"):
        return {"success": False, "message": data.get("error", "分析失敗")}

    card = build_stock_diagnosis_card(data)
    target = get_radar_channel() or os.environ.get("LINE_USER_ID", "")
    success = send_to_line(card, target_id=target)
    if not success:
        clean_text = format_clean_stock_diagnosis_text(data)
        success = send_to_line(clean_text, target_id=target)

    return {"success": success, "message": f"已成功推播【{data['code']} {data['name']}】診斷字卡至 LINE 視窗！" if success else "推播失敗，請檢查 LINE Token。"}


@app.get("/api/radar/watchlist")
async def get_radar_watchlist_endpoint():
    """取得盤中雷達即時自選監控清單 (含即時價格與進場價差比對)"""
    from radar_manager import load_watchlist, fetch_batch_realtime_quotes
    watchlist = load_watchlist()
    items = list(watchlist.values())

    # 批次向證交所 API 查詢最新即時行情
    codes = [it.get('code') for it in items if it.get('code')]
    quotes = fetch_batch_realtime_quotes(codes) if codes else {}

    for it in items:
        code = it.get('code', '')
        q = quotes.get(code, {})
        curr_p = q.get('price')
        prev_p = q.get('prev_close')

        # 建議進場價 (優先取 entry_price, entry_high, base_price)
        ep = float(it.get('entry_price') or it.get('entry_high') or it.get('base_price') or 0)
        it['entry_price_used'] = ep

        if curr_p is not None:
            it['realtime_price'] = curr_p
            it['prev_close'] = prev_p or curr_p
            it['realtime_change'] = q.get('change', 0.0)
            it['realtime_change_pct'] = q.get('change_pct', 0.0)
            it['quote_time'] = q.get('time', '')
        else:
            bp = float(it.get('base_price') or 0)
            it['realtime_price'] = bp
            it['prev_close'] = bp
            it['realtime_change'] = 0.0
            it['realtime_change_pct'] = 0.0
            it['quote_time'] = ''

        # 計算與建議進場價之差異
        eval_p = it['realtime_price']
        if ep > 0 and eval_p > 0:
            diff = round(eval_p - ep, 2)
            diff_pct = round((diff / ep) * 100, 2)
            it['diff_from_entry'] = diff
            it['diff_pct_from_entry'] = diff_pct

            sl_p = float(it.get('sl_price') or 0)
            ad_p = float(it.get('addon_price') or 0)
            tp1_p = float(it.get('tp1_price') or 0)

            if sl_p > 0 and eval_p <= sl_p:
                it['position_status'] = 'stop_loss'
                it['position_badge'] = '🛑 觸及停損防線'
                it['position_desc'] = f'現價已跌破停損線 {sl_p:.2f} 元'
            elif tp1_p > 0 and eval_p >= tp1_p:
                it['position_status'] = 'take_profit'
                it['position_badge'] = '🔴 攻抵目標停利'
                it['position_desc'] = f'現價已達 TP1 目標 {tp1_p:.2f} 元'
            elif ad_p > 0 and eval_p >= ad_p:
                it['position_status'] = 'addon'
                it['position_badge'] = '🔵 突破動能加碼'
                it['position_desc'] = f'現價高於進場 +{diff:.2f} 元 (+{diff_pct:.2f}%)，突破加碼'
            elif diff <= 0:
                it['position_status'] = 'discount'
                it['position_badge'] = '🟢 甜蜜進場區'
                it['position_desc'] = f'現價折價 {abs(diff):.2f} 元 ({diff_pct:.2f}%)，低於買進上限'
            else:
                it['position_status'] = 'premium'
                it['position_badge'] = '⚠️ 高於進場上限'
                it['position_desc'] = f'現價溢價 +{diff:.2f} 元 (+{diff_pct:.2f}%)，高於建議進場'
        else:
            it['diff_from_entry'] = 0.0
            it['diff_pct_from_entry'] = 0.0
            it['position_status'] = 'normal'
            it['position_badge'] = '⚪ 觀望評估'
            it['position_desc'] = '無進場價數據'

        # 計算掛入至今之交易日數與進場狀態
        from radar_manager import count_trading_days, TZ_TW
        now_tw = datetime.datetime.now(TZ_TW)
        today = now_tw.date()
        added_str = it.get('added_at', '')
        if added_str:
            try:
                added_d = datetime.datetime.strptime(added_str[:10], '%Y-%m-%d').date()
                it['trading_days_waiting'] = count_trading_days(added_d, today)
            except Exception:
                it['trading_days_waiting'] = 0
        else:
            it['trading_days_waiting'] = 0

        # 判定是否已曾進場
        has_entered = it.get('has_entered', False)
        if not has_entered and ep > 0 and eval_p <= ep:
            has_entered = True
            it['has_entered'] = True
        if not has_entered and it.get('alerted_today'):
            for evs in it['alerted_today'].values():
                if any(x in ('ENTRY', 'ADDON', 'TP1', 'TP2') for x in evs):
                    has_entered = True
                    it['has_entered'] = True
                    break
        it['has_entered'] = has_entered

        # 判定當日是否觸及到價或發送推播 (triggered_today)
        today_str = today.strftime('%Y-%m-%d')
        today_events = []
        is_triggered_today = False

        if 'alerted_today' in it and isinstance(it['alerted_today'], dict):
            today_events.extend(it['alerted_today'].get(today_str, []))

        last_push = str(it.get('last_push_at', ''))
        if last_push.startswith(today_str):
            is_triggered_today = True
            pe = it.get('last_push_event', '')
            if pe and pe not in today_events:
                today_events.append(pe)

        if today_events:
            is_triggered_today = True

        it['triggered_today'] = is_triggered_today
        it['today_events'] = list(dict.fromkeys(today_events))
        it['today_push_time'] = last_push[11:19] if last_push.startswith(today_str) else ''
        it['is_new_today'] = bool(added_str and added_str.startswith(today_str))

    # 依最近推送時間（無推送紀錄則採掛入時間）由近至遠排序
    items.sort(
        key=lambda x: max(str(x.get('last_push_at', '')), str(x.get('added_at', ''))),
        reverse=True
    )
    today_triggered_items = [it for it in items if it.get('triggered_today')]
    from radar_manager import sanitize_floats
    return sanitize_floats({
        "status": "ok",
        "count": len(items),
        "today_triggered_count": len(today_triggered_items),
        "today_triggered_items": today_triggered_items,
        "items": items
    })

@app.get("/api/radar/triggered_today")
async def get_radar_triggered_today_endpoint():
    """專門回傳今日觸及到價與已推播之個股清單"""
    res = await get_radar_watchlist_endpoint()
    return {
        "status": "ok",
        "count": res.get("today_triggered_count", 0),
        "items": res.get("today_triggered_items", [])
    }

@app.post("/api/radar/add")
async def add_to_radar_endpoint(request: Request):
    """手動將標的加入盤中到價雷達 (支援代碼、多檔批次、中文股名、題材概念股)"""
    from radar_manager import batch_add_to_radar, load_watchlist
    body = await request.json()
    query = body.get("query", "").strip()
    if not query:
        return {"success": False, "message": "請輸入有效之股票代碼、中文名稱或概念題材。"}
    res = batch_add_to_radar(query)
    watchlist = load_watchlist()
    return {
        "success": True,
        "message": res,
        "count": len(watchlist)
    }

@app.post("/api/radar/remove")
async def remove_from_radar_endpoint(request: Request):
    """自盤中到價雷達移除指定標的"""
    from radar_manager import remove_stock_from_radar, load_watchlist
    body = await request.json()
    code = body.get("code", "").strip()
    if not code:
        return {"success": False, "message": "請指定要移除之股票代碼或名稱。"}
    res = remove_stock_from_radar(code)
    watchlist = load_watchlist()
    return {
        "success": True,
        "message": res,
        "count": len(watchlist)
    }

@app.post("/api/radar/clear")
async def clear_radar_endpoint():
    """清空盤中雷達所有監控標的"""
    from radar_manager import clear_all_radar
    res = clear_all_radar()
    return {"success": True, "message": res, "count": 0}

@app.post("/api/radar/reset_alerts")
async def reset_alerts_endpoint():
    """重置今日到價警報狀態"""
    from radar_manager import reset_today_alerts
    res = reset_today_alerts()
    return {"success": True, "message": res}

@app.post("/api/radar/sync")
async def sync_radar_endpoint():
    """連動四大選股日報精選黑馬標的進雷達"""
    from radar_manager import sync_screener_to_radar, load_watchlist
    res = sync_screener_to_radar()
    watchlist = load_watchlist()
    return {"success": True, "message": res, "count": len(watchlist)}

@app.post("/api/radar/prune_unentered")
async def prune_unentered_endpoint():
    """手動或排程釋放超過 5 個交易日未觸及進場價之監控席位"""
    from radar_manager import prune_unentered_radar_stocks, load_watchlist
    pruned = prune_unentered_radar_stocks(max_trading_days=5, verbose=False)
    wl = load_watchlist()
    return {
        "success": True,
        "pruned_count": len(pruned),
        "pruned_items": pruned,
        "remaining_count": len(wl),
        "message": f"已自動釋放 {len(pruned)} 檔超過 5 個交易日未觸及進場價之標的。" if pruned else "目前無超過 5 個交易日未進場之逾期標的。"
    }

@app.post("/api/radar/rollover")
async def rollover_radar_endpoint():
    """執行雷達監控池跨日動態滾動校準與自動移動停利 (含自動釋放超過 5 日未進場席位)"""
    from radar_manager import rollover_watchlist_defense, load_watchlist
    res = rollover_watchlist_defense(verbose=False)
    watchlist = load_watchlist()
    return {
        "success": True,
        "result": res,
        "pruned_count": res.get("pruned", 0),
        "pruned_items": res.get("pruned_items", []),
        "message": f"動態校準完成！共更新 {res['updated']}/{res['total']} 檔，移動停利墊高 {res['trailed']} 檔" + (f"，已自動釋放 {res['pruned']} 檔逾 5 日未進場標的。" if res.get('pruned') else "。"),
        "count": len(watchlist)
    }



@app.post("/api/action/{action_name}")
async def trigger_action_endpoint(action_name: str, background_tasks: BackgroundTasks):
    """觸發本機後台任務"""
    import subprocess
    root_dir = Path(__file__).resolve().parent
    if action_name == "run_screener":
        def _task():
            subprocess.run([sys.executable, str(root_dir / "run_all.py")])
        background_tasks.add_task(_task)
        return {"message": "已在背景啟動全策略盤後選股 (run_all.py)！"}

    elif action_name == "run_radar":
        def _task():
            subprocess.run([sys.executable, str(root_dir / "run_all_intraday.py"), "--once"])
        background_tasks.add_task(_task)
        return {"message": "已在背景啟動全策略盤中到價掃描 (run_all_intraday.py --once)！"}

    elif action_name == "run_optimizer":
        def _task():
            subprocess.run([sys.executable, str(root_dir / "optimize_all.py"), "--line"])
        background_tasks.add_task(_task)
        return {"message": "已在背景啟動每週日動態優化 (optimize_all.py --line)！"}

    elif action_name == "sync_cloud":
        def _task():
            try:
                subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=str(root_dir), timeout=25)
            except Exception as e:
                log_debug(f"[!] 同步雲端失敗: {e}")
        background_tasks.add_task(_task)
        return {"message": "已在背景啟動同步雲端最新選股成果！"}

    elif action_name == "test_line":
        def _task():
            from line_sender import send_to_line
            send_to_line("🚀【總指揮中心測試】Web 戰情儀表板連線正常！", strategy=None)
        background_tasks.add_task(_task)
        return {"message": "已發送 LINE 測試訊息至總指揮窗口！"}

    return {"message": f"未知的動作: {action_name}"}

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
    port = int(os.environ.get("PORT", 8989))
    print(f"=====================================================")
    print(f"  台股即時隨傳隨回 LINE 操盤機器人伺服器啟動中...")
    print(f"  本機端點: http://127.0.0.1:{port}/callback")
    print(f"  LINE Token 設定狀態: {'已就緒' if CHANNEL_ACCESS_TOKEN else '未設定'}")
    print(f"=====================================================")
    uvicorn.run("line_bot_server:app", host="127.0.0.1", port=port, reload=False)

