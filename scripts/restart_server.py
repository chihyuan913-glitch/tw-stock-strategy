import os
import sys
import subprocess
import time
import socket

# 找到使用 8989 的 PID
cmd = 'netstat -ano | findstr :8989'
res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
pids = set()
for line in res.stdout.strip().split('\n'):
    parts = line.strip().split()
    if len(parts) >= 5 and 'LISTENING' in parts:
        pids.add(parts[-1])

print('Found PIDs on 8989:', pids)
for pid in pids:
    try:
        subprocess.run(f'taskkill /F /PID {pid}', shell=True, capture_output=True)
        print(f'Killed PID {pid}')
    except Exception as e:
        print(f'Error killing {pid}:', e)

time.sleep(1)

# 重新啟動 start_web.py
subprocess.Popen([sys.executable, 'start_web.py'], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
print('Relaunched start_web.py')
time.sleep(2)
