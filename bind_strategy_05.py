#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 05 (個股期貨波段多方) LINE 專屬群組自動綁定小幫手
功能：
1. 輪詢雲端 Webhook 接收站 (無需本機開穿透或手動設定)。
2. 當使用者在手機 LINE 建立「策略 05 專屬群組」並邀請機器人【台股雷達二號機 (@761awmcp)】後。
3. 只要在該群組內發送任意訊息（例如「05」或「多方」）。
4. 本程式將即時擷取該群組之 Group ID，自動寫入 .env 之 LINE_TARGET_STRATEGY_05。
5. 自動向群組發送高質感綁定成功歡迎卡與四大防線測試戰報！
"""

import os
import sys
import time
import json
import urllib.request
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

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"
WEBHOOK_UUID = "0a6e22ea-712c-4fdd-87f1-906f935622fb"

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

def save_env_var(key, value):
    lines = []
    found = False
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}=") or line.strip().startswith(f"#{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"  [✓] 已成功將 {key}={value} 儲存至 .env！")

def get_group_summary(token, group_id):
    """向 LINE API 查詢群組名稱"""
    url = f"https://api.line.me/v2/bot/group/{group_id}/summary"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get('groupName', '未知群組名稱')
    except Exception:
        return "台股個股期波段多方戰情室"

def send_group_welcome(token, group_id, group_name):
    """向該群組發送綁定成功訊息與四大防線卡片"""
    url = "https://api.line.me/v2/bot/message/push"
    msg = (
        f"🎯【台股量化選股策略】獨立視窗綁定成功！\n"
        f"─────────────────────\n"
        f"✅ 本群組【{group_name}】已正式設定為：\n"
        f"🔥【策略 05：個股期貨法人強勢波段多方】專屬獨立戰情報導室\n"
        f"📌 群組代號：{group_id[:8]}...{group_id[-4:]}\n\n"
        f"⚡ 交易標準：股票期貨價位優先報價（一口表彰2張現貨、保證金約13.5%、期交稅0.002%）\n"
        f"📡 每日 17:35 自動推播盤後強勢多方輪播日報\n"
        f"🚨 盤中 08:58~13:35 即時偵測四大防線（🟢進場、🔵加碼、🔴TP1/TP2、🛑停損）並秒級推播！"
    )
    payload = {
        "to": group_id,
        "messages": [{"type": "text", "text": msg}]
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"  [!] 發送歡迎詞失敗: {e}")
        return False

def listen_for_strategy_05(timeout_seconds=300):
    env = load_env()
    token = env.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("[X] 錯誤：找不到 LINE_CHANNEL_ACCESS_TOKEN！")
        return False

    existing_gids = {
        env.get("LINE_TARGET_STRATEGY_01"),
        env.get("LINE_TARGET_STRATEGY_02"),
        env.get("LINE_TARGET_STRATEGY_03"),
        env.get("LINE_TARGET_STRATEGY_04"),
    } - {None}

    print("=" * 70)
    print(" 🎯 策略 05 (個股期貨波段多方) - LINE 專屬群組自動綁定監聽中...")
    print("=" * 70)
    print("【操作指引】：")
    print(" 1. 打開手機 LINE，建立一個新群組（建議命名為：【台股】05-股期多方）。")
    print(" 2. 將機器人【台股雷達二號機 (@761awmcp)】邀請加入此群組。")
    print(" 3. 在群組內隨便傳送任意訊息（例如打「05」或「多方」）。")
    print(" 4. 本程式將即時捕捉訊號、綁定群組並發送驗證戰報！")
    print("=" * 70)
    print(f"[*] 監聽已啟動 (最長等待 {timeout_seconds} 秒)，請在 LINE 群組發送訊息...")

    seen_req_ids = set()
    # 先讀取現有請求 ID，避免吃到舊紀錄
    try:
        url_init = f"https://webhook.site/token/{WEBHOOK_UUID}/requests"
        req_init = urllib.request.Request(url_init, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req_init, timeout=5) as resp:
            d = json.loads(resp.read().decode('utf-8'))
            for item in d.get('data', []):
                seen_req_ids.add(item.get('uuid'))
    except Exception:
        pass

    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        try:
            url = f"https://webhook.site/token/{WEBHOOK_UUID}/requests"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                res_data = json.loads(resp.read().decode('utf-8'))
                requests_list = res_data.get('data', [])

            for item in reversed(requests_list):
                req_id = item.get('uuid')
                if req_id in seen_req_ids:
                    continue
                seen_req_ids.add(req_id)

                content_str = item.get('content')
                if not content_str:
                    continue

                try:
                    body = json.loads(content_str)
                    for ev in body.get('events', []):
                        src = ev.get('source', {})
                        if src.get('type') == 'group':
                            gid = src.get('groupId')
                            if not gid or gid in existing_gids:
                                continue

                            msg_text = ""
                            if ev.get('type') == 'message':
                                msg_text = ev.get('message', {}).get('text', '')

                            g_name = get_group_summary(token, gid)
                            print("\n" + "★" * 65)
                            print(f"🎉 成功偵測到新群組！")
                            print(f"📌 名稱：【{g_name}】")
                            print(f"📌 Group ID：{gid}")
                            if msg_text:
                                print(f"💬 收到訊息：'{msg_text}'")
                            print("★" * 65)

                            save_env_var("LINE_TARGET_STRATEGY_05", gid)
                            print(f"🚀 正在向群組發送綁定驗證戰報...")
                            send_group_welcome(token, gid, g_name)
                            
                            # 觸發實戰四大防線測試戰報
                            try:
                                import subprocess
                                print(f"📡 正在發送【策略 05：股期波段多方】四大防線實戰卡片至新視窗...")
                                subprocess.run([sys.executable, str(ROOT_DIR / "strategies" / "05_stock_futures_long" / "intraday_scanner.py"), "--test-push"], check=False)
                            except Exception as e:
                                print(f"  [!] 觸發測試卡片失敗: {e}")

                            print(f"\n🎊【策略 05 專屬視窗】綁定與驗證全部完成！")
                            return True

                except Exception:
                    pass

            time.sleep(2)
        except Exception:
            time.sleep(2)

    print(f"\n[!] 等待逾時 ({timeout_seconds} 秒)，尚未收到新群組訊息。")
    return False

if __name__ == '__main__':
    listen_for_strategy_05(timeout_seconds=1800)

