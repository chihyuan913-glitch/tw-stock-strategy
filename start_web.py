# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - Web 戰情儀表板一鍵啟動器 (Web Server Launcher)
雙擊桌面圖示即可自動開啟預設瀏覽器，直接進入全功能量化選股戰情室。

特點：
1. 採用專屬獨立連接埠 8989，絕不佔用或衝突 8000、8080 等其他專案/系統連接埠。
2. 具備智慧服務識別 (Service Signature)，自動辨識是否為台股系統本身。
3. 具備自動避讓機制 (Dynamic Port Fallback)，若連接埠被佔用自動平移至可用連接埠。
"""

import sys
import os
import time
import json
import socket
import webbrowser
import threading
import urllib.request
from pathlib import Path

# 強制設定 Windows 控制台輸出為 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import uvicorn

BASE_PORT = 8989

def is_our_service(port: int) -> bool:
    """檢查指定連接埠上是否已經是台股量化總指揮中心在運行"""
    try:
        url = f"http://127.0.0.1:{port}/health"
        req = urllib.request.Request(url, headers={'User-Agent': 'StockWebLauncher/1.0'})
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get("service") == "tw-stock-line-bot"
    except Exception:
        return False

def is_port_in_use(port: int) -> bool:
    """檢查指定連接埠是否被任何程式佔用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def find_available_port(start_port: int = BASE_PORT) -> tuple:
    """
    尋找可用連接埠：
    - 若已經是本系統在運行，回傳 (port, True)
    - 若找到全新未被佔用的連接埠，回傳 (port, False)
    """
    for p in range(start_port, start_port + 20):
        if is_our_service(p):
            return p, True
        if not is_port_in_use(p):
            return p, False
    return start_port, False

def open_browser(url: str):
    """延遲 1.2 秒後自動喚起預設瀏覽器進入介面"""
    time.sleep(1.2)
    print(f"\n[🌐] 自動為您開啟瀏覽器進入操作介面：{url}")
    webbrowser.open(url)

def main():
    host = "127.0.0.1"
    port, already_running = find_available_port(BASE_PORT)
    url = f"http://{host}:{port}"

    print("=" * 72)
    print("    📈 台股量化選股策略 - 最高總指揮中心戰情儀表板")
    print(f"    本機獨立專屬服務網址：{url} (專屬獨立埠，絕不衝突其他網頁)")
    print("    整合功能：📊 四大選股戰報 ｜ 🔍 個股即時診斷 ｜ 🧭 週日優化 ｜ 🚀 雲端直達")
    print("    核心準則：4+1 維度嚴選 ｜ 操盤四大防線價位 (進場、加碼、停利、停損)")
    print("=" * 72)

    # 1. 若台股系統已經在運行，直接喚起瀏覽器並安全退出
    if already_running:
        print(f"\n[⚡] 偵測到台股戰情系統已在背景運行中 (埠號 {port})，直接為您喚起瀏覽器：{url}")
        webbrowser.open(url)
        time.sleep(1.2)
        return

    # 2. 啟動非同步開啟瀏覽器線程
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    # 3. 啟動伺服器
    print(f"\n[🚀] 伺服器正在獨立連接埠 {port} 啟動中，請稍候...")
    print("    💡 提示：本系統具備生命週期綁定規範，關閉瀏覽器分頁即會自動安全終止並釋放資源。\n")
    try:
        os.environ["PORT"] = str(port)
        os.environ["LOCAL_WEB_MODE"] = "1"
        uvicorn.run(
            "line_bot_server:app",
            host=host,
            port=port,
            log_level="warning",
            reload=False
        )
    except KeyboardInterrupt:
        print("\n[🛑] 伺服器已安全停止。")
    except Exception as e:
        print(f"\n[❌] 伺服器發生異常：{e}")
        input("\n請按 Enter 鍵關閉...")

if __name__ == "__main__":
    main()
