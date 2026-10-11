# -*- coding: utf-8 -*-
import os
import sys
import subprocess

# 設定 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("=" * 60)
print("正在關閉「台股量化選股策略 最高總指揮中心戰情儀表板」...")
print("=" * 60)

pids = set()
try:
    out = subprocess.check_output("netstat -ano -p tcp", shell=True, text=True, errors="ignore")
    for line in out.splitlines():
        if ":8989" in line or ":8990" in line:
            parts = line.strip().split()
            if len(parts) >= 5:
                try:
                    pid = int(parts[-1])
                    if pid > 0 and pid != os.getpid():
                        pids.add(pid)
                except ValueError:
                    pass
except Exception as e:
    print(f"[!] 檢查連接埠失敗: {e}")

if not pids:
    print("[i] 目前未發現運行中之台股戰情系統服務。")
else:
    for pid in pids:
        try:
            subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"[✓] 已安全關閉處理程序 (PID: {pid})")
        except Exception:
            pass
    print("[🎉] 台股戰情系統已完全關閉，電腦記憶體與 8989 通訊埠已順利釋放！")
