# -*- coding: utf-8 -*-
"""
tests/test_run_all_cli.py
全策略排程總調度器 (run_all.py) 與策略 CLI 命令列介面測試套件
"""
import ast
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import run_all


def test_strategy09_accepts_run_all_args():
    """驗證策略 09 選股器正確解析 run_all 所傳入之 --export 與 --line 等參數"""
    path = ROOT_DIR / "strategies" / "09_treasury_stock_buyback" / "screener.py"
    assert path.exists(), f"策略 09 腳本不存在: {path}"

    spec = importlib.util.spec_from_file_location("screener_09", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod, "build_arg_parser"), "策略 09 screener 必須宣告 build_arg_parser 函式"
    p = mod.build_arg_parser()

    # 1. 帶 --export <file> 與 --line
    args1 = p.parse_args(["--export", "result.csv", "--line"])
    assert args1.export == "result.csv"
    assert args1.line is True

    # 2. 空參數預設值
    args2 = p.parse_args([])
    assert args2.export == mod.CSV_FILE
    assert args2.no_export is False

    # 3. 單獨傳 --export（不帶值，nargs=? const 預設輸出 CSV_FILE）
    args3 = p.parse_args(["--export"])
    assert args3.export == mod.CSV_FILE


def test_all_strategies_declare_run_all_flags():
    """驗證 run_all.STRATEGIES 中所有策略腳本皆宣告 --export、--line 與 --force 參數"""
    for strat in run_all.STRATEGIES:
        script_path = strat["script"]
        assert script_path.exists(), f"策略 {strat['id']} 腳本不存在: {script_path}"

        tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
        flags = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "add_argument":
                    if node.args:
                        first_arg = node.args[0]
                        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                            flags.add(first_arg.value)
                        elif hasattr(ast, "Str") and isinstance(first_arg, ast.Str):
                            flags.add(first_arg.s)

        for req_flag in ["--export", "--line", "--force"]:
            assert req_flag in flags, f"策略 {strat['id']} ({script_path}) 必須宣告 {req_flag}，目前宣告: {flags}"


class DummySubprocessResult:
    def __init__(self, returncode: int, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def _setup_mock_environment(monkeypatch, returncode: int, stderr_msg: str):
    """設置 run_all 執行之全域 mock，杜絕子程序啟動與外部連線"""
    def mock_subprocess_run(cmd, cwd=None, stderr=None, text=None, encoding=None, errors=None, **kwargs):
        return DummySubprocessResult(returncode=returncode, stderr=stderr_msg)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    # 守門員與分析引擎 mock
    import market_schedule
    monkeypatch.setattr(market_schedule, "check_market_open_or_exit", lambda *args, **kwargs: True)
    monkeypatch.setattr(market_schedule, "verify_twse_data_settlement", lambda *args, **kwargs: None)

    import market_analyst
    monkeypatch.setattr(market_analyst, "run_market_analysis", lambda *args, **kwargs: {})

    # 模擬模組注入，避免執行真實 radar / lab / cmoney 運作
    dummy_radar = types.ModuleType("radar_manager")
    dummy_radar.rollover_watchlist_defense = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "radar_manager", dummy_radar)

    dummy_cmoney = types.ModuleType("cmoney_sync")
    dummy_cmoney.sync_stocks_to_cmoney = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "cmoney_sync", dummy_cmoney)

    dummy_storage = types.ModuleType("lab.data.storage")
    dummy_storage.MarketStorage = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "lab.data.storage", dummy_storage)

    dummy_loader = types.ModuleType("lab.data.loader")
    dummy_loader.MarketDataLoader = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "lab.data.loader", dummy_loader)

    dummy_fusion = types.ModuleType("lab.engine.fusion")
    class DummyBridge:
        @staticmethod
        def generate_next_day_advisor_report(*args, **kwargs):
            # 刻意拋錯：讓 run_all 跳過寫入 lab/data/tomorrow_trading_advisor.csv，測試不落地任何檔案
            raise RuntimeError("stubbed lab bridge (test)")
        @staticmethod
        def format_advisor_line_text(*args, **kwargs):
            return ""
    dummy_fusion.ScreenerFusionBridge = DummyBridge
    monkeypatch.setitem(sys.modules, "lab.engine.fusion", dummy_fusion)


def test_run_all_returns_nonzero_on_failure(monkeypatch):
    """驗證任一策略執行失敗時，main() 結束碼為 1 且總結包含錯誤記錄與 stderr"""
    _setup_mock_environment(monkeypatch, returncode=3, stderr_msg="boom")
    monkeypatch.setattr(sys, "argv", ["run_all.py", "--strategy", "09"])

    exit_code = run_all.main()
    assert exit_code == 1

    assert any(
        r["returncode"] == 3 and "boom" in r.get("stderr", "")
        for r in run_all.LAST_RESULTS
    ), f"LAST_RESULTS 應記錄 returncode 3 與 boom: {run_all.LAST_RESULTS}"


def test_run_all_returns_zero_on_success(monkeypatch):
    """驗證策略全數成功時，main() 結束碼為 0"""
    _setup_mock_environment(monkeypatch, returncode=0, stderr_msg="")
    monkeypatch.setattr(sys, "argv", ["run_all.py", "--strategy", "09"])

    exit_code = run_all.main()
    assert exit_code == 0
    assert all(r["returncode"] == 0 for r in run_all.LAST_RESULTS)


def test_build_strategy_cmd():
    """驗證 build_strategy_cmd 建構之指令結構正確性"""
    strat_info = {
        "id": "09_treasury_stock_buyback",
        "name": "策略 09",
        "script": ROOT_DIR / "strategies" / "09_treasury_stock_buyback" / "screener.py",
        "export": "result.csv",
    }
    cmd = run_all.build_strategy_cmd(strat_info, line=False, force=False)
    assert "--export" in cmd
    assert "result.csv" in cmd
    assert "--line" not in cmd
    assert "--force" not in cmd

    cmd_line = run_all.build_strategy_cmd(strat_info, line=True, force=False)
    assert "--export" in cmd_line
    assert "--line" in cmd_line

    cmd_force = run_all.build_strategy_cmd(strat_info, line=False, force=True)
    assert "--force" in cmd_force
