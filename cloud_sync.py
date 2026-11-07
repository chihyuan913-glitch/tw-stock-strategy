"""雲端報表同步：以雲端版本為準還原雲端排程產出的報表，再以 --autostash 拉取，避免本機未提交修改讓 pull 失敗。"""
import subprocess
from pathlib import Path

# 雲端 GitHub Actions 會覆寫的報表檔 (git pathspec)
CLOUD_OWNED_PATHS = [
    "strategies/*/result.csv",
    "strategies/*/result.md",
    "lab/data/tomorrow_trading_advisor.csv",
    "strategies/12_etf_event_radar/premarket_candidates.json",
]


def _git(args, cwd, timeout):
    return subprocess.run(["git", *args], cwd=str(cwd), timeout=timeout,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def sync_cloud_reports(root, timeout=30):
    """回傳 (成功與否, 訊息)。"""
    root = Path(root)
    try:
        r = _git(["checkout", "--", *CLOUD_OWNED_PATHS], root, timeout)
        if r.returncode != 0:
            return False, f"還原雲端報表失敗: {(r.stderr or r.stdout).strip()}"
        r = _git(["pull", "--rebase", "--autostash", "origin", "main"], root, timeout)
        if r.returncode != 0:
            return False, f"git pull 失敗 (code {r.returncode}): {(r.stderr or r.stdout).strip()}"
        return True, (r.stdout or "").strip()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"