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

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 安全日誌包裝器：防範 Windows 無視窗 (Windowless/Silent VBS/WScript) 環境下
# 寫入無效控制台句柄拋出 OSError: [WinError 6] 控點無效 導致守護行程無聲崩潰。
LOG_FILE = ROOT_DIR / "run_line_bot.log"

class SafeOutputLogger:
    def __init__(self, filename, original_stream):
        self.filename = filename
        self.original_stream = original_stream
        self._file = None
        try:
            self._file = open(filename, "a", encoding="utf-8", buffering=1)
        except Exception:
            pass

    def write(self, s):
        if self._file:
            try:
                self._file.write(s)
            except Exception:
                pass
        if self.original_stream:
            try:
                self.original_stream.write(s)
            except Exception:
                pass

    def flush(self):
        if self._file:
            try:
                self._file.flush()
            except Exception:
                pass
        if self.original_stream:
            try:
                self.original_stream.flush()
            except Exception:
                pass

    def reconfigure(self, **kwargs):
        pass

# 替換 stdout 與 stderr 為防呆 SafeOutputLogger
sys.stdout = SafeOutputLogger(LOG_FILE, sys.stdout)
sys.stderr = SafeOutputLogger(LOG_FILE, sys.stderr)

from tunnel_manager import start_tunnel_async, stop_tunnel, get_current_tunnel_url

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

def _spawn_uvicorn_server(port: int):
    """啟動後端 Uvicorn 伺服器，將日誌寫入檔案避免 Pipe Buffer 阻塞"""
    server_cmd = [
        sys.executable, "-u", "-m", "uvicorn",
        "line_bot_server:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "warning"
    ]
    # 使用 CREATE_NO_WINDOW 確保無黑窗並允許檔案重定向日誌
    CREATE_NO_WINDOW = 0x08000000
    creationflags = CREATE_NO_WINDOW if os.name == "nt" else 0
    log_path = ROOT_DIR / "start_web.log"
    log_fp = open(log_path, "a", encoding="utf-8", buffering=1)
    
    # 強制設定 LOCAL_WEB_MODE="0"，確保台股量化戰情室 24H 永不因瀏覽器關閉而自動休眠退出
    env = os.environ.copy()
    env["LOCAL_WEB_MODE"] = "0"
    
    proc = subprocess.Popen(
        server_cmd,
        cwd=str(ROOT_DIR),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        env=env
    )
    return proc

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
        server_proc = _spawn_uvicorn_server(PORT)

        # 等待伺服器就緒
        ready = False
        start_wait = time.time()
        while time.time() - start_wait < 20:
            if is_our_service(PORT):
                ready = True
                break
            time.sleep(0.5)

        if not ready:
            print("[!] 伺服器未能及時啟動，請檢查 start_web.log 日誌。", flush=True)
            sys.exit(1)
        print(f"[✓] 後端伺服器已順利就緒！ (本機網址: http://127.0.0.1:{PORT})", flush=True)

    # 啟動 Cloudflare Tunnel (若尚未啟動)
    print(f"[*] [2/3] 正在建立 Cloudflare 免費全球 HTTPS 穿透通道...", flush=True)
    start_tunnel_async(port=PORT, notify_line=True)

    # 等待獲取 Tunnel URL
    tunnel_url = ""
    start_wait = time.time()
    while time.time() - start_wait < 25:
        tunnel_url = get_current_tunnel_url()
        if tunnel_url and (tunnel_url.startswith("http://") or tunnel_url.startswith("https://")):
            break
        time.sleep(1.0)

    if not tunnel_url:
        print("[!] 尚未獲取到 Cloudflare 公開網址，請手動確認網路環境。", flush=True)
    else:
        print(f"[✓] Cloudflare 外網專屬網址已就緒: {tunnel_url}", flush=True)
        print(f"[✓] [3/3] LINE 官方帳號 Webhook 與外網捷徑已由背景執行緒自動同步！", flush=True)

    print("\n" + "=" * 72, flush=True)
    print("  🎉【LINE 隨傳隨回操盤助手＆戰情室已進入 24H 常駐監控狀態！】", flush=True)
    print(f"  🖥️ 本機看盤網址：http://127.0.0.1:{PORT}", flush=True)
    if tunnel_url:
        print(f"  🌐 外網看盤網址：{tunnel_url}", flush=True)
    print("  👉 現在拿起手機打開 LINE（台股雷達二號機 @761awmcp）：", flush=True)
    print("     • 傳送「2330」或「台積電」：秒回四大防線價位與雙向錦囊", flush=True)
    print("     • 傳送「監控 2330」：即刻加入盤中到價即時雷達", flush=True)
    print("     • 傳送「同步選股」：一鍵載入今日策略黑馬名單", flush=True)
    print("=" * 72 + "\n", flush=True)

    try:
        while True:
            if not is_our_service(PORT):
                print("[!] 偵測到後端伺服器未運作，正在自動重啟修復...", flush=True)
                server_proc = _spawn_uvicorn_server(PORT)
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n[*] 正在安全關閉 LINE 操盤服務與外網通道...", flush=True)
        if server_proc and server_proc.poll() is None:
            try:
                server_proc.terminate()
                server_proc.wait(timeout=3)
            except Exception:
                server_proc.kill()
        stop_tunnel()
        print("[✓] 操盤助手服務已安全退出。")
    except Exception as e:
        print(f"[!] 守護監控迴圈異常: {e}", flush=True)

if __name__ == "__main__":
    main()
