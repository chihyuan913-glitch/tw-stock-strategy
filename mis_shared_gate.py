# -*- coding: utf-8 -*-
import email.message
import io
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_SHARED_GATE_DIR = Path(__file__).resolve().parent / "cache" / "mis_shared_gate"


def _load_dotenv_dict() -> dict:
    try:
        import line_sender
        res = line_sender.load_env()
        return res if isinstance(res, dict) else {}
    except Exception:
        return {}


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        raw = _load_dotenv_dict().get(name)
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


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        raw = _load_dotenv_dict().get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip()


def shared_gate_dir() -> Path:
    return Path(_env_str("TW_MIS_SHARED_DIR", str(DEFAULT_SHARED_GATE_DIR)))


def _speed_int(name: str, fallback: int) -> int:
    try:
        import intraday_speed
        return int(intraday_speed.get_int(name))
    except Exception:
        return fallback


def _sanitize_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", key.strip())


def _item_cache_key(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    ex = str(item.get("ex") or "").strip()
    ch = str(item.get("ch") or "").strip()
    if not ch:
        return ""
    if ex and not ch.startswith(f"{ex}_"):
        return f"{ex}_{ch}"
    return ch


class _BytesResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self._buf = io.BytesIO(body)
        self.status = int(status)
        self._headers = email.message.Message()
        for k, v in (headers or {"Content-Type": "application/json; charset=utf-8"}).items():
            self._headers[k] = str(v)

    def read(self, amt: int | None = None) -> bytes:
        return self._buf.read() if amt is None else self._buf.read(amt)

    def getcode(self) -> int:
        return self.status

    def info(self):
        return self._headers

    def close(self) -> None:
        self._buf.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class _FileLock:
    def __init__(self, lock_path: Path, timeout_sec: float = 2.0):
        self.lock_path = lock_path
        self.timeout_sec = timeout_sec
        self._fh = None

    def __enter__(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "a+b")
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"0")
            fh.flush()
        fh.seek(0)
        deadline = time.monotonic() + self.timeout_sec
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fh = fh
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    try:
                        fh.close()
                    except Exception:
                        pass
                    self._fh = None
                    return False
                time.sleep(0.02)

    def __exit__(self, exc_type, exc_val, exc_tb):
        fh = self._fh
        self._fh = None
        if fh is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass
        return False


class SharedMisGate:
    def __init__(
        self,
        gate_dir: str | Path | None = None,
        *,
        max_requests: int | None = None,
        window_sec: int | float | None = None,
        backoff_base: int | float | None = None,
        backoff_max: int | float | None = None,
        clock=None,
    ):
        self.gate_dir = Path(gate_dir) if gate_dir is not None else shared_gate_dir()
        self.max_requests = (
            _env_int("TW_MIS_SHARED_MAX_REQUESTS", 2, 1, 10)
            if max_requests is None
            else max(1, int(max_requests))
        )
        self.window_sec = float(_speed_int("TW_MIS_WINDOW_SEC", 5) if window_sec is None else window_sec)
        self.backoff_base = float(_speed_int("TW_MIS_BACKOFF_BASE_SEC", 10) if backoff_base is None else backoff_base)
        self.backoff_max = float(_speed_int("TW_MIS_BACKOFF_MAX_SEC", 120) if backoff_max is None else backoff_max)
        self._clock = clock or time.time
        self.state_path = self.gate_dir / "state.json"
        self.lock_path = self.gate_dir / "state.lock"
        self.quotes_dir = self.gate_dir / "quotes"

    def _read_state_unlocked(self) -> dict:
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("state not dict")
            reqs = data.get("requests")
            if not isinstance(reqs, list):
                raise ValueError("requests not list")
            reqs_clean = [float(x) for x in reqs]
            failures = max(0, int(data.get("failures", 0)))
            backoff_until = float(data.get("backoff_until", 0.0))
            return {"requests": reqs_clean, "failures": failures, "backoff_until": backoff_until}
        except Exception:
            state = {"requests": [], "failures": 0, "backoff_until": 0.0}
            self._write_state_unlocked(state)
            return state

    def _write_state_unlocked(self, state: dict) -> None:
        self.gate_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.gate_dir / f"state.json.tmp.{os.getpid()}"
        payload = json.dumps(state, ensure_ascii=False)
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, self.state_path)

    def acquire(self) -> bool:
        with _FileLock(self.lock_path) as locked:
            if not locked:
                return False
            now_ts = float(self._clock())
            state = self._read_state_unlocked()
            reqs = [t for t in state["requests"] if 0.0 <= (now_ts - t) < self.window_sec]
            state["requests"] = reqs
            if now_ts < state["backoff_until"] or len(reqs) >= self.max_requests:
                self._write_state_unlocked(state)
                return False
            reqs.append(now_ts)
            state["requests"] = reqs
            self._write_state_unlocked(state)
            return True

    def report_failure(self) -> float:
        with _FileLock(self.lock_path) as locked:
            if not locked:
                return 0.0
            now_ts = float(self._clock())
            state = self._read_state_unlocked()
            n = int(state.get("failures", 0)) + 1
            delay = min(self.backoff_base * (2 ** (n - 1)), self.backoff_max)
            state["failures"] = n
            state["backoff_until"] = now_ts + delay
            self._write_state_unlocked(state)
            return delay

    def report_success(self) -> None:
        with _FileLock(self.lock_path) as locked:
            if not locked:
                return
            state = self._read_state_unlocked()
            state["failures"] = 0
            state["backoff_until"] = 0.0
            self._write_state_unlocked(state)

    def cache_put(self, items: list, now: float | None = None) -> None:
        cache_put(items, self._clock() if now is None else now, gate_dir=self.gate_dir)

    def cache_get(self, keys: list[str], max_age: float | None = None, now: float | None = None) -> list | None:
        age = _env_int("TW_MIS_SHARED_CACHE_SEC", 3, 0, 30) if max_age is None else max_age
        return cache_get(keys, age, self._clock() if now is None else now, gate_dir=self.gate_dir)


def cache_put(items: list, now: float | None = None, *, gate_dir: str | Path | None = None) -> None:
    if not isinstance(items, list):
        return
    now_ts = float(time.time() if now is None else now)
    base_dir = Path(gate_dir) if gate_dir is not None else shared_gate_dir()
    q_dir = base_dir / "quotes"
    q_dir.mkdir(parents=True, exist_ok=True)
    for item in items:
        key = _item_cache_key(item)
        if not key:
            continue
        safe = _sanitize_key(key)
        tmp_f = q_dir / f"{safe}.json.tmp.{os.getpid()}"
        dst_f = q_dir / f"{safe}.json"
        tmp_f.write_text(json.dumps({"ts": now_ts, "item": item}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_f, dst_f)


def cache_get(keys: list[str], max_age: float, now: float | None = None, *, gate_dir: str | Path | None = None) -> list | None:
    if not keys or max_age <= 0:
        return None
    now_ts = float(time.time() if now is None else now)
    base_dir = Path(gate_dir) if gate_dir is not None else shared_gate_dir()
    q_dir = base_dir / "quotes"
    out = []
    for k in keys:
        safe = _sanitize_key(k)
        f_path = q_dir / f"{safe}.json"
        if not f_path.exists():
            return None
        try:
            payload = json.loads(f_path.read_text(encoding="utf-8"))
            ts = float(payload["ts"])
            item = payload["item"]
            if (now_ts - ts) > float(max_age) or (now_ts - ts) < -1.0 or not isinstance(item, dict):
                return None
            out.append(item)
        except Exception:
            return None
    return out


def _extract_url(url_or_req) -> str:
    if hasattr(url_or_req, "full_url"):
        return str(url_or_req.full_url)
    return str(url_or_req)


def _is_mis_quote_url(url_str: str) -> tuple[bool, list[str]]:
    try:
        parsed = urllib.parse.urlsplit(url_str)
        host = (parsed.hostname or "").lower()
        if host != "mis.twse.com.tw" or "getStockInfo.jsp" not in parsed.path:
            return False, []
        qs = urllib.parse.parse_qs(parsed.query)
        ex_ch = (qs.get("ex_ch") or [""])[0]
        keys = [part.strip() for part in ex_ch.split("|") if part.strip()]
        return True, keys
    except Exception:
        return False, []


def install_urlopen_patch(gate: SharedMisGate | None = None, *, opener=None, sleep=time.sleep, now=time.time):
    orig_urlopen = urllib.request.urlopen
    active_gate = gate if gate is not None else SharedMisGate(clock=now)
    real_opener = opener if opener is not None else orig_urlopen

    def _patched_urlopen(url_or_req, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *args, **kwargs):
        url_str = _extract_url(url_or_req)
        is_mis, keys = _is_mis_quote_url(url_str)
        if not is_mis:
            if timeout is socket._GLOBAL_DEFAULT_TIMEOUT:
                return real_opener(url_or_req, *([data] if data is not None else []), *args, **kwargs)
            if data is not None:
                return real_opener(url_or_req, data, timeout, *args, **kwargs)
            return real_opener(url_or_req, timeout=timeout, *args, **kwargs)

        cache_sec = _env_int("TW_MIS_SHARED_CACHE_SEC", 3, 0, 30)
        if keys and cache_sec > 0:
            cached = cache_get(keys, cache_sec, now(), gate_dir=active_gate.gate_dir)
            if cached is not None:
                body = json.dumps({"msgArray": cached, "rtcode": "0000", "rtmessage": "OK"}, ensure_ascii=False).encode("utf-8")
                return _BytesResponse(body, status=200)

        max_wait = float(_env_int("TW_MIS_SHARED_MAX_WAIT_SEC", 8, 0, 30))
        waited = 0.0
        while not active_gate.acquire():
            if waited >= max_wait or max_wait <= 0.0:
                raise urllib.error.URLError("MIS 共用限流，略過本次請求")
            step = min(0.2, max(0.01, max_wait - waited))
            sleep(step)
            waited += step

        eff_timeout = _speed_int("TW_MIS_TIMEOUT_SEC", 6) if timeout is socket._GLOBAL_DEFAULT_TIMEOUT or timeout is None else timeout
        try:
            if data is not None:
                resp = real_opener(url_or_req, data, eff_timeout, *args, **kwargs)
            else:
                try:
                    resp = real_opener(url_or_req, timeout=eff_timeout, *args, **kwargs)
                except TypeError:
                    resp = real_opener(url_or_req, *args, **kwargs)
            raw = resp.read() if hasattr(resp, "read") else bytes(resp)
            status = getattr(resp, "status", None) or (resp.getcode() if hasattr(resp, "getcode") else 200)
        except urllib.error.HTTPError as e:
            if e.code in (429, 403, 503):
                active_gate.report_failure()
            raise
        except (urllib.error.URLError, socket.timeout, TimeoutError):
            active_gate.report_failure()
            raise

        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        try:
            parsed_json = json.loads(raw.decode("utf-8", errors="replace"))
            if not isinstance(parsed_json, dict):
                raise ValueError("MIS payload not dict")
            active_gate.report_success()
            msg_arr = parsed_json.get("msgArray")
            if isinstance(msg_arr, list) and msg_arr:
                cache_put(msg_arr, now(), gate_dir=active_gate.gate_dir)
        except Exception:
            active_gate.report_failure()

        return _BytesResponse(raw, status=int(status or 200))

    urllib.request.urlopen = _patched_urlopen

    def _restore():
        if urllib.request.urlopen is _patched_urlopen:
            urllib.request.urlopen = orig_urlopen

    return _restore
