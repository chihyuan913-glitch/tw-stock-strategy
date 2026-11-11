# -*- coding: utf-8 -*-
"""隔離入口。只在目錄與鎖定清單一致時回報已核對。

不載入正式服務。未通過驗證時拒絕，不把眼前目錄重算成新的核准版。
hash 只能說明這次行程讀到的一般檔與這份 pin 一致，不能防止整套程式與信任根一起被換掉，本機來源為 commit 081b97d85a96a7f9b4564039cfbc600b692d77a2 加上工作樹覆蓋 cloud_sync.py（不是任何 commit 物件）。
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Mapping, Optional

from isolate_pin import EXPECTED_LOCK_SHA256

BASE_COMMIT = "5f68220683696d30194bf644cf2defce8950205b"
PR_COMMIT = "f8a0cbbb4ccde0412f4f8ab61ce5c95913c8f96f"
LOCAL_COMMIT = "081b97d85a96a7f9b4564039cfbc600b692d77a2"
LOCAL_WORKTREE_OVERRIDES = {"cloud_sync.py": "97ef47b0a821bf05f15dfa8e119728315f56ca75"}
ROOT = Path(__file__).resolve().parent
LOCK_NAME = "isolate_lock.json"
PIN_NAME = "isolate_pin.py"
PROVENANCE_NAME = "isolate_provenance.json"
OVERLAP = (
    "line_bot_server.py",
    "market_pulse.py",
    "strategies/01_bollinger_reversal/screener.py",
)
REVIEW_FIX_PATHS = (
    "scripts/inject_market_pulse_js.py",
    "strategies/01_bollinger_reversal/screener.py",
)
LOCAL_RETAINED_COUNT = 35
PR_RETAINED_COUNT = 17


class IsolateRefused(SystemExit):
    """隔離設定或目錄驗證失敗。不得改走正式服務。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def resolve_config(environ: Mapping[str, str]) -> dict:
    """只接受明確隔離設定。缺項、正式埠或非 loopback 都拒絕。"""
    errors = []
    flag = environ.get("TW_STOCK_ISOLATE", "")
    host = environ.get("TW_STOCK_ISOLATE_HOST", "")
    port_raw = environ.get("TW_STOCK_ISOLATE_PORT", "")
    if flag != "1":
        errors.append("TW_STOCK_ISOLATE 必須正好為 1，否則拒絕啟動")
    if host != "127.0.0.1":
        errors.append("TW_STOCK_ISOLATE_HOST 必須正好為 127.0.0.1")
    port = None
    if port_raw == "":
        errors.append("TW_STOCK_ISOLATE_PORT 必須明確指定，不預設 8989")
    else:
        try:
            port = int(port_raw)
        except ValueError:
            errors.append("TW_STOCK_ISOLATE_PORT 不是整數")
        else:
            if port < 1024 or port > 65535 or port == 8989:
                errors.append("拒絕 8989 與不合法埠")
    bypass_port = environ.get("PORT", "")
    if bypass_port not in ("",) and bypass_port != port_raw:
        errors.append("PORT 與隔離埠不一致，拒絕以免繞過")
    if bypass_port == "8989":
        errors.append("PORT=8989 拒絕")
    bypass_host = environ.get("HOST", "")
    if bypass_host not in ("", "127.0.0.1"):
        errors.append("HOST 不是 127.0.0.1，拒絕非 loopback")
    return {"ok": not errors, "errors": errors, "host": "127.0.0.1" if not errors else host, "port": port}


def _forbidden_name(name: str) -> bool:
    lowered = name.lower()
    if lowered.startswith(".env"):
        return True
    if lowered in {"regime_config.json", "watchlist.json", "trade_history.csv", "trade_history.json"}:
        return True
    return lowered.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"))


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_reparse_or_link(path: os.PathLike | str) -> bool:
    """檢查路徑是否為符號連結、junction 或具有 reparse point 屬性。

    Why:
    在 Windows 上，NTFS Junction 與重解析點之 os.path.islink() 可能為 False。
    必須檢查 isjunction、S_ISLNK 與 st_file_attributes 之 FILE_ATTRIBUTE_REPARSE_POINT，
    確保根目錄與中間路徑在讀取前即被拒絕。
    """
    try:
        if os.path.islink(path):
            return True
        if getattr(os.path, "isjunction", lambda p: False)(path):
            return True
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            return True
        attrs = getattr(st, "st_file_attributes", 0)
        if bool(attrs & REPARSE_ATTRIBUTE):
            return True
    except (OSError, ValueError):
        return True
    return False


def _is_entry_reparse_or_link(entry: os.DirEntry) -> bool:
    """檢查 scandir DirEntry 是否為符號連結、junction 或具有 reparse point 屬性。

    Why:
    在目錄樹內走訪時，於進入目錄或讀檔前即時攔截，避免遍歷外部聯結或解析重解析點。
    """
    try:
        if entry.is_symlink():
            return True
        if getattr(entry, "is_junction", lambda: False)():
            return True
        st = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            return True
        attrs = getattr(st, "st_file_attributes", 0)
        if bool(attrs & REPARSE_ATTRIBUTE):
            return True
    except (OSError, ValueError):
        return True
    return False


def _reject_link_components(path: Path) -> None:
    """檢查 root 自己以及它上面的每一層。不解析連結目標。"""
    if path.is_absolute():
        current = Path(path.anchor)
        parts = path.parts[1:]
    else:
        current = Path(".")
        parts = path.parts
    for part in parts:
        current = current / part
        if _is_reparse_or_link(current):
            raise IsolateRefused("symlink")


def _scan(root: Path) -> dict:
    """只收集一般檔路徑。遇到連結、特殊檔或不該攜帶的檔名即停止，不讀內容。"""
    _reject_link_components(root)
    if _is_reparse_or_link(root):
        raise IsolateRefused("symlink")
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode):
        raise IsolateRefused("type")
    found = {}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            iterator = os.scandir(current)
        except OSError as exc:
            raise IsolateRefused("type") from exc
        with iterator:
            for entry in iterator:
                if _is_entry_reparse_or_link(entry):
                    raise IsolateRefused("symlink")
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in {"__pycache__", ".pytest_cache", ".git"}:
                        raise IsolateRefused("extra")
                    stack.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise IsolateRefused("type")
                rel = str(Path(entry.path).relative_to(root)).replace("\\", "/")
                if _forbidden_name(entry.name):
                    raise IsolateRefused("forbidden")
                found[rel] = Path(entry.path)
    return found


def _load_lock(path: Path, expected_sha256: str) -> dict:
    if not expected_sha256 or len(expected_sha256) != 64:
        raise IsolateRefused("lock")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise IsolateRefused("lock")
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IsolateRefused("lock") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("files"), list):
        raise IsolateRefused("lock")
    return parsed


def _check_provenance(provenance: dict, blobs: dict) -> dict:
    if provenance.get("commit_object_present") is not False:
        raise IsolateRefused("provenance")
    if provenance.get("local") != LOCAL_COMMIT or provenance.get("local_worktree_overrides") != LOCAL_WORKTREE_OVERRIDES:
        raise IsolateRefused("provenance")
    if provenance.get("base") != BASE_COMMIT or provenance.get("pr") != PR_COMMIT:
        raise IsolateRefused("provenance")
    if provenance.get("runtime_rederived_ancestry") is not False:
        raise IsolateRefused("provenance")
    if provenance.get("review_fixes") != list(REVIEW_FIX_PATHS):
        raise IsolateRefused("provenance")
    rows = provenance.get("files")
    if not isinstance(rows, list):
        raise IsolateRefused("provenance")
    by_role = {}
    by_path = {}
    for row in rows:
        if not isinstance(row, dict):
            raise IsolateRefused("provenance")
        path = row.get("path")
        role = row.get("role")
        isolate_blob = row.get("isolate_blob")
        if not isinstance(path, str) or not isinstance(role, str) or not isinstance(isolate_blob, str):
            raise IsolateRefused("provenance")
        if path not in blobs or blobs[path] != isolate_blob:
            raise IsolateRefused("provenance")
        by_role.setdefault(role, []).append(row)
        by_path[path] = row
    local_rows = by_role.get("local-retained", [])
    pr_rows = by_role.get("pr-retained", [])
    pr_fix_rows = by_role.get("pr-plus-review-fix", [])
    overlap_rows = [row for row in rows if str(row.get("role", "")).startswith("merged-overlap")]
    if len(local_rows) != LOCAL_RETAINED_COUNT or len(pr_rows) != PR_RETAINED_COUNT:
        raise IsolateRefused("provenance")
    expected_pr_fix = sorted(set(REVIEW_FIX_PATHS) - set(OVERLAP))
    if sorted(row["path"] for row in pr_fix_rows) != expected_pr_fix:
        raise IsolateRefused("provenance")
    for row in pr_fix_rows:
        if row.get("isolate_blob") == row.get("pr_blob"):
            raise IsolateRefused("provenance")
    if sorted(row["path"] for row in overlap_rows) != sorted(OVERLAP):
        raise IsolateRefused("provenance")
    for row in local_rows:
        if row.get("isolate_blob") != row.get("local_blob"):
            raise IsolateRefused("provenance")
    for row in pr_rows:
        if row.get("isolate_blob") != row.get("pr_blob"):
            raise IsolateRefused("provenance")
    for row in overlap_rows:
        if row["isolate_blob"] in {row.get("local_blob"), row.get("pr_blob"), row.get("base_blob")}:
            raise IsolateRefused("provenance")
    for row in by_role.get("base-unchanged", []):
        if row.get("isolate_blob") != row.get("base_blob"):
            raise IsolateRefused("provenance")
    return {
        "recorded_local_retained": len(local_rows),
        "recorded_review_fixes": len(pr_fix_rows),
        "recorded_pr_retained": len(pr_rows),
        "recorded_merged_overlap": len(overlap_rows),
        "overlap_distinct_from_local_and_pr": True,
        "runtime_rederived_ancestry": False,
        "commit_object_present": False,
    }


def verify_tree(root: Path, expected_lock_sha256: str = EXPECTED_LOCK_SHA256) -> dict:
    """成功才代表目錄與這份 pin 一致。失敗不產生新的核准雜湊。"""
    root = Path(os.path.abspath(root))
    found = _scan(root)
    if LOCK_NAME not in found or PIN_NAME not in found or PROVENANCE_NAME not in found:
        raise IsolateRefused("missing")
    lock = _load_lock(found[LOCK_NAME], expected_lock_sha256)
    expected = {}
    for row in lock["files"]:
        if not isinstance(row, dict):
            raise IsolateRefused("lock")
        path = row.get("path")
        git_blob = row.get("git_blob")
        if not isinstance(path, str) or not isinstance(git_blob, str):
            raise IsolateRefused("lock")
        if path in {LOCK_NAME, PIN_NAME} or path in expected:
            raise IsolateRefused("lock")
        expected[path] = git_blob
    if PROVENANCE_NAME not in expected:
        raise IsolateRefused("missing")
    extras = set(found) - set(expected) - {LOCK_NAME, PIN_NAME}
    missing = set(expected) - set(found)
    if extras:
        raise IsolateRefused("extra")
    if missing:
        raise IsolateRefused("missing")
    blobs = {}
    for path, git_blob in expected.items():
        data = found[path].read_bytes()
        actual = _git_blob(data)
        if actual != git_blob:
            raise IsolateRefused("mismatch")
        blobs[path] = actual
    provenance = json.loads(found[PROVENANCE_NAME].read_text(encoding="utf-8"))
    summary = _check_provenance(provenance, blobs)
    return {
        "verified": True,
        "status": "isolate",
        "service": "tw-stock-isolate",
        "isolate": True,
        "production_started": False,
        "dashboard_served": False,
        "tree_sha256": expected_lock_sha256,
        "base": BASE_COMMIT,
        "pr": PR_COMMIT,
        "local": LOCAL_COMMIT,
        "local_worktree_overrides": dict(LOCAL_WORKTREE_OVERRIDES),
        "source_checked": "runtime file blobs match the pinned lock; git ancestry is build-time evidence inside isolate_provenance.json, not re-derived here",
        "symlink_check": "lstat+windows-reparse-attribute",
        "windows_junction_reparse_unverified": False,
        "windows_symlink_unverified": True,
        "windows_other_reparse_unverified": True,
        **summary,
    }


def _server_allowed(scope: dict) -> bool:
    server = scope.get("server")
    if not isinstance(server, (list, tuple)) or len(server) < 2:
        return False
    host, port = server[0], server[1]
    if host != "127.0.0.1":
        return False
    if not isinstance(port, int) or port == 8989 or port < 1024 or port > 65535:
        return False
    return True


def create_app(environ: Mapping[str, str]):
    """建立只含健康檢查與拒絕路由的行程內應用。不呼叫 uvicorn。"""
    config = resolve_config(environ)
    if not config["ok"]:
        raise IsolateRefused("config")
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="tw-stock-isolate", docs_url=None, redoc_url=None, openapi_url=None)
    allowed = {("GET", "/health"), ("HEAD", "/health"), ("GET", "/isolate/manifest"), ("HEAD", "/isolate/manifest")}

    @app.middleware("http")
    async def reject_unapproved(request, call_next):
        if not _server_allowed(request.scope):
            return JSONResponse(status_code=403, content={"status": "refused", "verified": False, "reason": "bind"})
        if (request.method, request.url.path) not in allowed:
            return JSONResponse(status_code=403, content={"status": "refused", "verified": False, "reason": "route"})
        return await call_next(request)

    def _verified_body():
        try:
            report = verify_tree(ROOT)
        except IsolateRefused as exc:
            return JSONResponse(status_code=503, content={"status": "refused", "verified": False, "reason": exc.code})
        report["bind"] = {"host": config["host"], "port": config["port"]}
        return report

    @app.get("/health")
    def health():
        return _verified_body()

    @app.get("/isolate/manifest")
    def manifest_route():
        body = _verified_body()
        if not isinstance(body, dict):
            return body
        body["manifest"] = "isolate_provenance.json"
        return body

    @app.websocket("/{full_path:path}")
    async def block_ws(websocket):
        await websocket.close(code=1008)

    return app


def serve(environ: Mapping[str, str], runner=None) -> int:
    """驗證通過才把控制權交給呼叫端提供的啟動函式。預設才匯入 uvicorn。"""
    config = resolve_config(environ)
    if not config["ok"]:
        return 2
    try:
        verify_tree(ROOT)
    except IsolateRefused:
        return 3

    def factory():
        return create_app(environ)

    if runner is None:
        import uvicorn
        runner = uvicorn.run
    runner(factory, host=config["host"], port=config["port"], factory=True, reload=False)
    return 0


def main(environ: Optional[Mapping[str, str]] = None, runner=None) -> int:
    return serve(os.environ if environ is None else environ, runner)


def __getattr__(name: str):
    if name == "app":
        raise SystemExit("拒絕 uvicorn isolate_entry:app 直接載入，以免繞過埠與位址檢查")
    raise AttributeError(name)


if __name__ == "__main__":
    sys.exit(main())
