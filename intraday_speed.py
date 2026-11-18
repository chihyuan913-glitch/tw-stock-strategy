# -*- coding: utf-8 -*-
"""
盤中速度與嚴格時段控制核心模組 (Intraday Speed & Strict Guard)
提供設定讀取、台北時段判斷、交易日快取、優先池載入、分級排程、MIS 限流退避與跨行程去重帳本。
"""

from __future__ import annotations

import csv
import datetime
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, Union

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

RADAR_PUSH_START = datetime.time(9, 0, 0)
RADAR_PUSH_END = datetime.time(13, 30, 0)
RUNNER_START = datetime.time(8, 58, 0)
RUNNER_END = datetime.time(13, 30, 0)

_BASE_DIR = Path(__file__).resolve().parent

_INT_SPEC: Dict[str, Tuple[int, Any, Any]] = {
    "TW_RADAR_PRIORITY_INTERVAL_SEC": (5, 3, 60),
    "TW_RADAR_NORMAL_INTERVAL_SEC": (30, "TW_RADAR_PRIORITY_INTERVAL_SEC", 300),
    "TW_MIS_MAX_REQUESTS": (3, 1, 10),
    "TW_MIS_WINDOW_SEC": (5, 1, 60),
    "TW_MIS_BACKOFF_BASE_SEC": (10, 2, 60),
    "TW_MIS_BACKOFF_MAX_SEC": (120, "TW_MIS_BACKOFF_BASE_SEC", 600),
    "TW_MIS_TIMEOUT_SEC": (5, 1, 10),
    "TW_RADAR_QUOTE_MAX_AGE_SEC": (15, 3, 120),
    "TW_ORDER_RISK_BUDGET_TWD": (10000, 1000, 1000000),
    "TW_ORDER_MAX_LOTS": (10, 1, 500),
    "TW_ORDER_MAX_NOTIONAL_TWD": (1000000, 10000, 100000000),
    "TW_SCANNER_INTERVAL_SEC": (60, 30, 300),
    "TW_SCANNER_STAGGER_SEC": (5, 0, 30),
    "TW_INTRADAY_RUNNER_LOCK_PORT": (18771, 1024, 65535),
}

_TRADING_DAY_CACHE: Dict[datetime.date, bool] = {}
_ADVISOR_CACHE: Dict[str, Tuple[Tuple[int, int], Dict[str, Dict[str, Any]]]] = {}
_ADVISOR_LOCK = threading.Lock()
_LIMITER_LOCK = threading.Lock()
_MIS_LIMITER: Optional["MisRateLimiter"] = None


def _load_env_safe() -> Dict[str, str]:
    try:
        import line_sender

        env = line_sender.load_env()
        return env if isinstance(env, dict) else {}
    except Exception:
        return {}


def _get_raw_setting(name: str) -> Optional[str]:
    val = os.environ.get(name)
    if val is not None and str(val).strip() != "":
        return str(val).strip()
    env = _load_env_safe()
    val2 = env.get(name)
    if val2 is not None and str(val2).strip() != "":
        return str(val2).strip()
    return None


def get_int(name: str) -> int:
    if name not in _INT_SPEC:
        raise KeyError(f"Unknown setting: {name}")
    default_val, lo_spec, hi_spec = _INT_SPEC[name]
    lo = get_int(lo_spec) if isinstance(lo_spec, str) else int(lo_spec)
    hi = get_int(hi_spec) if isinstance(hi_spec, str) else int(hi_spec)

    raw = _get_raw_setting(name)
    if raw is None:
        val = default_val
    else:
        try:
            val = int(raw)
        except (TypeError, ValueError):
            val = default_val

    if val < lo:
        val = lo
    if val > hi:
        val = hi
    return val


def ledger_dir() -> Path:
    raw = _get_raw_setting("TW_INTRADAY_LEDGER_DIR")
    if raw:
        return Path(raw)
    return _BASE_DIR / "cache" / "intraday_push_ledger"


def worker_sleep_seconds() -> int:
    return get_int("TW_RADAR_PRIORITY_INTERVAL_SEC")


def taipei_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).astimezone(TZ_TW)


def mono_now() -> float:
    return time.monotonic()


def to_taipei(dt: Optional[datetime.datetime]) -> datetime.datetime:
    if dt is None:
        return taipei_now()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ_TW)
    return dt.astimezone(TZ_TW)


def is_trading_day(d: Union[datetime.date, datetime.datetime]) -> bool:
    if isinstance(d, datetime.datetime):
        dt_tw = to_taipei(d)
        day_date = dt_tw.date()
    elif isinstance(d, datetime.date):
        day_date = d
        dt_tw = datetime.datetime.combine(day_date, datetime.time(10, 0, 0), TZ_TW)
    else:
        raise TypeError("Expected date or datetime")

    if day_date.weekday() >= 5:
        return False

    if day_date in _TRADING_DAY_CACHE:
        return _TRADING_DAY_CACHE[day_date]

    res = False
    try:
        import market_schedule

        is_open, _ = market_schedule.is_market_open_today(dt_tw, check_realtime=False)
        res = bool(is_open)
    except Exception:
        try:
            import market_schedule

            is_open, _ = market_schedule.is_market_open_today(
                dt_tw, check_emergency=False, check_realtime=False
            )
            res = bool(is_open)
        except Exception:
            res = day_date.weekday() < 5

    _TRADING_DAY_CACHE[day_date] = res
    return res


def is_radar_push_allowed(now: Optional[datetime.datetime] = None) -> Tuple[bool, str]:
    now_tw = to_taipei(now)
    if not is_trading_day(now_tw):
        return False, f"非台北交易日 ({now_tw.strftime('%Y-%m-%d')})"
    t = now_tw.time()
    if t < RADAR_PUSH_START:
        return False, f"尚未開盤 ({now_tw.strftime('%H:%M:%S')} < 09:00:00)"
    if t >= RADAR_PUSH_END:
        return False, f"已收盤 ({now_tw.strftime('%H:%M:%S')} >= 13:30:00)"
    return True, f"台北盤中推播時段內 ({now_tw.strftime('%H:%M:%S')})"


def is_runner_window(now: Optional[datetime.datetime] = None) -> Tuple[bool, str]:
    now_tw = to_taipei(now)
    if not is_trading_day(now_tw):
        return False, f"非台北交易日 ({now_tw.strftime('%Y-%m-%d')})"
    t = now_tw.time()
    if t < RUNNER_START:
        return False, f"尚未達啟動時段 ({now_tw.strftime('%H:%M:%S')} < 08:58:00)"
    if t >= RUNNER_END:
        return False, f"已達收盤停止時段 ({now_tw.strftime('%H:%M:%S')} >= 13:30:00)"
    return True, f"台北排程執行時段內 ({now_tw.strftime('%H:%M:%S')})"


def load_advisor(csv_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    path = Path(csv_path) if csv_path is not None else (_BASE_DIR / "lab" / "data" / "tomorrow_trading_advisor.csv")
    if not path.exists():
        return {}
    try:
        st = path.stat()
    except OSError:
        return {}

    key = str(path.resolve())
    sig = (st.st_mtime_ns, st.st_size)
    with _ADVISOR_LOCK:
        cached = _ADVISOR_CACHE.get(key)
        if cached is not None and cached[0] == sig:
            return dict(cached[1])

        rows: Dict[str, Dict[str, Any]] = {}
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sym = str(row.get("symbol") or "").strip()
                    if sym:
                        rows[sym] = dict(row)
        except Exception:
            return {}

        _ADVISOR_CACHE[key] = (sig, rows)
        return dict(rows)


def priority_codes(csv_path: Optional[Path] = None) -> Set[str]:
    rows = load_advisor(csv_path=csv_path)
    result: Set[str] = set()
    for sym, row in rows.items():
        action = str(row.get("tomorrow_action") or "").strip()
        if "優先買進" in action or "STRONG BUY" in action.upper():
            result.add(sym)
    return result


class RadarScheduler:
    """盤中分級掃描排程器：優先買進依優先間隔，其餘依一般間隔。"""

    def __init__(
        self,
        priority_interval: Optional[int] = None,
        normal_interval: Optional[int] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._priority_interval = priority_interval
        self._normal_interval = normal_interval
        self._clock = clock
        self._last_all: Optional[float] = None
        self._last_priority: Optional[float] = None

    def _now(self) -> float:
        return self._clock() if self._clock is not None else mono_now()

    def _get_intervals(self) -> Tuple[int, int]:
        p = self._priority_interval if self._priority_interval is not None else get_int("TW_RADAR_PRIORITY_INTERVAL_SEC")
        n = self._normal_interval if self._normal_interval is not None else get_int("TW_RADAR_NORMAL_INTERVAL_SEC")
        if n < p:
            n = p
        return p, n

    def due_codes(self, all_codes: Iterable[str], priority_set: Optional[Set[str]] = None) -> List[str]:
        codes_list = [str(c).strip() for c in all_codes if str(c).strip()]
        if not codes_list:
            return []
        pset = priority_set or set()
        now = self._now()
        p_int, n_int = self._get_intervals()

        if self._last_all is None or (now - self._last_all) >= n_int:
            self._last_all = now
            self._last_priority = now
            return codes_list

        if pset and (self._last_priority is None or (now - self._last_priority) >= p_int):
            due = [c for c in codes_list if c in pset]
            self._last_priority = now
            return due

        return []


class MisRateLimiter:
    """MIS 非阻塞滑動視窗限流與指數退避器（執行緒安全，不 sleep）。"""

    def __init__(
        self,
        max_requests: Optional[int] = None,
        window_sec: Optional[int] = None,
        backoff_base: Optional[int] = None,
        backoff_max: Optional[int] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._max_requests = max_requests
        self._window_sec = window_sec
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._clock = clock
        self._lock = threading.Lock()
        self._timestamps: deque[float] = deque()
        self._fail_count: int = 0
        self._backoff_until: float = 0.0

    def _now(self) -> float:
        return self._clock() if self._clock is not None else mono_now()

    def _params(self) -> Tuple[int, int, int, int]:
        mr = self._max_requests if self._max_requests is not None else get_int("TW_MIS_MAX_REQUESTS")
        ws = self._window_sec if self._window_sec is not None else get_int("TW_MIS_WINDOW_SEC")
        bb = self._backoff_base if self._backoff_base is not None else get_int("TW_MIS_BACKOFF_BASE_SEC")
        bm = self._backoff_max if self._backoff_max is not None else get_int("TW_MIS_BACKOFF_MAX_SEC")
        if bm < bb:
            bm = bb
        return mr, ws, bb, bm

    def acquire(self) -> bool:
        with self._lock:
            now = self._now()
            if now < self._backoff_until:
                return False
            mr, ws, _, _ = self._params()
            cutoff = now - ws
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= mr:
                return False
            self._timestamps.append(now)
            return True

    def report_failure(self) -> float:
        with self._lock:
            now = self._now()
            _, _, bb, bm = self._params()
            self._fail_count += 1
            delay = min(bb * (2 ** (self._fail_count - 1)), bm)
            self._backoff_until = now + delay
            return float(delay)

    def report_success(self) -> None:
        with self._lock:
            self._fail_count = 0
            self._backoff_until = 0.0


def get_mis_limiter() -> MisRateLimiter:
    global _MIS_LIMITER
    with _LIMITER_LOCK:
        if _MIS_LIMITER is None:
            _MIS_LIMITER = MisRateLimiter()
        return _MIS_LIMITER


def _sanitize_token(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "", str(s or ""))


def _ledger_file(base_dir: Path, code: str, event_type: str, day: str) -> Path:
    safe_day = re.sub(r"[^0-9-]", "", str(day or ""))[:10] or "unknown"
    safe_code = _sanitize_token(code) or "UNKNOWN"
    safe_event = _sanitize_token(event_type) or "EVENT"
    return Path(base_dir) / safe_day / f"{safe_code}_{safe_event}.flag"


def claim_push(base_dir: Path, code: str, event_type: str, day: str) -> bool:
    flag_path = _ledger_file(base_dir, code, event_type, day)
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(flag_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, b"1")
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        return False


def release_push(base_dir: Path, code: str, event_type: str, day: str) -> bool:
    flag_path = _ledger_file(base_dir, code, event_type, day)
    try:
        os.unlink(str(flag_path))
        return True
    except FileNotFoundError:
        return False
