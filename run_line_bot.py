#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股即時隨傳隨回 LINE 操盤機器人 - 一鍵啟動與公開通道穿透器 (Runner with Cloudflare Tunnel)
功能：
1. 啟動本機 FastAPI 伺服器 (埠號 8000)。
2. 自動啟動 tools/cloudflared.exe 穿透工具，產生免帳號、免費的專屬 HTTPS 網址。
3. 自動擷取公開 HTTPS 網址並顯示專屬 Webhook URL，方便一鍵貼至 LINE Developers 後台！
4. 保持長駐運行，按 Ctrl+C 即可優雅關閉所有進程。
"""

import os
import sys
import time
import re
import subprocess
import signal
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
CLOUDFLARED_BIN = ROOT_DIR / "tools" / "cloudflared.exe"

def main():
    print("=====================================================================", flush=True)
    print("  台股最高總指揮中心 - LINE 盤中隨傳隨回操盤機器人啟動器", flush=True)
    print("=====================================================================", flush=True)

    if not CLOUDFLARED_BIN.exists():
        print(f"[!] 找不到穿透工具：{CLOUDFLARED_BIN}", flush=True)
        print("    請確認 tools/cloudflared.exe 是否存在。", flush=True)
        sys.exit(1)

    port = 8000
    python_exe = sys.executable

    # 1. 啟動 FastAPI / Uvicorn 伺服器
    print(f"[*] [1/2] 正在啟動本機 Webhook 伺服器 (連接埠 {port})...", flush=True)
    server_cmd = [python_exe, "-u", "-m", "uvicorn", "line_bot_server:app", "--host", "127.0.0.1", "--port", str(port)]
    server_proc = subprocess.Popen(
        server_cmd,
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='ignore'
    )

    time.sleep(1.5)
    if server_proc.poll() is not None:
        print("[!] 本機伺服器啟動失敗，請檢查埠號是否被佔用。", flush=True)
        out, _ = server_proc.communicate()
        print(out, flush=True)
        sys.exit(1)
    print(f"[✓] 本機 Webhook 伺服器已在 http://127.0.0.1:{port} 順利運行！", flush=True)

    # 2. 啟動 Cloudflare Tunnel
    print(f"[*] [2/2] 正在建立 Cloudflare 免費 HTTPS 安全通道...", flush=True)
    tunnel_cmd = [
        str(CLOUDFLARED_BIN),
        "tunnel",
        "--url", f"http://127.0.0.1:{port}"
    ]
    tunnel_proc = subprocess.Popen(
        tunnel_cmd,
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='ignore'
    )

    # 3. 讀取 Cloudflare 回傳的 HTTPS 網址
    tunnel_url = None
    start_wait = time.time()
    while time.time() - start_wait < 15:
        line = tunnel_proc.stderr.readline()
        if not line:
            time.sleep(0.1)
            continue
        m = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
        if m:
            tunnel_url = m.group(0)
            break

    if not tunnel_url:
        print("[!] 尚未在時間內取得 Cloudflare 公開網址，請手動檢查紀錄。", flush=True)
    else:
        webhook_url = f"{tunnel_url}/callback"
        print("\n" + "=" * 69, flush=True)
        print("  🎉 LINE 隨傳隨回操盤助手 - 公開 Webhook 串接網址已就緒！", flush=True)
        print("=" * 69, flush=True)
        print(f"  👉 請複製下列 Webhook URL 貼到 LINE Developers Console 後台：\n", flush=True)
        print(f"     {webhook_url}\n", flush=True)
        print("  【快速設定三步驟】：", flush=True)
        print("  1. 登入 LINE Developers 後台 -> 進入您的 Messaging API Channel", flush=True)
        print("  2. 在「Messaging API」分頁中，找到「Webhook URL」貼上上方網址並點擊 [Update]", flush=True)
        print("  3. 點擊 [Verify] (確認顯示 Success)，並將下方的「Use webhook」開關打開 [ON]！", flush=True)
        print("=" * 69, flush=True)
        print("  📱 現在您可以拿起手機打開 LINE，傳送股票代碼（例如 3221、2330、2603）", flush=True)
        print("     系統將在 3 秒內自動秒回四大操盤防線價位與買點指引！", flush=True)
        print("  (保持本視窗開啟中，若要結束服務請按 Ctrl+C)\n", flush=True)

    # 4. 監聽與維持運作
    try:
        while True:
            # 檢查進程是否意外中止
            if server_proc.poll() is not None:
                print("[!] 伺服器進程已停止。", flush=True)
                break
            if tunnel_proc.poll() is not None:
                print("[!] 通道進程已停止。", flush=True)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] 正在關閉 LINE 操盤伺服器與通道...", flush=True)
    finally:
        if server_proc.poll() is None:
            server_proc.terminate()
        if tunnel_proc.poll() is None:
            tunnel_proc.terminate()
        print("[✓] 服務已安全結束。", flush=True)

if __name__ == '__main__':
    main()
