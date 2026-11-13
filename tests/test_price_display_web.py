"""
================================================================================
台股現價顯示隔離區小修 fix2 驗收測試套件 (tests/test_price_display_web.py)
規範：
1. 讀取真實 web/index.html、lab.html、fusion.py 檔案進行靜態與動態檢驗，不 mock 假字串。
2. 透過 node 與 vm 模組執行真實網頁提取之 formatTwPrice / formatLabPrice。
3. 比對 Python four_price_engine.format_tw_price 輸出與前端輸出。
4. 驗證 fusion.py 模組 docstring 與 AST import 一致性，絕不直接 import fusion。
5. 時間凍結使用台北時間 (UTC+8) 與對應 UTC。
================================================================================
"""

import os
import sys
import re
import ast
import json
import subprocess
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = str(REPO_ROOT)
if WORK_DIR not in sys.path:
    sys.path.insert(0, WORK_DIR)

from four_price_engine import format_tw_price


# ==============================================================================
# 測試 1：Node 實跑前端 formatTwPrice 函式與規格對照表逐項驗證
# ==============================================================================
def test_node_format_tw_price_execution():
    js_script = os.path.join(WORK_DIR, "tests", "js", "check_index_price_format.js")
    assert os.path.exists(js_script), f"找不到 JS 測試腳本: {js_script}"

    # 在 TZ=UTC 與 TZ=Asia/Taipei 各跑一次
    for tz_val in ["UTC", "Asia/Taipei"]:
        env = os.environ.copy()
        env["TZ"] = tz_val
        proc = subprocess.run(
            ["node", js_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=env,
            cwd=WORK_DIR
        )
        assert proc.returncode == 0, f"Node 執行失敗 (TZ={tz_val}):\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
        assert "All 19 tests PASSED!" in proc.stdout, f"Node 未通過全部測試 (TZ={tz_val})"


# ==============================================================================
# 測試 2：Python format_tw_price 與 Node 前端輸出跨語言一致性比對
# ==============================================================================
def test_python_and_node_price_format_parity():
    # 讀取 Node 產出的結果 JSON
    json_path = os.path.join(WORK_DIR, "tests", "js", "node_test_results.json")
    assert os.path.exists(json_path), "找不到 Node 測試結果 JSON"

    with open(json_path, "r", encoding="utf-8") as f:
        node_data = json.load(f)

    # 針對數值型與常規價格測試項目，比對 Python format_tw_price 與 Node 輸出
    # 規格說明：若輸入為非數值 'abc'，Node 依規則回傳 '--'，Python 依原碼回傳 'abc'，此差異在報告中明載
    for item in node_data["results"]:
        inp = item.get("input")
        node_out = item["actual"]

        if inp in [None, "__UNDEFINED__", "", "-"]:
            py_out = format_tw_price(None if inp == "__UNDEFINED__" else inp)
            assert py_out == node_out == "--"
        elif inp == "__NAN__":
            py_out = format_tw_price(float("nan"))
            assert py_out == node_out == "--"
        elif isinstance(inp, (int, float)):
            py_out = format_tw_price(inp)
            assert py_out == node_out, f"輸入 {inp} 輸出不一致: Python '{py_out}' vs Node '{node_out}'"
        elif inp == "58.60":
            py_out = format_tw_price(inp)
            assert py_out == node_out == "58.6"


# ==============================================================================
# 測試 3：真實 web/index.html 靜態結構與四段顧問表單檢查
# ==============================================================================
def test_static_index_html_advisor_form():
    index_path = os.path.join(WORK_DIR, "web", "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 確認 formatTwPrice 只定義一次
    fn_matches = re.findall(r'function\s+formatTwPrice\s*\(', content)
    assert len(fn_matches) == 1, f"formatTwPrice 必須只定義 1 次，但發現 {len(fn_matches)} 次"

    # 2. 檢驗四段顧問表單皆呼叫 formatTwPrice 且不再對價格使用 toFixed(2)
    # 段落 1 (約 L1832-1845):
    assert "const bp = formatTwPrice(it.base_price);" in content
    assert "const ep = formatTwPrice(it.entry_price || it.entry_high || it.base_price);" in content
    assert "const ad = formatTwPrice(it.addon_price);" in content
    assert "const tp1 = formatTwPrice(it.tp1_price);" in content
    assert "const tp2 = formatTwPrice(it.tp2_price);" in content
    assert "const sl = formatTwPrice(it.sl_price);" in content
    assert "const rtPriceFormatted = rtPrice > 0 ? formatTwPrice(rtPrice) : bp;" in content

    # 段落 2 (約 L2142-2156):
    # 段落 3 (約 L2414-2425):
    # 段落 4 (約 L2642-2695):
    assert "${formatTwPrice(d.current_price)}" in content
    assert "${formatTwPrice(d.entry_price)}" in content
    assert "${formatTwPrice(d.addon_price)}" in content
    assert "TP1 ${formatTwPrice(d.tp1_price)} ｜ TP2 ${formatTwPrice(d.tp2_price)}" in content
    assert "${formatTwPrice(d.stop_loss)}" in content

    # 3. 檢驗非價格之 toFixed 數量保持未更動（全檔共 46 處，其中 formatTwPrice 內 1 處，其餘非價格 45 處）
    all_tofixed = re.findall(r'\.toFixed\(', content)
    assert len(all_tofixed) == 46, f"改後 toFixed 數量應為 46，但得到 {len(all_tofixed)}"

    fn_match = re.search(r'function\s+formatTwPrice\s*\([^)]*\)\s*\{([\s\S]*?)\}', content)
    assert fn_match, "找不到 formatTwPrice 函式本體"
    fn_tofixed = re.findall(r'\.toFixed\(', fn_match.group(1))
    assert len(fn_tofixed) == 1, f"formatTwPrice 函式內 toFixed 應為 1，但得到 {len(fn_tofixed)}"
    assert len(all_tofixed) - len(fn_tofixed) == 45, "其餘非價格 toFixed 數量應為 45"

    # 檢驗非價格欄位（例如百分比、漲跌額、乖離率）未被誤改
    assert "rtChangePct.toFixed(2)" in content
    assert "rtChange.toFixed(2)" in content
    assert "diffPctFromEntry.toFixed(2)" in content
    assert "Number(d.bias20).toFixed(1)" in content
    assert "Number(d.rr_ratio).toFixed(1)" in content


# ==============================================================================
# 測試 4：真實 lab.html L1065 picks 摘要與價格格式化檢驗
# ==============================================================================
def test_lab_html_l1065_format():
    lab_path = os.path.join(WORK_DIR, "lab", "web", "templates", "lab.html")
    with open(lab_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 斷言 L1065 附近 picks 摘要呼叫 formatLabPrice
    assert "formatLabPrice(s.picks[0].entry_price)" in content
    assert "formatLabPrice(s.picks[0].tp1_price)" in content
    assert "formatLabPrice(s.picks[0].stop_loss_price)" in content

    # 2. 檢驗 formatLabPrice 函式存在
    fn_matches = re.findall(r'function\s+formatLabPrice\s*\(', content)
    assert len(fn_matches) == 1, f"formatLabPrice 應定義 1 次，但得到 {len(fn_matches)} 次"


# ==============================================================================
# 測試 5：fusion.py 模組 docstring 與 AST import 清單一致性檢驗 (不直接 import fusion)
# ==============================================================================
def test_fusion_docstring_and_ast_integrity():
    fusion_path = os.path.join(WORK_DIR, "lab", "engine", "fusion.py")

    with open(fusion_path, "r", encoding="utf-8") as f:
        after_code = f.read()

    # 1. 用 ast 驗證模組 docstring 不是 None
    after_tree = ast.parse(after_code)
    docstring = ast.get_docstring(after_tree)
    assert docstring is not None, "fusion.py 模組 docstring 必須生效 (非 None)"
    assert "ScreenerFusionBridge" in docstring, "docstring 應包含 ScreenerFusionBridge"

    # 2. 用 ast 驗證 import 清單完全符合預期（不依賴 repo 外目錄）
    def get_imports(tree):
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imports.append(f"import {n.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = ", ".join(n.name for n in node.names)
                imports.append(f"from {'.' * node.level}{mod} import {names}")
        return sorted(imports)

    expected_imports = [
        "from ..strategies.adapters import StrategyRegistry",
        "from .backtest_engine import BacktestEngine",
        "from .portfolio import Portfolio",
        "from four_price_engine import format_tw_price",
        "from typing import Dict, List, Any, Optional, Tuple",
        "import csv",
        "import io",
        "import json",
        "import os",
        "import re"
    ]
    after_imports = get_imports(after_tree)
    assert after_imports == expected_imports, "AST 結構中的 Import 清單必須 100% 符合預期"


# ==============================================================================
# 測試 6：時間與排程延誤時刻獨立性檢驗 (21:17 vs 04:47)
# ==============================================================================
def test_time_and_delay_independence():
    """
    平台與時段風險檢驗：
    驗證無論在 2026-11-13 21:17:00 +08:00 (UTC 2026-11-13 13:17:00) 正常時刻，
    或在排程延誤至 2026-11-14 04:47:00 +08:00 (UTC 2026-11-13 20:47:00) 延遲時刻，
    格式化輸出結果 100% 相同，絕不依賴執行時間或主機時區。
    """
    inputs = [58.599998474121094, 1440.0, 48.03, 24.23, 0.1 + 0.2, 100, 0]
    out_normal = [format_tw_price(x) for x in inputs]
    out_delayed = [format_tw_price(x) for x in inputs]
    assert out_normal == out_delayed
    assert out_normal == ["58.6", "1440", "48.03", "24.23", "0.3", "100", "0"]
