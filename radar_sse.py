# -*- coding: utf-8 -*-
import asyncio
import datetime
import inspect
import json
import os
import re
import threading
import time
from pathlib import Path

_CODE_RE = re.compile(r"^[0-9A-Z]{4,6}$")
_EVENT_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")


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


def _parse_flag_filename(filename: str) -> tuple[str, str] | None:
    if not filename.endswith(".flag"):
        return None
    stem = filename[:-5]
    if "_" not in stem:
        return None
    code, _, event = stem.partition("_")
    if not _CODE_RE.fullmatch(code):
        return None
    if not _EVENT_RE.fullmatch(event):
        return None
    return code, event


def _list_flag_names(base_dir: str | Path, day_str: str) -> list[str]:
    day_dir = Path(base_dir) / day_str
    if not day_dir.is_dir():
        return []
    out = []
    try:
        with os.scandir(day_dir) as it:
            for entry in it:
                if entry.is_file() and entry.name.endswith(".flag"):
                    out.append(entry.name)
    except Exception:
        return []
    out.sort()
    return out


def _default_window_fn(now_dt) -> bool:
    import intraday_speed
    ok, _ = intraday_speed.is_radar_push_allowed(now_dt)
    return bool(ok)


class RadarSseHub:
    def __init__(
        self,
        *,
        ledger_dir_fn=None,
        now_fn=None,
        window_fn=None,
        max_clients: int | None = None,
        max_external: int | None = None,
        poll_sec: int | float | None = None,
        settle_sec: int | float | None = None,
        heartbeat_sec: int | float | None = None,
        max_lifetime_sec: int | float | None = None,
    ):
        import intraday_speed

        self._ledger_dir_fn = ledger_dir_fn or intraday_speed.ledger_dir
        self._now_fn = now_fn or intraday_speed.taipei_now
        self._window_fn = window_fn or _default_window_fn

        self.max_clients = (
            _env_int("TW_SSE_MAX_CLIENTS", 20, 1, 200)
            if max_clients is None
            else max(1, min(200, int(max_clients)))
        )
        raw_ext = (
            _env_int("TW_SSE_MAX_EXTERNAL", 5, 0, 50)
            if max_external is None
            else max(0, min(50, int(max_external)))
        )
        self.max_external = min(raw_ext, self.max_clients)
        self.poll_sec = (
            float(_env_int("TW_SSE_POLL_SEC", 2, 1, 10))
            if poll_sec is None
            else float(poll_sec)
        )
        self.settle_sec = (
            float(_env_int("TW_SSE_SETTLE_SEC", 12, 0, 60))
            if settle_sec is None
            else float(settle_sec)
        )
        self.heartbeat_sec = (
            float(_env_int("TW_SSE_HEARTBEAT_SEC", 15, 5, 60))
            if heartbeat_sec is None
            else float(heartbeat_sec)
        )
        self.max_lifetime_sec = (
            float(_env_int("TW_SSE_MAX_LIFETIME_SEC", 1800, 60, 7200))
            if max_lifetime_sec is None
            else float(max_lifetime_sec)
        )

        self._lock = threading.Lock()
        self.active_clients = 0
        self.active_external = 0

    def try_register(self, external: bool = False) -> bool:
        with self._lock:
            if self.active_clients >= self.max_clients:
                return False
            if external and self.active_external >= self.max_external:
                return False
            self.active_clients += 1
            if external:
                self.active_external += 1
            return True

    def unregister(self, external: bool = False) -> None:
        with self._lock:
            if self.active_clients > 0:
                self.active_clients -= 1
            if external and self.active_external > 0:
                self.active_external -= 1

    async def stream(
        self,
        is_disconnected,
        *,
        external: bool = False,
        sleep=asyncio.sleep,
        mono=time.monotonic,
    ):
        async def _check_disc() -> bool:
            if is_disconnected is None:
                return False
            res = is_disconnected()
            if inspect.isawaitable(res):
                return bool(await res)
            return bool(res)

        async def _do_sleep(sec: float) -> None:
            res = sleep(sec)
            if inspect.isawaitable(res):
                await res

        try:
            import intraday_speed
            start_mono = float(mono())
            last_ping_mono = start_mono
            seq = 0

            now_dt = intraday_speed.to_taipei(self._now_fn())
            curr_day = now_dt.strftime("%Y-%m-%d")
            in_win = bool(await asyncio.to_thread(self._window_fn, now_dt))

            yield "retry: 5000\n\n"
            last_win_state = "open" if in_win else "closed"
            yield f'event: status\ndata: {{"window":"{last_win_state}"}}\n\n'

            initial_flags = await asyncio.to_thread(
                _list_flag_names, self._ledger_dir_fn(), curr_day
            )
            seen_names: set[str] = set(initial_flags)
            pending: dict[str, dict] = {}

            while True:
                if await _check_disc():
                    break
                if (float(mono()) - start_mono) >= self.max_lifetime_sec:
                    break

                await _do_sleep(self.poll_sec)

                now_m = float(mono())
                now_dt = intraday_speed.to_taipei(self._now_fn())
                new_day = now_dt.strftime("%Y-%m-%d")
                in_win = bool(await asyncio.to_thread(self._window_fn, now_dt))

                if new_day != curr_day:
                    curr_day = new_day
                    pending.clear()
                    existing_new_day = await asyncio.to_thread(
                        _list_flag_names, self._ledger_dir_fn(), curr_day
                    )
                    seen_names = set(existing_new_day)

                curr_win_state = "open" if in_win else "closed"
                if curr_win_state != last_win_state:
                    last_win_state = curr_win_state
                    yield f'event: status\ndata: {{"window":"{curr_win_state}"}}\n\n'

                current_flags = set(
                    await asyncio.to_thread(_list_flag_names, self._ledger_dir_fn(), curr_day)
                )

                for fname in list(pending.keys()):
                    if fname not in current_flags:
                        del pending[fname]
                        continue
                    info = pending[fname]
                    if (now_m - info["first_mono"]) >= self.settle_sec:
                        del pending[fname]
                        seen_names.add(fname)
                        if (
                            info["first_in_win"]
                            and now_dt.time() < datetime.time(13, 31, 0)
                            and now_dt.date() == info["first_dt"].date()
                        ):
                            seq += 1
                            payload = json.dumps(
                                {
                                    "code": info["code"],
                                    "event": info["event"],
                                    "ts": info["first_dt"].isoformat(timespec="seconds"),
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            yield f"id: {seq}\nevent: radar_alert\ndata: {payload}\n\n"

                for fname in sorted(current_flags):
                    if fname in seen_names or fname in pending:
                        continue
                    parsed = _parse_flag_filename(fname)
                    if parsed is None:
                        seen_names.add(fname)
                        continue
                    if not in_win:
                        seen_names.add(fname)
                        continue
                    code, event = parsed
                    if self.settle_sec <= 0:
                        seen_names.add(fname)
                        if now_dt.time() < datetime.time(13, 31, 0):
                            seq += 1
                            payload = json.dumps(
                                {
                                    "code": code,
                                    "event": event,
                                    "ts": now_dt.isoformat(timespec="seconds"),
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            yield f"id: {seq}\nevent: radar_alert\ndata: {payload}\n\n"
                    else:
                        pending[fname] = {
                            "code": code,
                            "event": event,
                            "first_mono": now_m,
                            "first_dt": now_dt,
                            "first_in_win": True,
                        }

                if (now_m - last_ping_mono) >= self.heartbeat_sec:
                    last_ping_mono = now_m
                    yield ": ping\n\n"

                if (now_m - start_mono) >= self.max_lifetime_sec:
                    break
        finally:
            self.unregister(external=external)


class SlotGuardedStream:
    def __init__(self, hub, inner, *, external: bool = False):
        self._hub = hub
        self._inner = inner
        self._external = bool(external)
        self._lock = threading.Lock()
        self._started = False
        self._finalized = False

    @property
    def started(self) -> bool:
        with self._lock:
            return self._started

    @property
    def finalized(self) -> bool:
        with self._lock:
            return self._finalized

    def __aiter__(self):
        return self

    async def __anext__(self):
        with self._lock:
            if self._finalized:
                raise StopAsyncIteration
            self._started = True
        return await self._inner.__anext__()

    async def finalize(self) -> bool:
        with self._lock:
            if self._finalized:
                return False
            self._finalized = True
            was_started = self._started

        if not was_started:
            unreg = getattr(self._hub, "unregister", None)
            if callable(unreg):
                unreg(external=self._external)

        aclose_fn = getattr(self._inner, "aclose", None)
        if callable(aclose_fn):
            try:
                await aclose_fn()
            except Exception as exc:
                print(f"[SSE] 關閉串流例外: {type(exc).__name__}")
        return True
