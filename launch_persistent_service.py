#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
啟動台股量化選股與 LINE 盤中隨傳隨回獨立背景常駐守護行程
使用 DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP 確保行程完全脫鉤常駐，
不隨任何命令列終端關閉而中斷。
"""

import os
import sys
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

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

# 檢查指定連接埠是否已由本服務監聽
def is_running():
    try:
        import urllib.request, json
        req = urllib.request.Request("http://127.0.0.1:8989/health", headers={'User-Agent': 'HealthCheck/1.0'})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get("service") == "tw-stock-line-bot"
    except Exception:
        return False

if __name__ == "__main__":
    if is_running():
        print("[✓] 台股量化常駐服務已在運行中 (http://127.0.0.1:8989)，無須重複啟動。")
        sys.exit(0)

    cmd = [sys.executable, str(ROOT_DIR / "run_line_bot.py")]
    
    # 建立獨立日誌輸出
    log_fp = open(ROOT_DIR / "persistent_daemon.log", "a", encoding="utf-8", buffering=1)
    
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True
    )
    print(f"[✓] 已成功孵化獨立背景常駐守護行程 (PID: {proc.pid})！")
