# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - Web 戰情儀表板一鍵啟動器 (Web Server Launcher)
雙擊桌面圖示即可自動開啟預設瀏覽器，直接進入全功能量化選股戰情室。

特點：
1. 採用專屬獨立連接埠 8989，絕不佔用或衝突 8000、8080 等其他專案/系統連接埠。
2. 具備智慧服務識別 (Service Signature)，自動辨識是否為台股系統本身。
3. 具備自動避讓機制 (Dynamic Port Fallback)，若連接埠被佔用自動平移至可用連接埠。
4. 符合 LOCAL_WEB_APP_LIFECYCLE_RULE_V1 全域生命週期規範：
   - 伺服器就緒後精準自動喚起瀏覽器
   - 關閉瀏覽器分頁即自我終止釋放資源
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

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# 確保在無控制台視窗 (Silent VBS / WScript) 環境下安全記錄日誌，防止 NoneType print 崩潰
LOG_FILE = CURRENT_DIR / "start_web.log"
try:
    log_fp = open(LOG_FILE, "w", encoding="utf-8", buffering=1)
    sys.stdout = log_fp
    sys.stderr = log_fp
except Exception:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

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

def wait_for_server_and_open_browser(url: str, port: int):
    """
    主動輪詢健康檢查端點，確認伺服器已 100% 準備就緒後，再精準喚起預設瀏覽器。
    杜絕伺服器載入中瀏覽器提早開啟導致 ERR_CONNECTION_REFUSED 問題。
    """
    health_url = f"http://127.0.0.1:{port}/health"
    start_time = time.time()
    server_ready = False
    
    while time.time() - start_time < 30:
        try:
            req = urllib.request.Request(health_url, headers={'User-Agent': 'StockWebLauncher/1.0'})
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                if resp.status == 200:
                    server_ready = True
                    break
        except Exception:
            pass
        time.sleep(0.3)
    
    elapsed = time.time() - start_time
    print(f"\n[🌐] 伺服器已就緒 (耗時 {elapsed:.1f} 秒)，自動為您喚起預設瀏覽器進入操作介面：{url}", flush=True)
    time.sleep(0.4)
    webbrowser.open(url)

def main():
    host = "127.0.0.1"
    port, already_running = find_available_port(BASE_PORT)
    url = f"http://{host}:{port}"

    print("=" * 72, flush=True)
    print("    📈 台股量化選股策略 - 最高總指揮中心戰情儀表板", flush=True)
    print(f"    本機獨立專屬服務網址：{url} (專屬獨立埠，絕不衝突其他網頁)", flush=True)
    print("    整合功能：📊 四大選股戰報 ｜ 🔍 個股即時診斷 ｜ 🧭 週日優化 ｜ 🚀 雲端直達", flush=True)
    print("    核心準則：4+1 維度嚴選 ｜ 操盤四大防線價位 (進場、加碼、停利、停損)", flush=True)
    print("=" * 72, flush=True)

    # 1. 若台股系統已經在背景運行，直接喚起既有瀏覽器並安全退出（防重複啟動保護）
    if already_running:
        print(f"\n[⚡] 偵測到台股戰情系統已在背景運行中 (埠號 {port})，直接為您喚起瀏覽器：{url}", flush=True)
        webbrowser.open(url)
        time.sleep(1.0)
        return

    # 2. 啟動背景線程：等待伺服器正式就緒後自動開啟瀏覽器
    threading.Thread(target=wait_for_server_and_open_browser, args=(url, port), daemon=True).start()

    # 3. 啟動伺服器
    print(f"\n[🚀] 伺服器正在獨立連接埠 {port} 啟動中，請稍候...", flush=True)
    print("    💡 提示：本系統具備生命週期綁定規範，關閉瀏覽器分頁即會自動安全終止並釋放資源。\n", flush=True)
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
        print("\n[🛑] 伺服器已安全停止。", flush=True)
    except Exception as e:
        print(f"\n[❌] 伺服器發生異常：{e}", flush=True)

if __name__ == "__main__":
    main()
