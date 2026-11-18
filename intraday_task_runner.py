# -*- coding: utf-8 -*-
"""
本機盤中即時掃描排程啟動器 (Intraday Task Runner)
供 Windows 工作排程器於平日 08:58 準時啟動，錯開啟動各策略掃描器，
並於台北時間 13:30:00 準時終止所有子程序。不執行任何推播。
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

import intraday_speed

_FORBIDDEN_FLAGS = {
    "--force",
    "--force-push",
    "--test-push",
    "--force-test",
    "--once",
    "--ignore-holiday",
}


def _default_lock(port: int) -> Optional[socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", int(port)))
        sock.listen(1)
        return sock
    except OSError:
        try:
            sock.close()
        except OSError:
            pass
        return None


def _default_spawn(cmd: List[str], cwd: str, env: Dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, cwd=cwd, env=env)


def _default_kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
        )
    else:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    now_fn: Optional[Callable[[], Any]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    spawn_fn: Optional[Callable[..., Any]] = None,
    kill_tree_fn: Optional[Callable[[int], None]] = None,
    lock_fn: Optional[Callable[[], Any]] = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    for a in args:
        if a in _FORBIDDEN_FLAGS:
            print(f"[REJECT] 禁止使用參數 {a}，本啟動器不接受強制或測試旗標。")
            return 2
    if args:
        print(f"[REJECT] 不接受多餘參數: {args}")
        return 2

    get_now = now_fn or intraday_speed.taipei_now
    do_sleep = sleep_fn or time.sleep
    do_spawn = spawn_fn or _default_spawn
    do_kill = kill_tree_fn or _default_kill_tree

    now_tw = intraday_speed.to_taipei(get_now())
    ok, reason = intraday_speed.is_runner_window(now_tw)
    if not ok:
        print(f"[SKIP] 非盤中排程時段: {reason}")
        return 0

    lock_port = intraday_speed.get_int("TW_INTRADAY_RUNNER_LOCK_PORT")
    lock_handle = None
    try:
        if lock_fn is not None:
            lock_handle = lock_fn()
        else:
            lock_handle = _default_lock(lock_port)
    except Exception as e:
        print(f"[SKIP] 已有執行個體或鎖定失敗: {e}")
        return 0

    if not lock_handle:
        print("[SKIP] 已有執行個體運行中，本次退出。")
        return 0

    procs: List[Any] = []
    try:
        import run_all_intraday

        strategies = list(getattr(run_all_intraday, "INTRADAY_STRATEGIES", []))
        interval_sec = intraday_speed.get_int("TW_SCANNER_INTERVAL_SEC")
        stagger_sec = intraday_speed.get_int("TW_SCANNER_STAGGER_SEC")

        child_env = os.environ.copy()
        child_env["TW_RADAR_STRICT_WINDOW"] = "1"
        child_env["TW_INTRADAY_LEDGER_DIR"] = str(intraday_speed.ledger_dir())

        start_date = now_tw.date()

        for idx, strat in enumerate(strategies):
            if idx > 0 and stagger_sec > 0:
                do_sleep(stagger_sec)

            cur_tw = intraday_speed.to_taipei(get_now())
            cur_ok, cur_reason = intraday_speed.is_runner_window(cur_tw)
            if cur_tw.date() != start_date or not cur_ok:
                print(f"[STOP] 啟動階段進入停止時段: {cur_reason}")
                break

            cmd = [sys.executable, str(strat["script"]), "--interval", str(interval_sec)]
            proc = do_spawn(cmd, cwd=str(strat["cwd"]), env=child_env)
            procs.append(proc)
            print(f"[SPAWN] {strat['id']} PID={proc.pid}")

        while True:
            cur_tw = intraday_speed.to_taipei(get_now())
            cur_ok, _ = intraday_speed.is_runner_window(cur_tw)
            if (
                cur_tw.date() != start_date
                or cur_tw.time() >= intraday_speed.RUNNER_END
                or not cur_ok
            ):
                for p in procs:
                    if p.poll() is None:
                        try:
                            do_kill(p.pid)
                        except Exception:
                            pass
                print("[STOP] 已達 13:30:00 停止時段，已終止所有存活子程序。")
                return 0

            if all(p.poll() is not None for p in procs):
                print("[DONE] 所有策略掃描器已自行結束。")
                return 0

            do_sleep(1)
    finally:
        if hasattr(lock_handle, "close"):
            try:
                lock_handle.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
