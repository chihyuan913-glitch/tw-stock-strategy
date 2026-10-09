#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE 官方機器人多群組自動綁定小幫手 (Zero-Setup Auto Binder)
使用雲端 Webhook 橋接技術，免安裝 ngrok、免手動設定連接埠！

運作流程：
1. 本程式已自動將您的 LINE Bot Webhook 連線至雲端接收站。
2. 您只需在 LINE App 建立 3 個群組並邀請機器人「選股日報小幫手」(@899zzwux)。
3. 在群組內發送任意訊息（例如「01」、「02」、「03」）。
4. 本程式將即時捕捉 Group ID、獲取群組名稱、自動歸納並寫入 .env！
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
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"  [✓] 已成功將 {key}={value} 寫入 .env")

def get_group_summary(token, group_id):
    """向 LINE API 查詢群組名稱"""
    url = f"https://api.line.me/v2/bot/group/{group_id}/summary"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get('groupName', '未知群組名稱')
    except Exception:
        return None

def send_group_welcome(token, group_id, strat_name):
    """向該群組發送綁定成功訊息"""
    url = "https://api.line.me/v2/bot/message/push"
    msg = (
        f"🎯【台股量化選股策略】獨立視窗連線成功！\n"
        f"─────────────────────\n"
        f"✅ 本群組已成功綁定為：【{strat_name}】專屬戰情室\n"
        f"📌 群組代號：{group_id[:8]}...{group_id[-4:]}\n\n"
        f"未來此策略之盤後選股名單與盤中雷達警示，將專屬定向推播至此，徹底杜絕多空混淆！"
    )
    payload = {
        "to": group_id,
        "messages": [{"type": "text", "text": msg}]
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"  [!] 發送歡迎詞失敗: {e}")
        return False

def check_webhook_events(token, seen_req_ids, bound_groups):
    """輪詢 Webhook.site 接收的請求"""
    url = f"https://webhook.site/token/{WEBHOOK_UUID}/requests"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            res_data = json.loads(resp.read().decode('utf-8'))
            requests_list = res_data.get('data', [])
    except Exception as e:
        return

    # 由舊到新處理
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
            events = body.get('events', [])
            for ev in events:
                src = ev.get('source', {})
                if src.get('type') == 'group':
                    gid = src.get('groupId')
                    if not gid or gid in bound_groups:
                        continue

                    msg_text = ""
                    if ev.get('type') == 'message':
                        msg_text = ev.get('message', {}).get('text', '')

                    g_name = get_group_summary(token, gid) or "台股選股群組"
                    print("\n" + "=" * 65)
                    print(f"🎉 捕捉到新群組！名稱: 【{g_name}】")
                    print(f"📌 Group ID: {gid}")
                    if msg_text:
                        print(f"💬 收到訊息內容: '{msg_text}'")
                    print("=" * 65)

                    # 智慧推斷策略歸屬
                    assigned_strat = None
                    strat_title = ""
                    text_to_check = (g_name + " " + msg_text).lower()

                    if any(k in text_to_check for k in ["01", "1", "超跌", "布林", "抄底", "反轉"]):
                        assigned_strat = "LINE_TARGET_STRATEGY_01"
                        strat_title = "策略 01：布林下軌超跌反轉 (抄底多方)"
                    elif any(k in text_to_check for k in ["02", "2", "法人", "起漲", "動能", "主升"]):
                        assigned_strat = "LINE_TARGET_STRATEGY_02"
                        strat_title = "策略 02：法人籌碼集中起漲 (順勢多方)"
                    elif any(k in text_to_check for k in ["03", "3", "做空", "放空", "避險", "股期", "破線"]):
                        assigned_strat = "LINE_TARGET_STRATEGY_03"
                        strat_title = "策略 03：弱勢破線做空避險 (股票期貨)"
                    elif any(k in text_to_check for k in ["04", "4", "可轉債", "cb", "定價", "伏擊", "閉鎖"]):
                        assigned_strat = "LINE_TARGET_STRATEGY_04"
                        strat_title = "策略 04：可轉債定價伏擊與區間博弈"
                    else:
                        # 依照尚未綁定的順序自動配對
                        env = load_env()
                        if not env.get("LINE_TARGET_STRATEGY_01"):
                            assigned_strat = "LINE_TARGET_STRATEGY_01"
                            strat_title = "策略 01：布林下軌超跌反轉 (抄底多方)"
                        elif not env.get("LINE_TARGET_STRATEGY_02"):
                            assigned_strat = "LINE_TARGET_STRATEGY_02"
                            strat_title = "策略 02：法人籌碼集中起漲 (順勢多方)"
                        elif not env.get("LINE_TARGET_STRATEGY_03"):
                            assigned_strat = "LINE_TARGET_STRATEGY_03"
                            strat_title = "策略 03：弱勢破線做空避險 (股票期貨)"
                        elif not env.get("LINE_TARGET_STRATEGY_04"):
                            assigned_strat = "LINE_TARGET_STRATEGY_04"
                            strat_title = "策略 04：可轉債定價伏擊與區間博弈"

                    if assigned_strat:
                        save_env_var(assigned_strat, gid)
                        bound_groups[gid] = assigned_strat
                        print(f"🚀 正在發送綁定確認卡片至群組...")
                        send_group_welcome(token, gid, strat_title)
                        print(f"✨【{strat_title}】已成功綁定！\n")
        except Exception as e:
            pass

def main():
    env = load_env()
    token = env.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("[X] 找不到 LINE_CHANNEL_ACCESS_TOKEN，請檢查 .env！")
        return

    print("=" * 65)
    print(" 🎯 台股量化策略 - LINE 獨立群組自動綁定小幫手")
    print("=" * 65)
    print("【機器人資訊】：")
    print(" • 名稱：選股日報小幫手")
    print(" • LINE ID：@899zzwux")
    print("\n【目前綁定狀態】：")
    print(f" • 策略 01 (布林超跌): {env.get('LINE_TARGET_STRATEGY_01', '尚未綁定')}")
    print(f" • 策略 02 (法人起漲): {env.get('LINE_TARGET_STRATEGY_02', '尚未綁定')}")
    print(f" • 策略 03 (股期做空): {env.get('LINE_TARGET_STRATEGY_03', '尚未綁定')}")
    print("=" * 65)
    print("\n💡【請總指揮執行以下動作】：")
    print("1. 打開手機 LINE，建立 3 個群組（建議名稱含 01 / 02 / 03 或 超跌 / 起漲 / 做空）。")
    print("2. 將機器人【選股日報小幫手 (@899zzwux)】邀請加入這 3 個群組。")
    print("3. 在每個群組內傳送任意一句話 (例如打 '01', '02', '03')。")
    print("   本程式將即時接收並自動完成寫入！(按 Ctrl+C 可隨時退出)\n")

    seen_req_ids = set()
    bound_groups = {}

    while True:
        try:
            check_webhook_events(token, seen_req_ids, bound_groups)
            env_cur = load_env()
            if (env_cur.get("LINE_TARGET_STRATEGY_01") and 
                env_cur.get("LINE_TARGET_STRATEGY_02") and 
                env_cur.get("LINE_TARGET_STRATEGY_03")):
                print("\n" + "★" * 65)
                print("🎊 報告總指揮！所有 3 項策略專屬群組已全數綁定完畢！")
                print("★" * 65)
                break
            time.sleep(2)
        except KeyboardInterrupt:
            print("\n[✓] 退出監聽。")
            break

if __name__ == '__main__':
    main()
