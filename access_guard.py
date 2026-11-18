# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
from typing import Callable, Iterable, Optional

PROXY_HEADERS = (
    "cf-connecting-ip",
    "cf-ray",
    "cf-visitor",
    "cf-ipcountry",
    "cf-warp-tag-id",
    "cdn-loop",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
    "true-client-ip",
)
_PROXY_HEADER_SET = frozenset(PROXY_HEADERS)

ADMIN_HEADER = "x-tw-admin-key"
ADMIN_ENV = "TW_ADMIN_KEY"
ADMIN_MIN_LEN = 32
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
EXEMPT_WRITE_PATHS = frozenset({"/callback", "/api/heartbeat"})
PROTECTED_GET_PATHS = frozenset({
    "/api/lab/fusion/daily-advisor",
    "/api/lab/leaderboard",
})
LOCAL_ONLY_GET_PATHS = frozenset({"/logs"})

_FORBIDDEN_BODY = json.dumps(
    {"status": "forbidden", "message": "外網唯讀：此操作僅限本機執行"},
    ensure_ascii=False,
).encode("utf-8")

_REDACT_PATTERNS = (
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"\d{6,12}:[A-Za-z0-9_-]{25,}"),
    re.compile(r"\b[UCR][0-9a-f]{32}\b"),
    re.compile(r"-100\d{6,}"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"[A-Za-z0-9+/=_-]{32,}"),
    re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|channel[_-]?access)\s*[:=]\s*\S+"),
)


def _iter_headers(headers) -> Iterable[tuple[str, str]]:
    if not headers:
        return ()
    if isinstance(headers, dict):
        return ((str(k).lower(), str(v)) for k, v in headers.items())
    out = []
    for item in headers:
        k, v = item
        ks = k.decode("latin-1").lower() if isinstance(k, (bytes, bytearray)) else str(k).lower()
        vs = v.decode("latin-1") if isinstance(v, (bytes, bytearray)) else str(v)
        out.append((ks, vs))
    return out


def _extract_hostname(host_val: str) -> str:
    h = (host_val or "").strip().lower()
    if not h:
        return ""
    if h.startswith("["):
        end = h.find("]")
        if end != -1:
            return h[1:end]
        return h.strip("[]")
    if ":" in h:
        return h.split(":", 1)[0]
    return h


def is_external(scope: dict) -> bool:
    if scope.get("type") not in ("http", "websocket"):
        return False

    host_val = ""
    for name, val in _iter_headers(scope.get("headers")):
        if name in _PROXY_HEADER_SET:
            return True
        if name == "host":
            host_val = val

    client = scope.get("client")
    if client is None:
        return True

    client_host = client[0] if isinstance(client, (tuple, list)) and client else ""
    if client_host:
        try:
            ip = ipaddress.ip_address(client_host)
            if not ip.is_loopback:
                return True
        except ValueError:
            pass

    hostname = _extract_hostname(host_val)
    if hostname:
        try:
            hip = ipaddress.ip_address(hostname)
            if not hip.is_loopback:
                return True
        except ValueError:
            if "." in hostname and hostname != "localhost":
                return True

    return False


def admin_key_ok(headers, *, env=None, loader: Optional[Callable[[], dict]] = None) -> bool:
    env_map = os.environ if env is None else env
    expected = (env_map.get(ADMIN_ENV) or "").strip()
    if not expected:
        loaded = {}
        try:
            if loader is None:
                from line_sender import load_env
                loaded = load_env() or {}
            else:
                loaded = loader() or {}
        except Exception:
            loaded = {}
        if isinstance(loaded, dict):
            expected = str(loaded.get(ADMIN_ENV) or "").strip()

    if len(expected) < ADMIN_MIN_LEN:
        return False

    provided = ""
    for name, val in _iter_headers(headers):
        if name == ADMIN_HEADER:
            provided = val.strip()
            break

    if not provided:
        return False

    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def verify_line_signature(secret: str, raw_body: bytes, signature: Optional[str]) -> bool:
    if not secret or not signature:
        return False
    try:
        expected = base64.b64encode(
            hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
        )
        sig_bytes = signature.strip().encode("ascii", "strict")
        return hmac.compare_digest(expected, sig_bytes)
    except Exception:
        return False


def redact_text(text: str) -> str:
    if not text:
        return text
    out = str(text)
    for pat in _REDACT_PATTERNS:
        out = pat.sub("[已遮蔽]", out)
    return out


class ExternalGuardMiddleware:
    def __init__(self, app, *, admin_ok=admin_key_ok, on_block=None):
        self.app = app
        self.admin_ok = admin_ok
        self.on_block = on_block

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = (scope.get("method") or "GET").upper()
        headers = scope.get("headers") or []
        ext = is_external(scope)

        blocked = False
        if path in LOCAL_ONLY_GET_PATHS and ext:
            blocked = True
        elif method in SAFE_METHODS:
            if path in PROTECTED_GET_PATHS and ext and not self.admin_ok(headers):
                blocked = True
        else:
            if path not in EXEMPT_WRITE_PATHS and ext and not self.admin_ok(headers):
                blocked = True

        if blocked:
            if self.on_block is not None:
                try:
                    self.on_block(method, path)
                except Exception:
                    pass
            await send({
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(_FORBIDDEN_BODY)).encode("ascii")),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": _FORBIDDEN_BODY,
                "more_body": False,
            })
            return

        await self.app(scope, receive, send)
