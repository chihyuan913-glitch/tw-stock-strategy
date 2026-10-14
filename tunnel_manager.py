#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 外網專屬連線穿透引擎 (Cloudflare Tunnel Manager)
================================================================================
本模組為戰情室提供安全、極速、免公網 IP 的專屬外網連線通道：
1. 自動調用本機 cloudflared.exe 建立全球 CDN 加速安全通道 (HTTPS)。
2. 自動產出 Google 雲端硬碟連動之「外網看盤直達.url」捷徑（別台電腦雙擊直接進）。
3. 自動將外網網址寫入 web_url.txt 並即時推播至 LINE，手機/平板點擊秒看盤。
4. 與 Web 伺服器生命週期深度綁定，關閉時自動安全釋放資源。
================================================================================
"""

import os
import sys
import re
import time
import subprocess
import threading
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

# Windows 命令列編碼保護
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CLOUDFLARED_EXE = ROOT_DIR / "cloudflared.exe"
URL_SHORTCUT_FILE = ROOT_DIR / "外網看盤直達.url"
URL_TXT_FILE = ROOT_DIR / "web_url.txt"

_cf_process = None
_tunnel_url = None
_tunnel_thread = None
_is_stopping = False

def get_current_tunnel_url() -> str:
    """取得目前活躍之外網連線網址"""
    global _tunnel_url
    if _tunnel_url:
        return _tunnel_url
    if URL_TXT_FILE.exists():
        try:
            with open(URL_TXT_FILE, "r", encoding="utf-8") as f:
                url = f.read().strip()
                if url.startswith("http"):
                    return url
        except Exception:
            pass
    return ""

def _write_shortcut(url: str):
    """產出 Windows 網頁捷徑檔，跨電腦雙擊即開"""
    content = f"[InternetShortcut]\nURL={url}\nIconIndex=0\n"
    try:
        with open(URL_SHORTCUT_FILE, "w", encoding="utf-8") as f:
            f.write(content)
        with open(URL_TXT_FILE, "w", encoding="utf-8") as f:
            f.write(url)
    except Exception as e:
        print(f"[!] 寫入外網捷徑檔失敗: {e}")

def sync_line_webhook(public_url: str) -> dict:
    """
    自動將公開網址註冊為 LINE 官方帳號 (二號機) 的 Webhook Endpoint，並觸發測試驗證。
    解決手動複製貼上網址之痛點，達成啟動即全自動秒級對接。
    """
    import urllib.request
    import json

    token = ""
    env_file = ROOT_DIR / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for l in f:
                    l = l.strip()
                    if l.startswith("LINE_CHANNEL_ACCESS_TOKEN="):
                        token = l.split("=", 1)[1].strip().strip("'\"")
                        break
        except Exception:
            pass
    if not token:
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

    if not token:
        print("[!] 找不到 LINE_CHANNEL_ACCESS_TOKEN，跳過 LINE Webhook 自動註冊。")
        return {"success": False, "reason": "No token"}

    webhook_url = public_url.rstrip("/")
    if not webhook_url.endswith("/callback"):
        webhook_url += "/callback"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # 1. 呼叫 LINE PUT Webhook Endpoint (帶 3 次重試防 Cloudflare DNS 傳播延遲)
    put_success = False
    for attempt in range(1, 4):
        try:
            put_url = "https://api.line.me/v2/bot/channel/webhook/endpoint"
            put_data = json.dumps({"endpoint": webhook_url}).encode("utf-8")
            req = urllib.request.Request(put_url, data=put_data, headers=headers, method="PUT")
            with urllib.request.urlopen(req, timeout=10) as resp:
                put_success = True
                break
        except urllib.error.HTTPError as e:
            if attempt < 3:
                time.sleep(2.5)
                continue
            err_msg = e.read().decode("utf-8", errors="ignore")
            print(f"[!] 設定 LINE Webhook Endpoint 失敗 (HTTP {e.code}): {err_msg}")
            return {"success": False, "reason": err_msg}
        except Exception as e:
            if attempt < 3:
                time.sleep(2.5)
                continue
            print(f"[!] 設定 LINE Webhook Endpoint 例外: {e}")
            return {"success": False, "reason": str(e)}

    # 2. 呼叫 LINE POST Webhook Test 驗證連通性
    test_result = "未知"
    try:
        test_url = "https://api.line.me/v2/bot/channel/webhook/test"
        test_data = json.dumps({"endpoint": webhook_url}).encode("utf-8")
        req = urllib.request.Request(test_url, data=test_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            t_res = json.loads(resp.read().decode("utf-8"))
            if t_res.get("success"):
                test_result = f"200 OK (連線成功, detail: {t_res.get('detail')})"
            else:
                test_result = f"連線警告: {t_res.get('reason')}"
    except Exception as e:
        test_result = f"測試例外: {e}"

    print("-" * 70, flush=True)
    print(" 🎉【LINE 官方帳號隨傳隨回 Webhook 已自動秒級對接完成！】", flush=True)
    print(f" 🔗 即時 Webhook 端點: {webhook_url}", flush=True)
    print(f" ⚡ LINE 官方伺服器連線測試: {test_result}", flush=True)
    print(" 📱 您現在可在手機 LINE 直接輸入個股代碼或名稱：", flush=True)
    print("    👉 例如輸入「2330」或「台積電」、「診斷 2476」", flush=True)
    print("    👉 系統將在 1 秒內秒回四大防線價位與雙向錦囊！", flush=True)
    print("-" * 70 + "\n", flush=True)

    return {"success": True, "endpoint": webhook_url, "test_result": test_result}

def _notify_line(url: str):
    """自動推播外網專屬網址至 LINE"""
    try:
        from line_sender import send_to_line
        msg = f"""🌐【台股戰情室・外網專屬連線已就緒】
━━━━━━━━━━━━━━━
別台電腦、筆電、平板或手機點擊下方連結即可即時看盤：
🔗 {url}

🎯 即時戰情功能：
• 盤中到價即時雷達 (四大防線)
• 四大策略選股戰報
• 個股即時診斷與操盤錦囊
• 跨日動態滾動校準 (移動停利)
━━━━━━━━━━━━━━━
📱【手機隨傳隨回秒級診斷已同步啟動】
在此視窗直接傳送股票代碼（如 2330、2476）或股票名稱（如 台積電），小助手秒回四大操盤防線與買賣錦囊！"""
        send_to_line(msg, strategy=None)
    except Exception as e:
        print(f"[!] 推送外網網址至 LINE 失敗: {e}")

def ensure_cloudflared_installed() -> bool:
    """若本機尚未具備 cloudflared.exe，自動下載安裝"""
    if CLOUDFLARED_EXE.exists():
        return True
    print("[*] 正在為您自動下載 Cloudflare 官方穿透工具 (cloudflared.exe)...")
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    try:
        import urllib.request
        urllib.request.urlretrieve(url, str(CLOUDFLARED_EXE))
        print("[✓] cloudflared.exe 下載完成！")
        return True
    except Exception as e:
        print(f"[!] 自動下載 cloudflared.exe 失敗: {e}")
        return False

def _monitor_tunnel(port: int, notify_line: bool = True):
    """背景執行緒：啟動 cloudflared 並擷取隨機專屬 HTTPS 網址"""
    global _cf_process, _tunnel_url, _is_stopping

    if not ensure_cloudflared_installed():
        return

    cmd = [
        str(CLOUDFLARED_EXE),
        "tunnel",
        "--url",
        f"http://127.0.0.1:{port}"
    ]

    try:
        _cf_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )

        for line in iter(_cf_process.stdout.readline, ""):
            if _is_stopping:
                break
            # 尋找分配到的 https://xxx.trycloudflare.com
            m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if m:
                _tunnel_url = m.group(0)
                print("\n" + "=" * 70, flush=True)
                print(f" 🌐【外網專屬連線通道建立成功！】", flush=True)
                print(f" 🔗 外網專屬網址: {_tunnel_url}", flush=True)
                print(f" 📁 已自動產出捷徑: 【外網看盤直達.url】(別台電腦雙擊即可直達)", flush=True)
                print("=" * 70 + "\n", flush=True)

                _write_shortcut(_tunnel_url)
                # 自動對接與更新 LINE 官方帳號 Webhook 端點
                try:
                    sync_line_webhook(_tunnel_url)
                except Exception as _e:
                    print(f"[!] 自動同步 LINE Webhook 例外: {_e}")

                if notify_line:
                    _notify_line(_tunnel_url)
                break

    except Exception as e:
        if not _is_stopping:
            print(f"[!] Cloudflare Tunnel 啟動異常: {e}")

def start_tunnel_async(port: int = 8989, notify_line: bool = True):
    """非同步啟動外網穿透隧道"""
    global _tunnel_thread, _is_stopping
    _is_stopping = False
    _tunnel_thread = threading.Thread(
        target=_monitor_tunnel,
        args=(port, notify_line),
        daemon=True
    )
    _tunnel_thread.start()

def stop_tunnel():
    """安全停止外網隧道行程"""
    global _cf_process, _is_stopping
    _is_stopping = True
    if _cf_process:
        try:
            _cf_process.terminate()
            _cf_process.wait(timeout=3)
        except Exception:
            try:
                _cf_process.kill()
            except Exception:
                pass
        _cf_process = None
        print("[✓] Cloudflare 外網連線通道已安全關閉。")

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8989
    print(f"[*] 正在為連接埠 {port} 啟動專屬外網連線通道...")
    start_tunnel_async(port=port, notify_line=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] 正在關閉隧道...")
        stop_tunnel()
