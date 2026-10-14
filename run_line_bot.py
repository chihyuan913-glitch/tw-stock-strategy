#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - LINE 盤中隨傳隨回操盤機器人主執行器
================================================================================
特色：
1. 自動偵測與啟動本機 FastAPI/Uvicorn 伺服器 (埠號 8989)。
2. 自動建立 Cloudflare 全球 CDN 免費安全通道 (HTTPS)。
3. 自動呼叫 LINE Messaging API 更新 Webhook Endpoint，免手動複製貼上！
4. 自動觸發 LINE 官方連線握手驗證，確保手機「隨傳隨回」秒級診斷 100% 通暢。
5. 支援背景長駐運行，按 Ctrl+C 安全優雅結束。
================================================================================
"""

import os
import sys
import time
import json
import socket
import urllib.request
import subprocess
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
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tunnel_manager import start_tunnel_async, stop_tunnel, get_current_tunnel_url, sync_line_webhook

PORT = 8989

def is_our_service(port: int) -> bool:
    """檢查指定連接埠上是否已經是台股系統在運行"""
    try:
        url = f"http://127.0.0.1:{port}/health"
        req = urllib.request.Request(url, headers={'User-Agent': 'LineBotRunner/1.0'})
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get("service") == "tw-stock-line-bot"
    except Exception:
        return False

def is_port_in_use(port: int) -> bool:
    """檢查指定連接埠是否被佔用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def main():
    print("=" * 72, flush=True)
    print("  🚀 台股量化最高總指揮中心 - LINE 盤中隨傳隨回操盤機器人", flush=True)
    print("  📱 手機 LINE 隨意傳送股票代碼 (如 2330) 或中文名稱 (如 台積電)", flush=True)
    print("  ⚡ 1 秒內極速秒回操盤四大防線 (🟢進場 🔵加碼 🔴停利 🛑停損) 與操盤錦囊！", flush=True)
    print("=" * 72 + "\n", flush=True)

    server_proc = None
    already_running = is_our_service(PORT)

    if already_running:
        print(f"[✓] 偵測到後端伺服器已在運行中 (http://127.0.0.1:{PORT})，直接對接！", flush=True)
    elif is_port_in_use(PORT):
        print(f"[!] 連接埠 {PORT} 已被其他程式佔用，請檢查是否有衝突行程。", flush=True)
        sys.exit(1)
    else:
        print(f"[*] [1/3] 正在啟動後端量化分析與 Webhook 伺服器 (連接埠 {PORT})...", flush=True)
        server_cmd = [
            sys.executable, "-u", "-m", "uvicorn",
            "line_bot_server:app",
            "--host", "0.0.0.0",
            "--port", str(PORT),
            "--log-level", "warning"
        ]
        server_proc = subprocess.Popen(
            server_cmd,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )

        # 等待伺服器就緒
        ready = False
        start_wait = time.time()
        while time.time() - start_wait < 15:
            if is_our_service(PORT):
                ready = True
                break
            time.sleep(0.5)

        if not ready:
            print("[!] 伺服器未能及時啟動，請檢查相關日誌。", flush=True)
            if server_proc and server_proc.poll() is not None:
                out, _ = server_proc.communicate()
                print(out, flush=True)
            sys.exit(1)
        print(f"[✓] 後端伺服器已順利就緒！", flush=True)

    # 啟動 Cloudflare Tunnel (若尚未啟動)
    print(f"[*] [2/3] 正在建立 Cloudflare 免費全球 HTTPS 穿透通道...", flush=True)
    start_tunnel_async(port=PORT, notify_line=True)

    # 等待獲取 Tunnel URL
    tunnel_url = ""
    start_wait = time.time()
    while time.time() - start_wait < 25:
        tunnel_url = get_current_tunnel_url()
        if tunnel_url and "trycloudflare.com" in tunnel_url:
            break
        time.sleep(1.0)

    if not tunnel_url:
        print("[!] 尚未獲取到 Cloudflare 公開網址，請手動確認網路環境。", flush=True)
    else:
        print(f"[✓] Cloudflare 外網專屬網址已就緒: {tunnel_url}", flush=True)
        print(f"[*] [3/3] 正在全自動對接 LINE 官方帳號 Webhook 端點...", flush=True)
        sync_res = sync_line_webhook(tunnel_url)

    print("\n" + "=" * 72, flush=True)
    print("  🎉【LINE 隨傳隨回操盤助手已進入常駐監控狀態！】", flush=True)
    print("  👉 現在拿起手機打開 LINE（台股雷達二號機 @761awmcp）：", flush=True)
    print("     • 傳送「2330」或「台積電」：秒回四大防線價位與雙向錦囊", flush=True)
    print("     • 傳送「監控 2330」：即刻加入盤中到價即時雷達", flush=True)
    print("     • 傳送「同步選股」：一鍵載入今日策略黑馬名單", flush=True)
    print("  💡 保持本視窗開啟即可隨傳隨回。若要結束服務，請按 Ctrl+C。", flush=True)
    print("=" * 72 + "\n", flush=True)

    try:
        while True:
            if server_proc and server_proc.poll() is not None:
                print("[!] 後端伺服器已異常停止。", flush=True)
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[*] 正在安全關閉 LINE 操盤服務與外網通道...", flush=True)
    finally:
        if server_proc and server_proc.poll() is None:
            try:
                server_proc.terminate()
                server_proc.wait(timeout=3)
            except Exception:
                server_proc.kill()
        stop_tunnel()
        print("[✓] 操盤助手服務已安全退出。")

if __name__ == "__main__":
    main()
