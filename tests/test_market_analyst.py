# -*- coding: utf-8 -*-
"""
tests/test_market_analyst.py
台股盤後宏觀與信用交易全戰報分析引擎離線測試套件
"""
import json
import pytest
from pathlib import Path

import market_analyst

ROOT_DIR = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures" / "market_20261111"


def load_fixture_json(filename: str) -> dict:
    path = FIXTURE_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def default_market_mocks(monkeypatch):
    twse_margin = load_fixture_json("twse_margin.json")
    tpex_margin = load_fixture_json("tpex_margin.json")
    twse_inst = load_fixture_json("twse_inst.json")
    twse_index = load_fixture_json("twse_index.json")

    def mock_get_json(url: str):
        if "MI_MARGN" in url:
            return json.loads(json.dumps(twse_margin))
        elif "margin_bal_result" in url:
            return json.loads(json.dumps(tpex_margin))
        elif "BFI82U" in url:
            return json.loads(json.dumps(twse_inst))
        elif "MI_INDEX" in url:
            return json.loads(json.dumps(twse_index))
        raise ValueError(f"Unexpected URL in mock_get_json: {url}")

    def fail_send_to_line(msg, strategy=None, force=False, fallback_text=None):
        raise AssertionError("send_to_line 嚴禁在無顯式 mock 下於測試中被調用！")

    monkeypatch.setattr(market_analyst, "_get_json", mock_get_json)
    monkeypatch.setattr(market_analyst, "send_to_line", fail_send_to_line)
    monkeypatch.setattr(market_analyst, "should_suppress_push", lambda force=False, task_type="": (False, ""))


def test_to_roc_date():
    """驗證西元 YYYYMMDD 轉民國年 ROC 格式轉換"""
    assert market_analyst.to_roc_date("20261106") == "115/11/06"
    assert market_analyst.to_roc_date("20240101") == "113/01/01"


def test_clean_num():
    """驗證字串數值清理 (移除逗號、百分比與空白)"""
    assert market_analyst.clean_num("1,234,567.89") == 1234567.89
    assert market_analyst.clean_num("-12.5%") == -12.5
    assert market_analyst.clean_num("  98.5 ") == 98.5
    assert market_analyst.clean_num(None) == 0.0
    assert market_analyst.clean_num("invalid") == 0.0


def test_recompute_20261111(tmp_path):
    """驗證 20261111 官方數據完全解析與數值精確度"""
    res = market_analyst.run_market_analysis(
        date_str="20261111",
        line=False,
        force=True,
        output_dir=tmp_path,
        use_cache=False,
    )
    nums = res["numbers"]
    assert nums["foreign"] == -7.66
    assert nums["trust"] == 185.85
    assert nums["dealer_self"] == 28.01
    assert nums["dealer_hedge"] == 34.56
    assert nums["inst_total"] == 240.76
    assert nums["tpex_margin_balance"] == 1718.02
    assert nums["tpex_margin_diff"] == 24.94
    assert nums["twse_margin_balance"] == 5411.58
    assert nums["twse_margin_diff"] == 44.79
    assert nums["total_short_diff"] == 2113

    md_text = (tmp_path / "market_analysis_report.md").read_text(encoding="utf-8")
    assert "融券增加 2,113 張" in md_text
    assert "斷頭" not in md_text
    assert "上櫃信用 20261111" in md_text
    assert "【資料勾稽】**：通過" in md_text

    assert len(res["checks"]) == 7
    assert all(res["checks"].values()) is True
    assert all(v == "20261111" for v in res["data_dates"].values())


def test_line_text_20261111(tmp_path, monkeypatch):
    """驗證 LINE 推播字卡大字體排版、估算標記與動態描述"""
    collected = []

    def capture_send_to_line(msg, strategy=None, force=False, fallback_text=None):
        collected.append(msg)
        return True

    monkeypatch.setattr(market_analyst, "send_to_line", capture_send_to_line)

    market_analyst.run_market_analysis(
        date_str="20261111",
        line=True,
        force=True,
        output_dir=tmp_path,
        use_cache=False,
    )

    assert len(collected) == 1
    msg = collected[0]
    assert "自營自行: +28.0億" in msg
    assert "(估算)" in msg
    assert "（增加）" in msg
    assert "逆勢護盤" not in msg


def test_missing_fields_show_unknown(tmp_path, monkeypatch):
    """驗證官方欄位缺失時正確顯示未知且無寫死舊備援數字"""
    twse_margin = load_fixture_json("twse_margin.json")
    tpex_margin = load_fixture_json("tpex_margin.json")
    twse_inst = load_fixture_json("twse_inst.json")
    twse_index = load_fixture_json("twse_index.json")

    # 移除 tpex "融資金(仟元)" 列
    tpex_copy = json.loads(json.dumps(tpex_margin))
    summary = tpex_copy["tables"][0]["summary"]
    tpex_copy["tables"][0]["summary"] = [r for r in summary if not r[1].startswith("融資金")]

    # 移除 BFI82U 的 "外資及陸資" 列
    twse_inst_copy = json.loads(json.dumps(twse_inst))
    data_rows = twse_inst_copy["data"]
    twse_inst_copy["data"] = [r for r in data_rows if not r[0].startswith("外資及陸資")]

    def mock_get_json(url: str):
        if "MI_MARGN" in url:
            return twse_margin
        elif "margin_bal_result" in url:
            return tpex_copy
        elif "BFI82U" in url:
            return twse_inst_copy
        elif "MI_INDEX" in url:
            return twse_index
        raise ValueError(url)

    monkeypatch.setattr(market_analyst, "_get_json", mock_get_json)

    res = market_analyst.run_market_analysis(
        date_str="20261111",
        line=False,
        force=True,
        output_dir=tmp_path,
        use_cache=False,
    )

    assert res["numbers"]["tpex_margin_balance"] is None
    assert res["numbers"]["foreign"] is None
    assert res["margin"]["tpex_balance"] is None
    assert res["institutions"]["foreign"] == "未知"

    md_text = (tmp_path / "market_analysis_report.md").read_text(encoding="utf-8")
    json_text = (tmp_path / "market_analysis_report.json").read_text(encoding="utf-8")

    assert "未知" in md_text

    old_numbers = [
        "1699.74", "140.63", "5340.53", "423.56", "911.88",
        "266.91", "43.09", "451.67", "1139.73", "39701.64",
        "152.14", "144.24"
    ]
    for num in old_numbers:
        assert num not in md_text, f"舊備援數字 {num} 不應出現在 Markdown 中"
        assert num not in json_text, f"舊備援數字 {num} 不應出現在 JSON 中"


def test_all_sources_down(tmp_path, monkeypatch):
    """驗證所有 API 來源均失敗時優雅降級為未知且不拋出例外"""
    def mock_fail(url: str):
        raise ConnectionError("官方 API 伺服器無回應")

    monkeypatch.setattr(market_analyst, "_get_json", mock_fail)

    res = market_analyst.run_market_analysis(
        date_str="20261111",
        line=False,
        force=True,
        output_dir=tmp_path,
        use_cache=False,
    )

    assert res is not None
    assert all(v is None for v in res["numbers"].values())
    assert "未知" in res["regime"]["panic_level"]

    md_text = (tmp_path / "market_analysis_report.md").read_text(encoding="utf-8")
    json_text = (tmp_path / "market_analysis_report.json").read_text(encoding="utf-8")

    old_numbers = [
        "1699.74", "140.63", "5340.53", "423.56", "911.88",
        "266.91", "43.09", "451.67", "1139.73", "39701.64",
        "152.14", "144.24"
    ]
    for num in old_numbers:
        assert num not in md_text, f"舊備援數字 {num} 不應出現在 Markdown 中"
        assert num not in json_text, f"舊備援數字 {num} 不應出現在 JSON 中"


def test_date_mismatch_is_unknown(tmp_path, monkeypatch):
    """驗證官方回傳資料日期與請求不符時視為無資料"""
    twse_margin = load_fixture_json("twse_margin.json")
    tpex_margin = load_fixture_json("tpex_margin.json")
    twse_inst = load_fixture_json("twse_inst.json")
    twse_index = load_fixture_json("twse_index.json")

    twse_margin_mismatch = json.loads(json.dumps(twse_margin))
    twse_margin_mismatch["date"] = "20261110"

    tpex_margin_mismatch = json.loads(json.dumps(tpex_margin))
    tpex_margin_mismatch["date"] = "20261110"

    twse_inst_mismatch = json.loads(json.dumps(twse_inst))
    twse_inst_mismatch["date"] = "20261110"

    twse_index_mismatch = json.loads(json.dumps(twse_index))
    twse_index_mismatch["date"] = "20261110"

    def mock_get_json(url: str):
        if "MI_MARGN" in url:
            return twse_margin_mismatch
        elif "margin_bal_result" in url:
            return tpex_margin_mismatch
        elif "BFI82U" in url:
            return twse_inst_mismatch
        elif "MI_INDEX" in url:
            return twse_index_mismatch
        raise ValueError(url)

    monkeypatch.setattr(market_analyst, "_get_json", mock_get_json)

    res = market_analyst.run_market_analysis(
        date_str="20261111",
        line=False,
        force=True,
        output_dir=tmp_path,
        use_cache=False,
    )

    assert all(v is None for v in res["numbers"].values())


def test_regime_ignores_ratio():
    """驗證狀態機評估恐慌等級時不再受維持率高低干擾"""
    res = market_analyst.evaluate_market_regime(
        idx_pct=-0.5,
        total_margin_diff_billion=-10.0,
        total_margin_diff_pct=-0.2,
        twse_ratio=100.0,
        tpex_ratio=100.0,
        trust_billion=10.0,
        foreign_billion=-10.0,
        short_diff_lots=0,
    )
    panic = res["panic_level"]
    assert "斷頭" not in panic
    assert "5級" not in panic


def test_level5_cover_and_trust_buy():
    """驗證 Level 5 融券回補且投信買超時，觸發回補與吃貨字眼且無斷頭，恐慌等級為 5 級"""
    res = market_analyst.evaluate_market_regime(
        idx_pct=-4.0,
        total_margin_diff_billion=-450.0,
        total_margin_diff_pct=-6.0,
        short_diff_lots=-5000,
        trust_billion=50.0,
    )
    joined = " ".join(str(v) for v in res.values())
    assert "回補" in joined
    assert "吃貨" in joined
    assert "斷頭" not in joined
    assert res["panic_level"].startswith("🚨 5級")


def test_level5_no_cover_no_trust_buy():
    """驗證 Level 5 融券增加且投信賣超時，無回補、無吃貨、無斷頭，含融券增加、投信賣超與反彈訊號未確認"""
    res = market_analyst.evaluate_market_regime(
        idx_pct=-4.0,
        total_margin_diff_billion=-450.0,
        total_margin_diff_pct=-6.0,
        short_diff_lots=3000,
        trust_billion=-20.0,
    )
    joined = " ".join(str(v) for v in res.values())
    assert "回補" not in joined
    assert "吃貨" not in joined
    assert "斷頭" not in joined
    assert "融券增加" in joined
    assert "投信賣超" in joined
    assert "反彈訊號未確認" in joined


def test_level5_unknown_flows():
    """驗證 Level 5 籌碼流向未知時，顯示未知字樣且無回補、吃貨、斷頭"""
    res = market_analyst.evaluate_market_regime(
        idx_pct=-4.0,
        total_margin_diff_billion=-450.0,
        total_margin_diff_pct=-6.0,
        short_diff_lots=None,
        trust_billion=None,
    )
    joined = " ".join(str(v) for v in res.values())
    assert "融券增減未知" in joined
    assert "投信動向未知" in joined
    assert "回補" not in joined
    assert "吃貨" not in joined
    assert "斷頭" not in joined


def test_low_ratio_never_triggers_liquidation():
    """驗證極低維持率但融資與指數跌幅輕微時，絕不觸發斷頭字樣或 5 級恐慌"""
    res = market_analyst.evaluate_market_regime(
        idx_pct=-0.5,
        total_margin_diff_billion=-10.0,
        total_margin_diff_pct=-0.2,
        twse_ratio=120.0,
        tpex_ratio=110.0,
    )
    joined = " ".join(str(v) for v in res.values())
    assert "斷頭" not in joined
    assert "5級" not in res["panic_level"]


def test_no_duan_tou_anywhere():
    """驗證多維參數網格下回傳之字典全文絕不出現『斷頭』"""
    margin_values = [-500.0, -350.0, -250.0, -100.0, 0.0, 50.0]
    short_values = [-1, 1, None]
    trust_values = [-1.0, 1.0, None]

    for m in margin_values:
        pct = (m / 7000.0) * 100.0
        for s in short_values:
            for t in trust_values:
                res = market_analyst.evaluate_market_regime(
                    idx_pct=-4.0,
                    total_margin_diff_billion=m,
                    total_margin_diff_pct=pct,
                    short_diff_lots=s,
                    trust_billion=t,
                )
                full_text = " ".join(str(v) for v in res.values())
                assert "斷頭" not in full_text, f"Found '斷頭' in regime for m={m}, s={s}, t={t}: {res}"


def test_no_unsupported_claims_grid():
    """驗證多維參數網格下回傳之字典全文絕不出現無數據支援之固定論述"""
    forbidden = ["斷頭", "追繳", "斷尾", "均線", "套牢", "量價俱揚", "同步偏多", "下週初", "報復性", "日線"]
    for margin in [-500, -350, -250, -100, -10, 0, 50]:
        pct = margin / 100.0
        for idx in [-4, -1, 0, 1.5]:
            for short in [-1, 1, None]:
                for trust in [-1, 1, None]:
                    for foreign in [-2, 2, None]:
                        res = market_analyst.evaluate_market_regime(
                            idx_pct=idx,
                            total_margin_diff_billion=margin,
                            total_margin_diff_pct=pct,
                            short_diff_lots=short,
                            trust_billion=trust,
                            foreign_billion=foreign,
                        )
                        joined = " ".join(str(v) for v in res.values())
                        for word in forbidden:
                            assert word not in joined, f"Found '{word}' for margin={margin}, idx={idx}, short={short}, trust={trust}, foreign={foreign}: {joined}"
                        if trust is None or trust <= 0:
                            assert "吃貨" not in joined, f"Found '吃貨' when trust={trust}: {joined}"
                        if short is None or short >= 0:
                            assert "回補" not in joined, f"Found '回補' when short={short}: {joined}"


def test_level4_uses_real_amount():
    """驗證 Level 4 使用真實融資金額與警戒字樣"""
    res = market_analyst.evaluate_market_regime(
        idx_pct=-1.0,
        total_margin_diff_billion=-250.0,
        total_margin_diff_pct=-1.5,
    )
    assert res["panic_level"].startswith("⚠️ 4級")
    assert "250.00 億" in res["margin_status"]


def test_up_branch_selling_not_bullish():
    """驗證指數上漲但法人賣超時不誤判為多方偏強或買超"""
    res = market_analyst.evaluate_market_regime(
        idx_pct=1.0,
        total_margin_diff_billion=-50.0,
        total_margin_diff_pct=-0.5,
        trust_billion=-10.0,
        foreign_billion=-20.0,
    )
    outlook = res["short_term_outlook"]
    assert "賣超 30.00 億" in outlook
    assert "買超" not in outlook
    assert "多方偏強" not in outlook


def test_up_branch_buying():
    """驗證指數上漲且融資與法人同步增加時標註多方偏強與買超金額"""
    res = market_analyst.evaluate_market_regime(
        idx_pct=1.0,
        total_margin_diff_billion=30.0,
        total_margin_diff_pct=0.4,
        trust_billion=10.0,
        foreign_billion=5.0,
    )
    outlook = res["short_term_outlook"]
    assert "多方偏強" in outlook
    assert "買超 15.00 億" in outlook

