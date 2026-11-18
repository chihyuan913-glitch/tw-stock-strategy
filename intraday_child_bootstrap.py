# -*- coding: utf-8 -*-
import runpy
import sys
from pathlib import Path

from mis_shared_gate import install_urlopen_patch

FORBIDDEN_FLAGS = {
    "--force",
    "--force-push",
    "--test-push",
    "--force-test",
    "--ignore-holiday",
}


def main(argv=None, *, allowed_root=None, gate=None, opener=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2

    script_raw = args[0]
    scanner_args = args[1:]

    for arg in scanner_args:
        flag = str(arg).split("=", 1)[0].strip()
        if flag in FORBIDDEN_FLAGS:
            return 2

    root_dir = (
        Path(__file__).resolve().parent / "strategies"
        if allowed_root is None
        else Path(allowed_root).resolve()
    )
    try:
        script_path = Path(script_raw).resolve()
    except Exception:
        return 2

    if script_path.name != "intraday_scanner.py":
        return 2
    if not script_path.is_relative_to(root_dir):
        return 2
    if not script_path.is_file():
        return 2

    restore = install_urlopen_patch(gate, opener=opener)
    old_argv = sys.argv[:]
    old_sys_path = sys.path[:]
    try:
        sys.argv = [str(script_path), *[str(x) for x in scanner_args]]
        sys.path.insert(0, str(script_path.parent))
        runpy.run_path(str(script_path), run_name="__main__")
        return 0
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return int(exc.code)
        return 1
    except Exception as exc:
        print(f"[!] 子行程掃描異常: {type(exc).__name__}")
        return 1
    finally:
        try:
            restore()
        finally:
            sys.argv = old_argv
            sys.path[:] = old_sys_path


if __name__ == "__main__":
    sys.exit(main())
