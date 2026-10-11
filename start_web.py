# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - Web 戰情儀表板一鍵啟動器 (Web Server Launcher)
雙擊桌面圖示即可自動開啟預設瀏覽器，直接進入全功能量化選股戰情室。
"""

import sys
import os
import time
import socket
import webbrowser
import threading
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

def is_port_in_use(port: int = 8000) -> bool:
    """檢查指定連接埠是否已被監聽"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0

def open_browser(url: str):
    """延遲 1.2 秒後自動喚起預設瀏覽器進入介面"""
    time.sleep(1.2)
    print(f"\n[🌐] 自動為您開啟瀏覽器進入操作介面：{url}")
    webbrowser.open(url)

def main():
    host = "127.0.0.1"
    port = 8000
    url = f"http://{host}:{port}"

    print("=" * 72)
    print("    📈 台股量化選股策略 - 最高總指揮中心戰情儀表板")
    print(f"    本機服務網址：{url}")
    print("    整合功能：📊 四大選股戰報 ｜ 🔍 個股即時診斷 ｜ 🧭 週日優化 ｜ 🚀 雲端直達")
    print("    核心準則：4+1 維度嚴選 ｜ 操盤四大防線價位 (進場、加碼、停利、停損)")
    print("=" * 72)

    # 1. 若已經在運行，直接開啟瀏覽器並退出，避免重複啟動報錯
    if is_port_in_use(port):
        print(f"\n[⚡] 偵測到服務已在運行中，直接為您喚起瀏覽器：{url}")
        webbrowser.open(url)
        time.sleep(1.2)
        return

    # 2. 啟動非同步開啟瀏覽器線程
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    # 3. 啟動伺服器
    print("\n[🚀] 伺服器正在啟動中，請稍候...")
    print("    💡 提示：使用完畢後，直接關閉本黑色視窗即可完全停止服務。\n")
    try:
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
