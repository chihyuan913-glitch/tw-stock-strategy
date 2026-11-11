# -*- coding: utf-8 -*-
"""審查修正單元測試。

不連網、不執行 screener main、不匯入 pandas/yfinance。
驗證 screener.py AST 結構與 inject_market_pulse_js.py 之現金顯示邏輯。
"""
import ast
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_screener_main_ast_structure():
    screener_path = ROOT / "strategies" / "01_bollinger_reversal" / "screener.py"
    source = screener_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    main_fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )

    try_nodes = [node for node in main_fn.body if isinstance(node, ast.Try)]

    def has_is_defensive_assign(try_node: ast.Try) -> bool:
        for sub in ast.walk(try_node):
            if isinstance(sub, ast.Assign):
                for target in sub.targets:
                    if isinstance(target, ast.Name) and target.id == "is_defensive":
                        return True
        return False

    def has_flatten_call(try_node: ast.Try) -> bool:
        for sub in ast.walk(try_node):
            if isinstance(sub, ast.Call):
                if isinstance(sub.func, ast.Name) and sub.func.id == "flatten_effective_regime":
                    return True
        return False

    defensive_try = next(t for t in try_nodes if has_is_defensive_assign(t))
    flatten_try = next(t for t in try_nodes if has_flatten_call(t))

    # (a) flatten_effective_regime 的呼叫不在包含 is_defensive 指派的那個 Try 節點內
    assert not has_flatten_call(defensive_try)

    # (b) 它所在的 Try 節點位於該 Try 之後
    idx_defensive = main_fn.body.index(defensive_try)
    idx_flatten = main_fn.body.index(flatten_try)
    assert idx_flatten > idx_defensive

    # (c) 它的 except handler 把 cash_ratio_str 設為 "未知"
    handler_sets_unknown = False
    for handler in flatten_try.handlers:
        for stmt in handler.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == "cash_ratio_str":
                        if isinstance(stmt.value, ast.Constant) and stmt.value.value == "未知":
                            handler_sets_unknown = True
    assert handler_sets_unknown

    # (d) is_defensive 那段 Try 內仍有收緊與自適應參數邏輯（用 ast.unparse 比對字串）
    defensive_unparse = ast.unparse(defensive_try).replace("'", '"')
    assert "args.max_dist = min(args.max_dist, 0.5)" in defensive_unparse
    assert "args.strict_candle = True" in defensive_unparse
    assert '"min_volume" in adap_p' in defensive_unparse
    assert '"bb_lower_band_offset_pct" in adap_p' in defensive_unparse


def test_inject_market_pulse_js_text():
    js_path = ROOT / "scripts" / "inject_market_pulse_js.py"
    content = js_path.read_text(encoding="utf-8")

    # 斷言不再含 cash_target_pct || 50
    assert "cash_target_pct || 50" not in content

    # 且含對 null/undefined 的判斷與 未知
    assert "null" in content
    assert "undefined" in content
    assert "未知" in content


def test_inject_market_pulse_js_node_eval():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("系統未安裝 node，跳過 js 運算式求值測試")

    js_path = ROOT / "scripts" / "inject_market_pulse_js.py"
    lines = js_path.read_text(encoding="utf-8").splitlines()
    l55 = next(
        line.strip() for line in lines
        if "cashTargetEl" in line and "cash_target_pct" in line
    )

    js_code = f"""
function evaluate(val) {{
    const data = {{ cash_target_pct: val }};
    let cashTargetEl = {{}};
    {l55}
    return cashTargetEl.innerText;
}}
const results = [
    evaluate(null),
    evaluate(undefined),
    evaluate(0),
    evaluate(45)
];
console.log(JSON.stringify(results));
"""
    proc = subprocess.run(
        [node_bin, "-e", js_code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    results = json.loads(proc.stdout)
    assert "未知" in results[0]
    assert "未知" in results[1]
    assert "0%" in results[2]
    assert "45%" in results[3]
