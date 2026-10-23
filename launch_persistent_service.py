#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
啟動台股量化選股與 LINE 盤中隨傳隨回獨立背景常駐守護行程
透過 Windows WMI (Win32_Process.Create) 派生獨立守護行程，
父進程直屬系統服務 WmiPrvSE.exe，完全脫離任何終端、Shell 或工作階段，
確保 24H 穩定常駐不中斷，徹底杜絕 502 Bad Gateway。
"""

import os
import sys
import time
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

def is_running() -> bool:
    """檢查指定連接埠是否已由本服務監聽"""
    try:
        import urllib.request, json
        req = urllib.request.Request("http://127.0.0.1:8989/health", headers={'User-Agent': 'HealthCheck/1.0'})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get("service") == "tw-stock-line-bot"
    except Exception:
        return False

def launch_service() -> bool:
    if is_running():
        print("[✓] 台股量化常駐服務已在運行中 (http://127.0.0.1:8989)，無須重複啟動。")
        return True

    py_path = sys.executable
    script_path = str(ROOT_DIR / "run_line_bot.py")

    if os.name == "nt":
        # 優先使用 WMI 派生直屬系統服務的完全脫鉤行程
        ps_cmd = f"""
        $res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{
            CommandLine = '"{py_path}" "{script_path}"'
            CurrentDirectory = '{ROOT_DIR}'
        }}
        Write-Output "ReturnCode: $($res.ReturnValue), PID: $($res.ProcessId)"
        """
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
            print(f"[*] 透過 WMI 建立背景守護進程: {res.stdout.strip()}")
        except Exception as e:
            print(f"[!] WMI 啟動例外: {e}，降級使用 CREATE_NO_WINDOW 啟動...")
            subprocess.Popen([py_path, script_path], cwd=str(ROOT_DIR), creationflags=0x08000000)
    else:
        subprocess.Popen([py_path, script_path], cwd=str(ROOT_DIR), start_new_session=True)

    # 等待並驗證服務是否成功在線
    for _ in range(15):
        time.sleep(1)
        if is_running():
            print("[✓] 台股量化常駐服務已成功啟動並常駐 (http://127.0.0.1:8989)！")
            return True

    print("[!] 警告：服務啟動逾時，請檢查 start_web.log 或 run_line_bot.log")
    return False

if __name__ == "__main__":
    success = launch_service()
    sys.exit(0 if success else 1)
