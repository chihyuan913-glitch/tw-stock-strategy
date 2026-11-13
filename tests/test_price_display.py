"""
================================================================================
台股顧問「優先買進」現價來源與價格格式化離線驗收測試套件
測試環境：Windows 11 / Python 3.13 / pytest
時區規範：凍結時間一律對齊台北時間 (Asia/Taipei, UTC+8)，並顯式註記對應之 UTC 時間。
執行範圍：嚴格限定於隔離工作目錄 (C:\\tw-backup\\tw-stock-price-display-20261113\\work)，禁止碰觸正式目錄。
================================================================================
"""

import os
import sys
import csv
import io
import math
import datetime
from unittest.mock import patch, MagicMock
import pytest
import numpy as np

# 加入 work 目錄至 sys.path
WORK_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORK_DIR not in sys.path:
    sys.path.insert(0, WORK_DIR)

from four_price_engine import format_tw_price, format_price, format_twse_price
from line_sender import format_price_val
from lab.engine.fusion import ScreenerFusionBridge, _safe_float


# ==============================================================================
# 測試項目 1：格式化單元 (Requirement 1)
# 輸入 58.599998474121094 (float(numpy.float32(58.6))) 輸出必須是 58.6，不得為 58.60 或長殘值
# ==============================================================================
def test_requirement_1_format_tw_price_unit():
    # 模擬 float32 殘值
    raw_val = float(np.float32(58.6))
    assert raw_val == 58.599998474121094

    res = format_tw_price(raw_val)
    assert res == "58.6", f"預期 '58.6'，但得到 '{res}'"
    assert res != "58.60", "升降單位為 0.10，末尾 0 必須去除"
    assert res != "58.599998474121094", "不得輸出 float32 殘值"

    # 更多測試案例
    assert format_tw_price(114.5) == "114.5"
    assert format_tw_price(167.0) == "167"
    assert format_tw_price(520.0) == "520"
    assert format_tw_price(9.50) == "9.5"
    assert format_tw_price(3.96) == "3.96"

    # 空值與無效值安全保底
    assert format_tw_price(None) == "--"
    assert format_tw_price("") == "--"
    assert format_tw_price("-") == "--"
    assert format_tw_price(float("nan")) == "--"
    assert format_tw_price(0.0) == "0"


# ==============================================================================
# 測試項目 2：浮點誤差消除 (Requirement 2)
# 輸入 0.1 + 0.2 (0.30000000000000004) 輸出必須是 0.3，不得留下長小數
# ==============================================================================
def test_requirement_2_floating_point_ieee_error():
    val = 0.1 + 0.2
    assert val == 0.30000000000000004

    res = format_tw_price(val)
    assert res == "0.3", f"預期 '0.3'，但得到 '{res}'"
    assert "0000" not in res, "不得殘留 IEEE 754 浮點長小數"
    assert len(res) == 3


# ==============================================================================
# 測試項目 3：高價股不含強制千分位或多餘小數零 (Requirement 3)
# 輸入 1440 或 1440.0 (如 8996) 輸出必須是 1440，不得為 1440.0、1440.00 或 1,440.00
# ==============================================================================
def test_requirement_3_high_price_stock_no_commas_or_extra_decimals():
    res_int = format_tw_price(1440)
    res_float = format_tw_price(1440.0)

    assert res_int == "1440", f"預期 '1440'，但得到 '{res_int}'"
    assert res_float == "1440", f"預期 '1440'，但得到 '{res_float}'"
    assert res_float != "1440.0", "整數價位不得保留 .0"
    assert res_float != "1440.00", "整數價位不得保留 .00"
    assert res_float != "1,440.00", "股價不得強制加上千分位逗號"
    assert res_float != "1,440", "股價不得強制加上千分位逗號"

    # 其他高價股檢驗
    assert format_tw_price(2800.0) == "2800"
    assert format_tw_price(1050.0) == "1050"


# ==============================================================================
# 測試項目 4：必須保留分位的價格精確保留 (Requirement 4)
# 48.03、24.23 輸出仍為 48.03、24.23，不得被四捨五入成 48.0 或 24.2
# ==============================================================================
def test_requirement_4_cent_precision_preserved():
    res_00911 = format_tw_price(48.03)
    res_other = format_tw_price(24.23)

    assert res_00911 == "48.03", f"預期 '48.03'，但得到 '{res_00911}'"
    assert res_other == "24.23", f"預期 '24.23'，但得到 '{res_other}'"
    assert res_00911 != "48.0", "分位價位不得遺失第二位小數"
    assert res_other != "24.2", "分位價位不得遺失第二位小數"
    assert format_tw_price(12.05) == "12.05"


# ==============================================================================
# 測試項目 5：策略 12 假價格反例徹底阻斷 (Requirement 5)
# 準備一份只含真實欄位（有 昨日收盤=47.5、動態公允開盤價=52.0，沒有 收盤價）的 00911 CSV
# 呼叫 parse_strategy_result_csv，00911 不得出現 100/102/108/115/95 或 2,300 股，必須跳過
# ==============================================================================
def test_requirement_5_strategy_12_fake_price_counterexample_blocked(tmp_path):
    # 建立無收盤價的 00911 舊版 result.csv 夾具
    fake_csv_content = (
        "股票代碼,股票名稱,市場,事件類型,昨日收盤,動態公允開盤價,預估漲跌幅(%),海外複合錨定,實戰操盤指引\n"
        "00911,兆豐洲際半導體,tse,🎯 實盤鎖定・折價低接潛力,47.5,52.0,+9.47%,SOXX.US,海外母體強勁...\n"
    )
    s12_dir = tmp_path / "strategies" / "12_etf_event_radar"
    s12_dir.mkdir(parents=True)
    fake_csv_file = s12_dir / "result.csv"
    fake_csv_file.write_text(fake_csv_content, encoding="utf-8-sig")

    meta = {
        "id": "12",
        "folder": "12_etf_event_radar",
        "name": "策略 12：全台 ETF 跨市場折溢價事件雷達",
        "lab_strategy_id": "02",
        "scenario": "trend_up",
    }

    with patch("lab.engine.fusion.STRATEGIES_DIR", str(tmp_path / "strategies")):
        res = ScreenerFusionBridge.parse_strategy_result_csv(meta)

    # 驗證：由於取不到收盤價，00911 必須直接跳過，picks 必須為空
    assert len(res["picks"]) == 0, f"無收盤價標的必須跳過，但 picks 包含: {res['picks']}"

    # 驗證 recommendations 流程中絕不得出現 100/102/108/115/95 或 2,300 股
    rec_line = []
    for p in res["picks"]:
        if p["symbol"] == "00911":
            rec_line.append(p)
    assert len(rec_line) == 0

    # 產生推播與 CSV 模擬
    fake_report = {"market_regime": {}, "recommendations": []}
    line_text = ScreenerFusionBridge.format_advisor_line_text(fake_report)
    assert "00911" not in line_text
    assert "100.0" not in line_text
    assert "2,300" not in line_text


# ==============================================================================
# 測試項目 6：策略 12 有可信收盤 48.03 時正確產出 (Requirement 6)
# result.csv 含有 48.03，顧問現價為 48.03，進場/股數不再為 100 系列
# ==============================================================================
def test_requirement_6_strategy_12_with_credible_close_price(tmp_path):
    # 建立包含今日收盤價 48.03 的修正版 result.csv 夾具
    csv_content = (
        "股票代碼,股票名稱,市場,事件類型,收盤價,最新現價,昨日收盤,動態公允開盤價,預估漲跌幅(%),海外複合錨定,實戰操盤指引\n"
        "00911,兆豐洲際半導體,tse,🎯 實盤鎖定・折價低接潛力,48.03,48.03,47.5,52.0,+9.47%,SOXX.US,海外母體強勁...\n"
    )
    s12_dir = tmp_path / "strategies" / "12_etf_event_radar"
    s12_dir.mkdir(parents=True)
    csv_file = s12_dir / "result.csv"
    csv_file.write_text(csv_content, encoding="utf-8-sig")

    meta = {
        "id": "12",
        "folder": "12_etf_event_radar",
        "name": "策略 12：全台 ETF 跨市場折溢價事件雷達",
        "lab_strategy_id": "02",
        "scenario": "trend_up",
    }

    with patch("lab.engine.fusion.STRATEGIES_DIR", str(tmp_path / "strategies")):
        res = ScreenerFusionBridge.parse_strategy_result_csv(meta)

    assert len(res["picks"]) == 1
    pick = res["picks"][0]
    assert pick["symbol"] == "00911"
    assert pick["close_price"] == 48.03, "現價必須精確解析為今日收盤價 48.03"
    assert pick["close_price"] != 100.0, "絕不可為假價格 100.0"
    assert pick["close_price"] != 47.5, "不可誤用昨日收盤價 47.5"
    assert pick["close_price"] != 52.0, "不可誤用動態公允開盤價 52.0"

    # 模擬顧問 recommendations 結構檢驗
    rec = {
        "priority": 1,
        "composite_rank": 88.5,
        "symbol": "00911",
        "name": "兆豐洲際半導體",
        "source_strategy": meta["name"],
        "tomorrow_action": "🟢 優先買進 (STRONG BUY)",
        "verdict": "實盤高分 × 回測高勝率共振",
        "close_price": format_tw_price(pick["close_price"]),
        "entry_price": format_tw_price(pick["entry_price"]),
        "addon_price": format_tw_price(pick["addon_price"]),
        "tp1_price": format_tw_price(pick["tp1_price"]),
        "tp2_price": format_tw_price(pick["tp2_price"]),
        "stop_loss_price": format_tw_price(pick["stop_loss_price"]),
        "rr_ratio": 2.2,
        "expectancy_pct": 5.4,
        "recommended_shares": 4000,
        "hist_return_pct": 14.8,
        "hist_win_rate_pct": 66.7,
        "hist_sharpe": 1.45,
        "overnight_risk": "🟢主力籌碼沉澱",
        "action_plan_zh": f"【明日首選買進】於 ${format_tw_price(pick['close_price'])} ~ ${format_tw_price(pick['entry_price'])} 掛限價單建倉（建議 4,000 股）。",
    }

    # 寫入 CSV 並真實讀回解析
    advisor_csv = tmp_path / "tomorrow_trading_advisor.csv"
    with open(advisor_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rec.keys()))
        writer.writeheader()
        writer.writerow(rec)

    with open(advisor_csv, "r", encoding="utf-8-sig") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        row = reader[0]
        assert row["close_price"] == "48.03"
        assert row["close_price"] != "100.0"
        assert row["close_price"] != "100"

    # 測試推播文字
    report_dict = {"market_regime": {"regime": "多頭攻勢", "bias_desc": "大盤偏多"}, "recommendations": [rec]}
    line_msg = ScreenerFusionBridge.format_advisor_line_text(report_dict)
    assert "（現價 $48.03）" in line_msg
    assert "100.0" not in line_msg
    assert "2,300 股" not in line_msg


# ==============================================================================
# 測試項目 7：21:15 盤後時點凍結驗證 (Requirement 7)
# 凍結時間為台北時間 2026-11-13 21:17:00 (UTC 2026-11-13 13:17:00)
# 策略 12 寫檔路徑 (result.csv / result.md) 必須寫出今日收盤價 48.03，不得只寫昨日收盤
# ==============================================================================
def test_requirement_7_afterhours_2115_strategy12_writes_today_close(tmp_path):
    import importlib
    s12_screener = importlib.import_module("strategies.12_etf_event_radar.screener")

    # 凍結時間：台北時間 2026-11-13 21:17:00 (UTC+8) -> 對應 UTC: 2026-11-13 13:17:00
    tz_taipei = datetime.timezone(datetime.timedelta(hours=8))
    frozen_taipei_time = datetime.datetime(2026, 11, 13, 21, 17, 0, tzinfo=tz_taipei)
    frozen_utc_time = frozen_taipei_time.astimezone(datetime.timezone.utc)
    assert frozen_utc_time == datetime.datetime(2026, 11, 13, 13, 17, 0, tzinfo=datetime.timezone.utc)

    # 模擬 00911 行情數據：昨日收盤 47.5，今日收盤 48.03 (證交所今日真實數字)
    mock_quotes = {
        "00911": {
            "code": "00911",
            "open_price": 48.78,
            "close_price": 48.03,
            "yesterday_close": 47.50,
            "volume": 1200,
            "market": "tse"
        }
    }
    mock_universe = {
        "00911": {
            "code": "00911",
            "name": "兆豐洲際半導體",
            "market": "tse",
            "anchor": "SOXX.US",
            "category": "OVERSEAS_EQUITY"
        }
    }

    test_csv_path = tmp_path / "result.csv"
    test_md_path = tmp_path / "result.md"

    # 建立 mock datetime
    class MockDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz:
                return frozen_taipei_time.astimezone(tz)
            return datetime.datetime(2026, 11, 13, 21, 17, 0)

    with patch.object(s12_screener.datetime, "datetime", MockDatetime), \
         patch.object(s12_screener, "load_etf_universe", return_value=mock_universe), \
         patch.object(s12_screener, "fetch_batch_quotes", return_value=mock_quotes), \
         patch.object(s12_screener, "calculate_dynamic_theo_chg", return_value=(9.47, "費半SOXX +9.47%")), \
         patch.object(s12_screener, "RESULT_CSV", str(test_csv_path)), \
         patch.object(s12_screener, "RESULT_MD", str(test_md_path)), \
         patch.object(s12_screener, "PREMARKET_FILE", str(tmp_path / "premarket.json")):

        candidates = s12_screener.run_screener(send_line=False, export_path=str(test_csv_path))

    assert len(candidates) >= 1
    assert candidates[0]["code"] == "00911"
    assert candidates[0]["close_price"] == 48.03

    # 檢驗真實寫出的 result.csv
    assert test_csv_path.exists()
    with open(test_csv_path, "r", encoding="utf-8-sig") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) >= 1
        r00911 = next(r for r in reader if r["股票代碼"] == "00911")
        assert "收盤價" in r00911, "表頭必須包含 收盤價"
        assert "最新現價" in r00911, "表頭必須包含 最新現價"
        assert r00911["收盤價"] == "48.03", f"收盤價欄位必須為今日收盤 48.03，但得到 {r00911['收盤價']}"
        assert r00911["昨日收盤"] == "47.5"
        assert r00911["動態公允開盤價"] == "52.0"

    # 檢驗真實寫出的 result.md
    assert test_md_path.exists()
    with open(test_md_path, "r", encoding="utf-8") as f:
        md_text = f.read()
        assert "| 今日收盤 | 昨日收盤 |" in md_text, "Markdown 表頭必須包含 今日收盤 欄位"
        assert "| 48.03 | 47.5 |" in md_text, "Markdown 表身必須正確填入 48.03"


# ==============================================================================
# 測試項目 8：2607 榮運 float32 殘值全輸出檢驗 (Requirement 8)
# close=58.599998474121094 產生顧問 LINE 文字與 CSV，現價欄與「現價 $…」均為 58.6
# ==============================================================================
def test_requirement_8_stock_2607_float32_residue_full_output(tmp_path):
    raw_residue = float(np.float32(58.6)) # 58.599998474121094

    rec = {
        "priority": 1,
        "composite_rank": 92.0,
        "symbol": "2607",
        "name": "榮運",
        "source_strategy": "策略08・台股25大集團作帳母子共振",
        "tomorrow_action": "🟢 優先買進 (STRONG BUY)",
        "verdict": "實盤高分 × 回測高勝率共振",
        "close_price": format_tw_price(raw_residue),
        "entry_price": format_tw_price(58.6),
        "addon_price": format_tw_price(60.1),
        "tp1_price": format_tw_price(63.3),
        "tp2_price": format_tw_price(68.0),
        "stop_loss_price": format_tw_price(55.7),
        "rr_ratio": 2.1,
        "expectancy_pct": 6.2,
        "recommended_shares": 1000,
        "hist_return_pct": 18.5,
        "hist_win_rate_pct": 71.4,
        "hist_sharpe": 1.62,
        "overnight_risk": "🟢主力籌碼沉澱",
        "action_plan_zh": f"【明日首選買進】於 ${format_tw_price(raw_residue)} ~ ${format_tw_price(58.6)} 掛限價單建倉（建議 1,000 股）。",
    }

    # 1. 寫入 CSV 檔案並真實讀回
    csv_file = tmp_path / "advisor_2607.csv"
    with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rec.keys()))
        writer.writeheader()
        writer.writerow(rec)

    with open(csv_file, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        assert len(rows) == 1
        r = rows[0]
        assert r["close_price"] == "58.6", f"CSV 現價欄必須為 '58.6'，但得到 '{r['close_price']}'"
        assert r["entry_price"] == "58.6"
        assert r["close_price"] != "58.60"
        assert r["close_price"] != "58.599998474121094"
        assert "$58.6" in r["action_plan_zh"]
        assert "58.599998474121094" not in r["action_plan_zh"]

    # 2. 檢驗 LINE 推播文字輸出
    report_dict = {"market_regime": {"regime": "多頭攻勢", "bias_desc": "大盤偏多"}, "recommendations": [rec]}
    line_text = ScreenerFusionBridge.format_advisor_line_text(report_dict)
    assert "（現價 $58.6）" in line_text, f"LINE 文字必須包含 '（現價 $58.6）'，但得到:\n{line_text}"
    assert "建議進場：$58.6" in line_text
    assert "58.599998474121094" not in line_text, "LINE 文字絕對不得出現 float32 殘值"
    assert "58.60" not in line_text, "LINE 文字不得出現多餘的 0"


# ==============================================================================
# 測試項目 9：line_sender.format_price_val 雙軌一致性檢驗 (Requirement 9)
# 升級後的 format_price_val 必須通過第 1–4 項所有測試
# ==============================================================================
def test_requirement_9_format_price_val_parity():
    # 檢驗第 1 項：float32 殘值
    raw_val = float(np.float32(58.6))
    assert format_price_val(raw_val) == "58.6"
    assert format_price_val(raw_val) != "58.60"
    assert format_price_val(raw_val) != "58.599998474121094"

    # 檢驗第 2 項：浮點誤差
    assert format_price_val(0.1 + 0.2) == "0.3"
    assert "0000" not in format_price_val(0.1 + 0.2)

    # 檢驗第 3 項：高價股整數
    assert format_price_val(1440) == "1440"
    assert format_price_val(1440.0) == "1440"
    assert format_price_val(1440.0) != "1440.00"
    assert format_price_val(1440.0) != "1,440.00"

    # 檢驗第 4 項：分位精確保留
    assert format_price_val(48.03) == "48.03"
    assert format_price_val(24.23) == "24.23"
    assert format_price_val(48.03) != "48.0"

    # 空值處理
    assert format_price_val(None) == "--"
    assert format_price_val("") == "--"
    assert format_price_val("-") == "--"
