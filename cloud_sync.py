"""雲端報表同步：以雲端版本為準還原雲端排程產出的報表，再以 --autostash 拉取，避免本機未提交修改讓 pull 失敗。"""
import os
import subprocess
from pathlib import Path

# 雲端 GitHub Actions 會覆寫的報表檔 (git pathspec)
CLOUD_OWNED_PATHS = [
    "strategies/*/result.csv",
    "strategies/*/result.md",
    "lab/data/tomorrow_trading_advisor.csv",
    "strategies/12_etf_event_radar/premarket_candidates.json",
]

DEFAULT_TIMEOUT = 180

UNFINISHED_GIT_MARKERS = (
    "rebase-merge",
    "rebase-apply",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "index.lock",
)


def _resolve_timeout(timeout=180):
    # 1. TW_STOCK_SYNC_TIMEOUT 是正整數時，用它
    env_timeout = os.environ.get("TW_STOCK_SYNC_TIMEOUT")
    if env_timeout is not None:
        try:
            val = int(env_timeout)
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass

    # 2. 否則用 max(傳入的 timeout, 180)，傳入值是 None 或不合法時用 180
    try:
        if timeout is not None:
            val = int(timeout)
            if val > 0:
                return max(val, DEFAULT_TIMEOUT)
    except (ValueError, TypeError):
        pass

    return DEFAULT_TIMEOUT


def _git(args, cwd, timeout):
    return subprocess.run(["git", *args], cwd=str(cwd), timeout=timeout,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def sync_cloud_reports(root, timeout=180):
    """回傳 (成功與否, 訊息)。"""
    root = Path(root)
    git_dir = root / ".git"

    for marker in UNFINISHED_GIT_MARKERS:
        try:
            if (git_dir / marker).exists():
                return False, "偵測到未完成的 git 操作，已跳過同步"
        except OSError:
            pass

    actual_timeout = _resolve_timeout(timeout)

    try:
        r = _git(["checkout", "--", *CLOUD_OWNED_PATHS], root, actual_timeout)
        if r.returncode != 0:
            return False, f"還原雲端報表失敗: {(r.stderr or r.stdout).strip()}"

        try:
            r = _git(["pull", "--rebase", "--autostash", "origin", "main"], root, actual_timeout)
        except subprocess.TimeoutExpired:
            return False, "逾時，可能留下未完成狀態，需人工處理"

        if r.returncode != 0:
            return False, f"git pull 失敗 (code {r.returncode}): {(r.stderr or r.stdout).strip()}"
        return True, (r.stdout or "").strip()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
