#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 全策略盤中到價即時雷達總調度引擎 (Master Intraday Runner)
功能：
1. 一鍵執行所有策略的盤中到價掃描。
2. 支援 --test-push 一鍵測試全策略 LINE 到價推播連線與卡片排版。
3. 支援 --once 單次快篩巡邏。
4. 支援排程或長駐輪詢。
"""

import os
import sys
import json
import argparse
import subprocess
import datetime
import threading
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
WEB_TRIGGER_LOG_PATH = ROOT_DIR / "cache" / "web_trigger_timeout.log"
WEB_TRIGGER_STATUS_PATH = ROOT_DIR / "cache" / "web_trigger_status.json"
_WEB_TRIGGER_STATUS_LOCK = threading.Lock()
_DEFAULT_WEB_TRIGGER_STATUS = {
    "status": "idle",
    "timed_out": False,
    "timeout_sec": 900.0,
    "pid": None,
    "started_at": None,
    "finished_at": None,
    "message": "尚未觸發",
}
_WEB_TRIGGER_STATUS = dict(_DEFAULT_WEB_TRIGGER_STATUS)


def _reset_web_trigger_state_for_test() -> None:
    with _WEB_TRIGGER_STATUS_LOCK:
        _WEB_TRIGGER_STATUS.clear()
        _WEB_TRIGGER_STATUS.update(_DEFAULT_WEB_TRIGGER_STATUS)


def _write_web_trigger_log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        WEB_TRIGGER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with WEB_TRIGGER_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _update_web_trigger_status(**fields) -> dict:
    with _WEB_TRIGGER_STATUS_LOCK:
        _WEB_TRIGGER_STATUS.update(fields)
        snap = dict(_WEB_TRIGGER_STATUS)
    try:
        WEB_TRIGGER_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        WEB_TRIGGER_STATUS_PATH.write_text(
            json.dumps(snap, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return snap


def get_web_trigger_status() -> dict:
    with _WEB_TRIGGER_STATUS_LOCK:
        return dict(_WEB_TRIGGER_STATUS)

INTRADAY_STRATEGIES = [
    {
        "id": "01_bollinger_reversal",
        "name": "策略 01：布林通道下軌超跌反轉到價雷達",
        "script": ROOT_DIR / "strategies" / "01_bollinger_reversal" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "01_bollinger_reversal"
    },
    {
        "id": "02_institutional_momentum",
        "name": "策略 02：法人籌碼集中起漲到價雷達",
        "script": ROOT_DIR / "strategies" / "02_institutional_momentum" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "02_institutional_momentum"
    },
    {
        "id": "03_short_momentum",
        "name": "策略 03：弱勢破線做空與個股期貨避險到價雷達",
        "script": ROOT_DIR / "strategies" / "03_short_momentum" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "03_short_momentum"
    },
    {
        "id": "04_cb_pricing_ambush",
        "name": "策略 04：可轉債 (CB) 定價伏擊到價雷達",
        "script": ROOT_DIR / "strategies" / "04_cb_pricing_ambush" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "04_cb_pricing_ambush"
    },
    {
        "id": "05_stock_futures_long",
        "name": "策略 05：個股期貨法人強勢波段多方到價雷達",
        "script": ROOT_DIR / "strategies" / "05_stock_futures_long" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "05_stock_futures_long"
    },
    {
        "id": "06_volume_surge",
        "name": "策略 06：盤中成交量異常爆量起漲動能雷達",
        "script": ROOT_DIR / "strategies" / "06_volume_surge" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "06_volume_surge"
    },
    {
        "id": "07_margin_call_ambush",
        "name": "策略 07：高融資斷頭伏擊與浮額清洗到價雷達",
        "script": ROOT_DIR / "strategies" / "07_margin_call_ambush" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "07_margin_call_ambush"
    },
    {
        "id": "08_group_window_dressing",
        "name": "策略 08：台股集團作帳與母子聯動到價雷達",
        "script": ROOT_DIR / "strategies" / "08_group_window_dressing" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "08_group_window_dressing"
    },
    {
        "id": "09_treasury_stock_buyback",
        "name": "策略 09：庫藏股宣告護盤到價雷達",
        "script": ROOT_DIR / "strategies" / "09_treasury_stock_buyback" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "09_treasury_stock_buyback"
    },
    {
        "id": "10_investment_trust_sprint",
        "name": "策略 10：投信認養集中度爆發到價雷達",
        "script": ROOT_DIR / "strategies" / "10_investment_trust_sprint" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "10_investment_trust_sprint"
    },
    {
        "id": "11_revenue_volume_breakout",
        "name": "策略 11：營收創高與量價突破到價雷達",
        "script": ROOT_DIR / "strategies" / "11_revenue_volume_breakout" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "11_revenue_volume_breakout"
    },
    {
        "id": "12_etf_event_radar",
        "name": "策略 12：全台 ETF 跨市場折溢價與到價確認雷達",
        "script": ROOT_DIR / "strategies" / "12_etf_event_radar" / "intraday_scanner.py",
        "cwd": ROOT_DIR / "strategies" / "12_etf_event_radar"
    },
]

def run_intraday_single(strat, extra_args):
    print("\n" + "=" * 70)
    print(f"🚀 [執行到價掃描] {strat['name']}")
    print(f"📁 腳本位置: {strat['script']}")
    print("=" * 70)

    if not strat['script'].exists():
        print(f"[X] 找不到腳本: {strat['script']}")
        return False

    cmd = [sys.executable, str(strat['script'])] + extra_args

    try:
        ret = subprocess.run(cmd, cwd=strat['cwd'], check=True)
        print(f"[✓] {strat['name']} 執行成功！")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[!] {strat['name']} 執行失敗，代碼: {e.returncode}")
        return False
    except Exception as e:
        print(f"[!] 發生未預期錯誤: {e}")
        return False

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))
_WEB_TRIGGER_LOCK = threading.Lock()


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        try:
            import line_sender
            env_map = line_sender.load_env()
            if isinstance(env_map, dict):
                raw = env_map.get(name)
        except Exception:
            raw = None
    if raw is None or str(raw).strip() == "":
        val = default
    else:
        try:
            val = int(str(raw).strip())
        except Exception:
            val = default
    if val < lo:
        return lo
    if val > hi:
        return hi
    return val


def _default_kill_tree(pid: int, proc=None) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass


def _default_popen(cmd, cwd=None, env=None):
    if os.name == "nt":
        return subprocess.Popen(cmd, cwd=cwd, env=env)
    return subprocess.Popen(cmd, cwd=cwd, env=env, start_new_session=True)


def run_once_parallel(
    targets,
    *,
    now_fn=None,
    popen_fn=None,
    sleep_fn=None,
    kill_tree_fn=None,
    concurrency=None,
    stagger=None,
    child_timeout=None,
) -> dict:
    import time
    import intraday_speed
    import mis_shared_gate

    _now = now_fn or intraday_speed.taipei_now
    _sleep = sleep_fn or time.sleep
    _popen = popen_fn or _default_popen

    def _kill(pid: int, proc=None):
        if kill_tree_fn is not None:
            kill_tree_fn(pid)
        else:
            _default_kill_tree(pid, proc)

    target_list = list(targets or [])
    now0 = intraday_speed.to_taipei(_now())
    ok_win, reason_win = intraday_speed.is_runner_window(now0)
    if not ok_win:
        print(f"[盤中時段保護] {reason_win}，未啟動任何單次掃描子行程。")
        return {
            "started": 0,
            "succeeded": 0,
            "failed": 0,
            "timed_out": 0,
            "skipped_window": len(target_list),
            "reason": reason_win,
        }

    eff_conc = (
        _env_int("TW_INTRADAY_ONCE_CONCURRENCY", 3, 1, 6)
        if concurrency is None
        else max(1, min(6, int(concurrency)))
    )
    eff_stagger = float(
        intraday_speed.get_int("TW_SCANNER_STAGGER_SEC") if stagger is None else stagger
    )
    eff_timeout = float(
        _env_int("TW_INTRADAY_ONCE_TIMEOUT_SEC", 180, 30, 900)
        if child_timeout is None
        else child_timeout
    )

    start_date = now0.date()
    bootstrap_path = ROOT_DIR / "intraday_child_bootstrap.py"
    ledger_path = str(intraday_speed.ledger_dir())
    gate_path = str(mis_shared_gate.shared_gate_dir())

    queue = list(target_list)
    active = []
    started = 0
    succeeded = 0
    failed = 0
    timed_out = 0
    has_launched_any = False
    window_closed = False
    virtual_elapsed = 0.0

    def _reap_finished():
        nonlocal succeeded, failed
        still_running = []
        for item in active:
            rc = item["proc"].poll()
            if rc is None:
                still_running.append(item)
            else:
                if rc == 0:
                    succeeded += 1
                else:
                    failed += 1
        active[:] = still_running

    def _check_window_and_timeouts() -> bool:
        nonlocal window_closed, timed_out
        curr_dt = intraday_speed.to_taipei(_now())
        in_win, _ = intraday_speed.is_runner_window(curr_dt)
        if curr_dt.date() != start_date or curr_dt.time() >= datetime.time(13, 30, 0) or not in_win:
            window_closed = True
            for item in list(active):
                if not item["killed"]:
                    _kill(item["pid"], item["proc"])
                    item["killed"] = True
            active.clear()
            return False

        remaining = []
        for item in active:
            age_dt = (curr_dt - item["start_dt"]).total_seconds()
            age_virt = virtual_elapsed - item["start_virt"]
            age = max(age_dt, age_virt)
            if age >= eff_timeout and not item["killed"]:
                _kill(item["pid"], item["proc"])
                item["killed"] = True
                timed_out += 1
            else:
                remaining.append(item)
        active[:] = remaining
        return True

    while queue or active:
        _reap_finished()
        if not _check_window_and_timeouts():
            break

        while queue and not window_closed and len(active) < eff_conc:
            if has_launched_any and eff_stagger > 0:
                _sleep(eff_stagger)
                virtual_elapsed += eff_stagger
                _reap_finished()
                if not _check_window_and_timeouts():
                    break
                if len(active) >= eff_conc:
                    break

            curr_dt = intraday_speed.to_taipei(_now())
            in_win, _ = intraday_speed.is_runner_window(curr_dt)
            if curr_dt.date() != start_date or curr_dt.time() >= datetime.time(13, 30, 0) or not in_win:
                window_closed = True
                for item in list(active):
                    if not item["killed"]:
                        _kill(item["pid"], item["proc"])
                        item["killed"] = True
                active.clear()
                break

            strat = queue.pop(0)
            has_launched_any = True
            cmd = [sys.executable, str(bootstrap_path), str(strat["script"]), "--once"]
            child_env = os.environ.copy()
            child_env["TW_RADAR_STRICT_WINDOW"] = "1"
            child_env["TW_INTRADAY_LEDGER_DIR"] = ledger_path
            child_env["TW_MIS_SHARED_DIR"] = gate_path

            try:
                proc = _popen(cmd, cwd=str(strat["cwd"]), env=child_env)
                started += 1
                active.append({
                    "strat": strat,
                    "proc": proc,
                    "pid": getattr(proc, "pid", None),
                    "start_dt": curr_dt,
                    "start_virt": virtual_elapsed,
                    "killed": False,
                })
            except Exception as exc:
                print(f"[!] 啟動失敗 ({strat.get('id', 'unknown')}): {type(exc).__name__}")
                failed += 1

        _reap_finished()
        if not active and (not queue or window_closed):
            break

        if active:
            _sleep(1)
            virtual_elapsed += 1.0
            _reap_finished()
            if not _check_window_and_timeouts():
                break

    skipped_window = len(queue)
    summary = {
        "started": started,
        "succeeded": succeeded,
        "failed": failed,
        "timed_out": timed_out,
        "skipped_window": skipped_window,
    }
    print(
        f"[盤中單次平行總結] 啟動: {started}, 成功: {succeeded}, 失敗: {failed}, "
        f"逾時: {timed_out}, 未啟動: {skipped_window}"
    )
    return summary


def web_trigger_once(
    *,
    now_fn=None,
    runner=None,
    timeout_sec: float = 900.0,
    popen_fn=None,
    kill_tree_fn=None,
) -> tuple[bool, str]:
    import intraday_speed

    _now = now_fn or intraday_speed.taipei_now
    now_dt = intraday_speed.to_taipei(_now())
    ok_win, _ = intraday_speed.is_runner_window(now_dt)
    if not ok_win:
        return False, "非盤中時段（台北 08:58–13:30 交易日），未啟動盤中掃描"

    if not _WEB_TRIGGER_LOCK.acquire(blocking=False):
        return False, "上一輪盤中掃描仍在執行，未重複啟動"

    conc = _env_int("TW_INTRADAY_ONCE_CONCURRENCY", 3, 1, 6)
    eff_timeout = float(timeout_sec)
    started_iso = now_dt.isoformat()
    start_msg = f"已在背景啟動全策略盤中到價掃描（平行上限 {conc} 支）"
    _update_web_trigger_status(
        status="running",
        timed_out=False,
        timeout_sec=eff_timeout,
        pid=None,
        started_at=started_iso,
        finished_at=None,
        message=start_msg,
    )

    def _finish_iso() -> str:
        return intraday_speed.to_taipei(_now()).isoformat()

    def _bg():
        try:
            if runner is not None:
                runner()
                _update_web_trigger_status(
                    status="completed",
                    timed_out=False,
                    finished_at=_finish_iso(),
                    message="盤中掃描完成",
                )
                return

            _popen = popen_fn or _default_popen
            _kill = kill_tree_fn or _default_kill_tree
            proc = _popen(
                [sys.executable, str(Path(__file__).resolve()), "--once"],
                cwd=str(ROOT_DIR),
                env=os.environ.copy(),
            )
            pid = getattr(proc, "pid", None)
            _update_web_trigger_status(pid=pid)

            try:
                proc.wait(timeout=eff_timeout)
                _update_web_trigger_status(
                    status="completed",
                    timed_out=False,
                    finished_at=_finish_iso(),
                    message=f"盤中掃描完成 (PID={pid})",
                )
            except subprocess.TimeoutExpired:
                if proc.poll() is not None:
                    _update_web_trigger_status(
                        status="completed",
                        timed_out=False,
                        finished_at=_finish_iso(),
                        message=f"盤中掃描於逾時邊界完成 (PID={pid})",
                    )
                    return

                kill_err_note = ""
                if pid is not None:
                    try:
                        _kill(pid, proc)
                    except ProcessLookupError:
                        pass
                    except Exception as ke:
                        kill_err_note = f"；殺樹異常: {ke}"
                try:
                    proc.wait()
                except Exception:
                    pass

                timeout_msg = (
                    f"盤中掃描逾時（{eff_timeout:g} 秒），已終止子程式樹 PID={pid}"
                    + kill_err_note
                )
                _write_web_trigger_log(timeout_msg)
                _update_web_trigger_status(
                    status="timeout",
                    timed_out=True,
                    finished_at=_finish_iso(),
                    message=timeout_msg,
                )
        except Exception as exc:
            err_msg = f"盤中掃描執行異常: {exc}"
            _write_web_trigger_log(err_msg)
            _update_web_trigger_status(
                status="error",
                timed_out=False,
                finished_at=_finish_iso(),
                message=err_msg,
            )
        finally:
            try:
                _WEB_TRIGGER_LOCK.release()
            except RuntimeError:
                pass

    t = threading.Thread(target=_bg, daemon=True)
    t.start()
    return True, start_msg


def main():
    parser = argparse.ArgumentParser(description="台股總指揮中心 - 全策略盤中到價即時雷達總調度")
    parser.add_argument("--interval", type=int, default=60, help="輪詢間隔秒數 (預設 60 秒)")
    parser.add_argument("--once", action="store_true", help="執行單次快篩巡邏後結束")
    parser.add_argument("--test-push", "--force-test", dest="test_push", action="store_true", help="一鍵發送全策略模擬到價快訊至 LINE 測試連線")
    parser.add_argument("--strategy", type=str, default=None, help="指定單一策略 (01, 02, 03, 04)")
    parser.add_argument("--force", action="store_true", help="強制啟動雷達，忽略台股休市守衛檢查")
    args = parser.parse_args()

    now_tw = datetime.datetime.now(TZ_TW)
    print(f"============================================================")
    print(f" 🎯 台股量化策略總指揮中心 - 全策略盤中到價即時雷達")
    print(f" 🕒 啟動時間: {now_tw.strftime('%Y-%m-%d %H:%M:%S')} (台灣時間)")
    print(f"============================================================")

    # 1. 測試推播連線模式：放行測試
    if args.test_push:
        pass
    else:
        # 2. 休市守衛檢查 (若帶入 --force 僅略過休市日，不可略過收盤時段)
        from market_schedule import check_market_open_or_exit, is_intraday_window
        if not args.force:
            if not check_market_open_or_exit(context_name="全策略盤中到價即時雷達", allow_force=False):
                return

        # 3. 中央盤中時段守衛 (13:35 收盤後絕對不准啟動任何 scanner)
        # allow_premarket_runner=True 允許 08:58~08:59 提早啟動以供各 scanner 暖機等待開盤
        # 注意：--force 不准用來略過收盤後時段守衛！
        in_window, window_reason = is_intraday_window(now_tw, allow_premarket_runner=True)
        if not in_window:
            print(f"\n============================================================")
            print(f"🛑 [盤中時段保護] {window_reason}")
            print(f"📌 今日盤中交易已結束 (13:35 收盤)，調度器安全退出，不啟動任何策略 Scanner 行程。")
            print(f"============================================================\n")
            return  # 成功碼 0 退出，不要當成失敗

    targets = INTRADAY_STRATEGIES
    if args.strategy:
        targets = [s for s in INTRADAY_STRATEGIES if args.strategy in s['id']]
        if not targets:
            print(f"[!] 找不到符合 '{args.strategy}' 的策略！")
            return

    extra_args = []
    if args.test_push:
        extra_args.append("--test-push")
    elif args.once:
        extra_args.append("--once")
    else:
        extra_args.extend(["--interval", str(args.interval)])

    # 單次巡邏或推播測試
    if args.test_push:
        success = 0
        for strat in targets:
            if run_intraday_single(strat, extra_args):
                success += 1

        print("\n" + "=" * 70)
        print(f"🏁 全策略到價調度總結: 完成 {success}/{len(targets)} 項策略。")
        print("=" * 70)
    elif args.once:
        run_once_parallel(targets)
    else:
        # 常駐輪詢監控模式：平行啟動所有策略雷達行程，各自獨立盯盤至 13:35
        print(f"\n[🚀] 平行啟動全策略 ({len(targets)} 項) 盤中即時雷達行程...")
        processes = []
        for strat in targets:
            cmd = [sys.executable, str(strat['script'])] + extra_args
            p = subprocess.Popen(cmd, cwd=strat['cwd'])
            processes.append((strat['name'], p))
            print(f"  • [已在線] {strat['name']} (PID: {p.pid})")

        print("\n[*] 全策略盤中到價雷達已全數在線監控中，持續盯盤至 13:35 收盤 (按 Ctrl+C 可停止)...")
        try:
            for name, p in processes:
                p.wait()
            print("\n[✓] 今日盤中監控圓滿收工，所有策略雷達已自動退出。")
        except KeyboardInterrupt:
            print("\n[🛑] 收到中斷信號，正在停止所有策略雷達進程...")
            for name, p in processes:
                try:
                    p.terminate()
                except Exception:
                    pass
            for name, p in processes:
                try:
                    p.wait()
                except Exception:
                    pass
            print("[✓] 所有策略雷達已完全終止。")

if __name__ == '__main__':
    main()
