# -*- coding: utf-8 -*-
"""隔離入口測試。不啟動 uvicorn、不綁真實 socket、不讀秘密或 runtime。"""
import ast
import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import isolate_entry


GOOD = {
    "TW_STOCK_ISOLATE": "1",
    "TW_STOCK_ISOLATE_HOST": "127.0.0.1",
    "TW_STOCK_ISOLATE_PORT": "18080",
}
SENTINEL = b"SENTINEL-NOT-A-SECRET\n"


def test_static_entry_has_no_side_effect_imports():
    source = (ROOT / "isolate_entry.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".")[0])
    for name in ("subprocess", "sqlite3", "socket", "threading", "line_bot_server", "radar_manager", "line_sender", "stock_analyzer", "cloud_sync"):
        assert name not in imported
    assert "readlink" not in source


def test_uvicorn_import_is_only_inside_serve():
    source = (ROOT / "isolate_entry.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    serve = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "serve")
    nested = [node for node in ast.walk(serve) if isinstance(node, ast.Import) and any(alias.name == "uvicorn" for alias in node.names)]
    assert len(nested) == 1
    top = [node for node in tree.body if isinstance(node, ast.Import) and any(alias.name == "uvicorn" for alias in node.names)]
    assert top == []


def test_production_guard_is_before_side_effect_imports():
    source = (ROOT / "line_bot_server.py").read_text(encoding="utf-8")
    guard = source.index("_isolate_flag")
    for marker in ("from stock_analyzer import", "ENV_PATH", "include_router", "from radar_manager import", "sync_cloud_reports"):
        assert guard < source.index(marker)


def test_production_file_diff_is_only_the_guard():
    ref_env = os.environ.get("TW_ISOLATE_REFERENCE_DIR", "")
    if not ref_env:
        pytest.skip("環境變數 TW_ISOLATE_REFERENCE_DIR 未設定，跳過參考檔比對測試")
    ref_dir = Path(ref_env)
    if not ref_dir.is_dir():
        pytest.skip(f"環境變數 TW_ISOLATE_REFERENCE_DIR 指定之目錄不存在: {ref_env}")
    candidate = (ref_dir / "line_bot_server.py").read_text(encoding="utf-8")
    current = (ROOT / "line_bot_server.py").read_text(encoding="utf-8")
    inserted = (
        '_isolate_flag = os.environ.get("TW_STOCK_ISOLATE", "")\n'
        'if _isolate_flag != "":\n'
        '    raise SystemExit("TW_STOCK_ISOLATE 已設定，拒絕載入正式 line_bot_server，不回退正式啟動")\n'
    )
    assert current.replace(inserted, "", 1) == candidate


def test_strategy_files_match_candidate():
    ref_env = os.environ.get("TW_ISOLATE_REFERENCE_DIR", "")
    if not ref_env:
        pytest.skip("環境變數 TW_ISOLATE_REFERENCE_DIR 未設定，跳過參考檔比對測試")
    ref_dir = Path(ref_env)
    if not ref_dir.is_dir():
        pytest.skip(f"環境變數 TW_ISOLATE_REFERENCE_DIR 指定之目錄不存在: {ref_env}")
    for rel in (
        "four_price_engine.py",
        "lab/data/storage.py",
        "cloud_sync.py",
    ):
        assert (ROOT / rel).read_bytes() == (ref_dir / rel).read_bytes()


def test_screener_diff_is_only_review_fix():
    ref_env = os.environ.get("TW_ISOLATE_REFERENCE_DIR", "")
    if not ref_env:
        pytest.skip("環境變數 TW_ISOLATE_REFERENCE_DIR 未設定，跳過參考檔比對測試")
    ref_dir = Path(ref_env)
    if not ref_dir.is_dir():
        pytest.skip(f"環境變數 TW_ISOLATE_REFERENCE_DIR 指定之目錄不存在: {ref_env}")
    reference = (ref_dir / "strategies/01_bollinger_reversal/screener.py").read_text(encoding="utf-8")
    current = (ROOT / "strategies/01_bollinger_reversal/screener.py").read_text(encoding="utf-8")

    part1_target = '    cash_ratio_str = "未知"\n    reg_cfg = None\n'
    assert current.count(part1_target) == 1
    current = current.replace(part1_target, '    cash_ratio_str = "未知"\n', 1)

    part2_target = (
        "\n"
        "    try:\n"
        "        from dashboard_contract import flatten_effective_regime\n"
        '        cash_ratio_str = flatten_effective_regime(reg_cfg).get("cash_display", "未知")\n'
        "    except Exception:\n"
        '        cash_ratio_str = "未知"\n'
    )
    assert current.count(part2_target) == 1
    current = current.replace(part2_target, "", 1)

    part3_target = '        market_label = market_reg.get("label") or "生效位階未知"\n'
    part3_insert = (
        "        from dashboard_contract import flatten_effective_regime\n"
        '        cash_ratio_str = flatten_effective_regime(reg_cfg).get("cash_display", "未知")\n'
    )
    assert current.count(part3_target) == 1
    current = current.replace(part3_target, part3_target + part3_insert, 1)

    assert current == reference


@pytest.mark.parametrize("environ", [
    {},
    {"TW_STOCK_ISOLATE": "0"},
    {"TW_STOCK_ISOLATE": "true"},
    {"TW_STOCK_ISOLATE": "1"},
    {"TW_STOCK_ISOLATE": "1 ", "TW_STOCK_ISOLATE_HOST": "127.0.0.1", "TW_STOCK_ISOLATE_PORT": "18080"},
    {"TW_STOCK_ISOLATE": "1", "TW_STOCK_ISOLATE_HOST": "0.0.0.0", "TW_STOCK_ISOLATE_PORT": "18080"},
    {"TW_STOCK_ISOLATE": "1", "TW_STOCK_ISOLATE_HOST": "localhost", "TW_STOCK_ISOLATE_PORT": "18080"},
    {"TW_STOCK_ISOLATE": "1", "TW_STOCK_ISOLATE_HOST": "::1", "TW_STOCK_ISOLATE_PORT": "18080"},
    {"TW_STOCK_ISOLATE": "1", "TW_STOCK_ISOLATE_HOST": "127.0.0.1", "TW_STOCK_ISOLATE_PORT": "8989"},
    {"TW_STOCK_ISOLATE": "1", "TW_STOCK_ISOLATE_HOST": "127.0.0.1", "TW_STOCK_ISOLATE_PORT": " 8989"},
    {"TW_STOCK_ISOLATE": "1", "TW_STOCK_ISOLATE_HOST": "127.0.0.1", "TW_STOCK_ISOLATE_PORT": "08989"},
    {"TW_STOCK_ISOLATE": "1", "TW_STOCK_ISOLATE_HOST": "127.0.0.1", "TW_STOCK_ISOLATE_PORT": "80"},
    {"TW_STOCK_ISOLATE": "1", "TW_STOCK_ISOLATE_HOST": "127.0.0.1", "TW_STOCK_ISOLATE_PORT": "nope"},
    {**GOOD, "PORT": "8989"},
    {**GOOD, "HOST": "0.0.0.0"},
    {**GOOD, "PORT": "80"},
])
def test_bad_config_refuses_without_runner(environ):
    called = []
    code = isolate_entry.serve(environ, runner=lambda *args, **kwargs: called.append(kwargs))
    assert code == 2
    assert called == []
    with pytest.raises(isolate_entry.IsolateRefused):
        isolate_entry.create_app(environ)


def test_good_config_passes_runner_but_does_not_bind():
    seen = {}

    def runner(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs

    code = isolate_entry.serve(dict(GOOD), runner=runner)
    assert code == 0
    assert seen["kwargs"]["host"] == "127.0.0.1"
    assert seen["kwargs"]["port"] == 18080
    assert seen["kwargs"]["factory"] is True
    assert seen["kwargs"]["reload"] is False


def test_module_app_attribute_refuses_uvicorn_bypass():
    with pytest.raises(SystemExit, match="拒絕 uvicorn"):
        isolate_entry.app


def _client():
    app = isolate_entry.create_app(dict(GOOD))
    return TestClient(app, base_url="http://127.0.0.1:18080")


def test_health_reports_verified_pin_not_a_local_commit():
    with _client() as client:
        first = client.get("/health")
        second = client.get("/isolate/manifest")
    assert first.status_code == 200
    body = first.json()
    assert body["verified"] is True
    assert body["dashboard_served"] is False
    assert body["commit_object_present"] is False
    assert body["runtime_rederived_ancestry"] is False
    assert body["local"] == isolate_entry.LOCAL_COMMIT
    assert body["local_worktree_overrides"] == isolate_entry.LOCAL_WORKTREE_OVERRIDES
    assert body["recorded_local_retained"] == 35
    assert body["recorded_review_fixes"] == 1
    assert body["recorded_pr_retained"] == 17
    assert body["recorded_merged_overlap"] == 3
    assert body["overlap_distinct_from_local_and_pr"] is True
    assert body["tree_sha256"] == isolate_entry.EXPECTED_LOCK_SHA256
    assert second.status_code == 200
    assert second.json()["tree_sha256"] == body["tree_sha256"]
    assert body["windows_junction_reparse_unverified"] is False
    assert body["windows_symlink_unverified"] is True


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/action/sync_cloud"),
    ("GET", "/api/action/sync_cloud"),
    ("PUT", "/api/action/sync_cloud"),
    ("PATCH", "/api/action/run_screener"),
    ("DELETE", "/api/action/run_radar"),
    ("TRACE", "/api/action/sync_cloud"),
    ("OPTIONS", "/api/action/test_line"),
    ("HEAD", "/api/action/run_optimizer"),
    ("POST", "/callback"),
    ("GET", "/api/overview"),
    ("GET", "/api/strategy/01"),
    ("POST", "/api/studio/chat"),
    ("GET", "/"),
    ("GET", "/logs"),
])
def test_side_effect_routes_are_refused(method, path):
    with _client() as client:
        response = client.request(method, path)
    assert response.status_code == 403
    if method != "HEAD":
        assert response.json()["verified"] is False


def test_non_loopback_and_8989_scope_are_refused():
    app = isolate_entry.create_app(dict(GOOD))
    outside = TestClient(app, base_url="http://10.1.2.3:18080")
    production_port = TestClient(app, base_url="http://127.0.0.1:8989")
    with outside as client:
        assert client.get("/health").status_code == 403
    with production_port as client:
        assert client.get("/health").status_code == 403
        assert client.post("/callback").status_code == 403


def test_websocket_is_refused_in_process():
    app = isolate_entry.create_app(dict(GOOD))
    client = TestClient(app, base_url="http://127.0.0.1:18080")
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/radar"):
            pass


def _db_stamp():
    db = ROOT / "lab" / "data" / "tw_market.db"
    if not db.exists():
        return None
    info = db.lstat()
    return (info.st_size, info.st_mtime_ns)


def test_loading_production_module_with_flag_stops_before_side_effects(monkeypatch):
    stamp = _db_stamp()
    before = set(sys.modules)
    monkeypatch.setenv("TW_STOCK_ISOLATE", "1")
    for key in ("LINE_CHANNEL_ACCESS_TOKEN", "HOST", "PORT", "RENDER_EXTERNAL_URL"):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("line_bot_server_blocked", ROOT / "line_bot_server.py")
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(SystemExit, match="拒絕載入正式"):
        spec.loader.exec_module(module)
    assert _db_stamp() == stamp
    introduced = set(sys.modules) - before
    assert "lab.web.router" not in introduced
    assert not any(name.split(".")[0] in {"stock_analyzer", "radar_manager", "line_sender", "cloud_sync"} for name in introduced)


def test_invalid_flag_also_blocks_production_module(monkeypatch):
    stamp = _db_stamp()
    monkeypatch.setenv("TW_STOCK_ISOLATE", "yes")
    spec = importlib.util.spec_from_file_location("line_bot_server_bad_flag", ROOT / "line_bot_server.py")
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(SystemExit, match="拒絕載入正式"):
        spec.loader.exec_module(module)
    assert _db_stamp() == stamp


def _refuse(root: Path) -> str:
    with pytest.raises(isolate_entry.IsolateRefused) as caught:
        isolate_entry.verify_tree(root, isolate_entry.EXPECTED_LOCK_SHA256)
    assert SENTINEL.decode() not in str(caught.value)
    return caught.value.code


def _symlink_or_skip(link_path: Path, target: Path, target_is_directory: bool = False) -> None:
    try:
        link_path.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as e:
        if getattr(e, "winerror", None) == 1314:
            pytest.skip("Windows 無符號連結建立權限 (WinError 1314)，需開啟開發人員模式或以管理者身分執行")
        raise


def test_file_dir_and_root_symlinks_refuse_before_read(tmp_path):
    missing = tmp_path / "missing-sentinel"
    root = tmp_path / "tree"
    root.mkdir()
    _symlink_or_skip(root / "looks-safe.txt", missing)
    assert _refuse(root) == "symlink"
    assert not missing.exists()

    root2 = tmp_path / "tree2"
    root2.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.txt").write_bytes(SENTINEL)
    _symlink_or_skip(root2 / "linked-dir", outside, target_is_directory=True)
    assert _refuse(root2) == "symlink"

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "root-link"
    _symlink_or_skip(link, real, target_is_directory=True)
    assert _refuse(link) == "symlink"


def test_disguised_file_link_and_forbidden_names_are_not_opened(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    target = tmp_path / "sentinel-target"
    target.write_bytes(SENTINEL)
    _symlink_or_skip(root / "readme.txt", target)
    assert _refuse(root) == "symlink"

    plain = tmp_path / "plain"
    plain.mkdir()
    secret = plain / ".env"
    secret.write_bytes(SENTINEL)
    os.chmod(secret, 0)
    try:
        assert _refuse(plain) == "forbidden"
    finally:
        os.chmod(secret, 0o600)
    database = tmp_path / "dbcase"
    database.mkdir()
    db = database / "sample.db"
    db.write_bytes(SENTINEL)
    os.chmod(db, 0)
    try:
        assert _refuse(database) == "forbidden"
    finally:
        os.chmod(db, 0o600)


def test_missing_extra_and_tamper_do_not_become_a_new_approval(tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(ROOT, copy, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    report = isolate_entry.verify_tree(copy)
    assert report["verified"] is True
    (copy / "ISOLATE_APPLY.md").write_text("tampered\n", encoding="utf-8")
    assert _refuse(copy) == "mismatch"
    copy2 = tmp_path / "copy2"
    shutil.copytree(ROOT, copy2, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (copy2 / "ISOLATE_APPLY.md").unlink()
    assert _refuse(copy2) == "missing"
    copy3 = tmp_path / "copy3"
    shutil.copytree(ROOT, copy3, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    extra = copy3 / "extra.txt"
    extra.write_bytes(SENTINEL)
    os.chmod(extra, 0)
    try:
        assert _refuse(copy3) == "extra"
    finally:
        os.chmod(extra, 0o600)


def test_two_file_root_cannot_claim_verified_ancestry(tmp_path):
    root = tmp_path / "fake"
    root.mkdir()
    (root / "isolate_pin.py").write_text("EXPECTED_LOCK_SHA256 = 'x'\n", encoding="utf-8")
    lock = {
        "version": 2,
        "files": [{"path": "isolate_provenance.json", "git_blob": "abc"}],
        "claims_verified_37": True,
    }
    import json
    raw = (json.dumps(lock) + "\n").encode()
    (root / "isolate_lock.json").write_bytes(raw)
    (root / "isolate_provenance.json").write_text("{}", encoding="utf-8")
    digest = __import__("hashlib").sha256(raw).hexdigest()
    with pytest.raises(isolate_entry.IsolateRefused) as caught:
        isolate_entry.verify_tree(root, digest)
    assert caught.value.code in {"lock", "provenance", "mismatch", "missing"}
    assert caught.value.code != "symlink"


def test_old_or_edited_lock_is_refused(tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(ROOT, copy, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (copy / "isolate_lock.json").write_text("{}\n", encoding="utf-8")
    assert _refuse(copy) == "lock"


from contextlib import contextmanager
import builtins
import io


@contextmanager
def _assert_no_sentinel_reads():
    """攔截 open, io.open, Path.read_bytes, Path.read_text，斷言對 sentinel 的讀取呼叫為 0 次。"""
    calls = []
    orig_builtin_open = builtins.open
    orig_io_open = io.open
    orig_read_bytes = Path.read_bytes
    orig_read_text = Path.read_text

    def _check_sentinel(target):
        s = str(target)
        if "sentinel" in s:
            calls.append(s)

    def fake_builtin_open(file, *args, **kwargs):
        _check_sentinel(file)
        return orig_builtin_open(file, *args, **kwargs)

    def fake_io_open(file, *args, **kwargs):
        _check_sentinel(file)
        return orig_io_open(file, *args, **kwargs)

    def fake_read_bytes(self):
        _check_sentinel(self)
        return orig_read_bytes(self)

    def fake_read_text(self, *args, **kwargs):
        _check_sentinel(self)
        return orig_read_text(self, *args, **kwargs)

    builtins.open = fake_builtin_open
    io.open = fake_io_open
    Path.read_bytes = fake_read_bytes
    Path.read_text = fake_read_text
    try:
        yield calls
    finally:
        builtins.open = orig_builtin_open
        io.open = orig_io_open
        Path.read_bytes = orig_read_bytes
        Path.read_text = orig_read_text
    assert len(calls) == 0, f"sentinel 檔案不應在拒絕前被讀取，實際攔截到呼叫: {calls}"


def _make_junction(target: Path, link: Path):
    """建立 NTFS Junction 或跨平台 symlink 供測試。"""
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return
        except Exception:
            pass
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_root_junction_refused_before_read(tmp_path):
    """根目錄本身為 junction 時，讀取前即拒絕，斷言 sentinel 未被讀取。"""
    real = tmp_path / "real_root"
    real.mkdir()
    sentinel = real / "sentinel.txt"
    sentinel.write_bytes(SENTINEL)

    junc_root = tmp_path / "junc_root"
    _make_junction(real, junc_root)

    with _assert_no_sentinel_reads():
        assert _refuse(junc_root) == "symlink"


def test_intermediate_path_junction_refused_before_read(tmp_path):
    """中間路徑包含 junction 時，讀取前即拒絕，斷言 sentinel 未被讀取。"""
    real_parent = tmp_path / "real_parent"
    real_parent.mkdir()
    sub = real_parent / "sub"
    sub.mkdir()
    tree = sub / "tree"
    tree.mkdir()
    (tree / "sentinel.txt").write_bytes(SENTINEL)

    junc_parent = tmp_path / "junc_parent"
    _make_junction(real_parent, junc_parent)

    target_tree = junc_parent / "sub" / tree.name
    with _assert_no_sentinel_reads():
        assert _refuse(target_tree) == "symlink"


def test_in_tree_junction_directory_refused_before_read(tmp_path):
    """樹內子目錄為 junction 時，讀取前即拒絕，斷言 sentinel 未被讀取。"""
    root = tmp_path / "tree"
    root.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "sentinel.txt").write_bytes(SENTINEL)

    junc_child = root / "junc_child"
    _make_junction(outside, junc_child)

    with _assert_no_sentinel_reads():
        assert _refuse(root) == "symlink"


def test_root_reparse_attribute_refused_before_read(tmp_path, monkeypatch):
    """根目錄帶有 FILE_ATTRIBUTE_REPARSE_POINT 屬性時，讀取前即拒絕，斷言 sentinel 未被讀取。"""
    root = tmp_path / "reparse_root"
    root.mkdir()
    (root / "sentinel.txt").write_bytes(SENTINEL)

    orig_lstat = os.lstat

    def fake_lstat(path, *args, **kwargs):
        st = orig_lstat(path, *args, **kwargs)
        if Path(path).resolve() == root.resolve():
            st_dict = {attr: getattr(st, attr) for attr in dir(st) if attr.startswith("st_")}
            st_dict["st_file_attributes"] = getattr(st, "st_file_attributes", 0) | isolate_entry.REPARSE_ATTRIBUTE
            return type("StatWithReparse", (), st_dict)()
        return st

    monkeypatch.setattr(os, "lstat", fake_lstat)
    with _assert_no_sentinel_reads():
        assert _refuse(root) == "symlink"


def test_intermediate_path_reparse_attribute_refused_before_read(tmp_path, monkeypatch):
    """中間路徑帶有 FILE_ATTRIBUTE_REPARSE_POINT 屬性時，讀取前即拒絕，斷言 sentinel 未被讀取。"""
    parent = tmp_path / "reparse_parent"
    parent.mkdir()
    sub = parent / "sub"
    sub.mkdir()
    (sub / "sentinel.txt").write_bytes(SENTINEL)

    orig_lstat = os.lstat

    def fake_lstat(path, *args, **kwargs):
        st = orig_lstat(path, *args, **kwargs)
        if Path(path).resolve() == parent.resolve():
            st_dict = {attr: getattr(st, attr) for attr in dir(st) if attr.startswith("st_")}
            st_dict["st_file_attributes"] = getattr(st, "st_file_attributes", 0) | isolate_entry.REPARSE_ATTRIBUTE
            return type("StatWithReparse", (), st_dict)()
        return st

    monkeypatch.setattr(os, "lstat", fake_lstat)
    with _assert_no_sentinel_reads():
        assert _refuse(sub) == "symlink"


def test_in_tree_reparse_attribute_refused_before_read(tmp_path, monkeypatch):
    """樹內目錄帶有 FILE_ATTRIBUTE_REPARSE_POINT 屬性時，讀取前即拒絕，斷言 sentinel 未被讀取。"""
    root = tmp_path / "tree"
    root.mkdir()
    entry_path = root / "reparse_entry"
    entry_path.mkdir()
    (entry_path / "sentinel.txt").write_bytes(SENTINEL)

    orig_scandir = os.scandir

    class EntryProxy:
        def __init__(self, real_entry):
            self._real = real_entry
            self.name = real_entry.name
            self.path = real_entry.path

        def is_symlink(self):
            return self._real.is_symlink()

        def is_junction(self):
            return getattr(self._real, "is_junction", lambda: False)()

        def is_dir(self, follow_symlinks=False):
            return self._real.is_dir(follow_symlinks=follow_symlinks)

        def is_file(self, follow_symlinks=False):
            return self._real.is_file(follow_symlinks=follow_symlinks)

        def stat(self, follow_symlinks=False):
            st = self._real.stat(follow_symlinks=follow_symlinks)
            st_dict = {attr: getattr(st, attr) for attr in dir(st) if attr.startswith("st_")}
            st_dict["st_file_attributes"] = getattr(st, "st_file_attributes", 0) | isolate_entry.REPARSE_ATTRIBUTE
            return type("StatWithReparse", (), st_dict)()

    class IteratorContext:
        def __init__(self, items):
            self.items = items

        def __iter__(self):
            return iter(self.items)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_scandir(path="."):
        entries = []
        for e in orig_scandir(path):
            if e.name == "reparse_entry":
                entries.append(EntryProxy(e))
            else:
                entries.append(e)
        return IteratorContext(entries)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    with _assert_no_sentinel_reads():
        assert _refuse(root) == "symlink"


def test_in_tree_reparse_file_refused_before_open(tmp_path, monkeypatch):
    """樹內一般檔案若帶有 REPARSE 屬性，在 open/read 讀取前即拒絕，斷言 sentinel 未被讀取。"""
    root = tmp_path / "tree"
    root.mkdir()
    target_file = root / "sentinel_reparse.txt"
    target_file.write_bytes(SENTINEL)

    orig_scandir = os.scandir

    class FileEntryProxy:
        def __init__(self, real_entry):
            self._real = real_entry
            self.name = real_entry.name
            self.path = real_entry.path

        def is_symlink(self):
            return False

        def is_junction(self):
            return False

        def is_dir(self, follow_symlinks=False):
            return False

        def is_file(self, follow_symlinks=False):
            return True

        def stat(self, follow_symlinks=False):
            st = self._real.stat(follow_symlinks=follow_symlinks)
            st_dict = {attr: getattr(st, attr) for attr in dir(st) if attr.startswith("st_")}
            st_dict["st_file_attributes"] = getattr(st, "st_file_attributes", 0) | isolate_entry.REPARSE_ATTRIBUTE
            return type("StatWithReparse", (), st_dict)()

    class IteratorContext:
        def __init__(self, items):
            self.items = items

        def __iter__(self):
            return iter(self.items)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_scandir(path="."):
        entries = []
        for e in orig_scandir(path):
            if e.name == "sentinel_reparse.txt":
                entries.append(FileEntryProxy(e))
            else:
                entries.append(e)
        return IteratorContext(entries)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    with _assert_no_sentinel_reads():
        assert _refuse(root) == "symlink"


def test_root_mock_is_junction_refused_before_read(tmp_path, monkeypatch):
    """使用 mock 模擬 is_junction 為 True（根目錄），驗證跨平台在讀取前拒絕。"""
    root = tmp_path / "mock_junc_root"
    root.mkdir()
    (root / "sentinel.txt").write_bytes(SENTINEL)

    def fake_isjunction(path):
        return Path(path).resolve() == root.resolve()

    monkeypatch.setattr(os.path, "isjunction", fake_isjunction, raising=False)
    with _assert_no_sentinel_reads():
        assert _refuse(root) == "symlink"


def test_intermediate_path_mock_is_junction_refused_before_read(tmp_path, monkeypatch):
    """使用 mock 模擬 is_junction 為 True（中間路徑），驗證跨平台在讀取前拒絕。"""
    parent = tmp_path / "mock_junc_parent"
    parent.mkdir()
    sub = parent / "sub"
    sub.mkdir()
    (sub / "sentinel.txt").write_bytes(SENTINEL)

    def fake_isjunction(path):
        return Path(path).resolve() == parent.resolve()

    monkeypatch.setattr(os.path, "isjunction", fake_isjunction, raising=False)
    with _assert_no_sentinel_reads():
        assert _refuse(sub) == "symlink"


def test_lstat_oserror_refused_as_symlink(tmp_path, monkeypatch):
    """模擬 lstat 拋出 OSError 時，出錯一律拒絕為 symlink，且 sentinel 不被讀取。"""
    root = tmp_path / "oserror_root"
    root.mkdir()
    (root / "sentinel.txt").write_bytes(SENTINEL)

    orig_lstat = os.lstat

    def fake_lstat(path, *args, **kwargs):
        if Path(path).resolve() == root.resolve():
            raise OSError("模擬磁碟或權限存取異常 (Simulated device/permission error)")
        return orig_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fake_lstat)
    with _assert_no_sentinel_reads():
        assert _refuse(root) == "symlink"



