#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 全市場盤後宏觀與籌碼分析引擎 (Market Analyst)
================================================================================
本模組依據總指揮中心規範，於台股交易日盤後（每晚 21:15 官方數據全沉澱後）自動執行：
1. 信用交易全解析：
   - 上市融資餘額、今日增減、維持率(估算)、融券增減
   - 上櫃融資餘額、今日增減、維持率(估算)、融券增減
   - 上市櫃合計融資增減幅比率與籌碼動向評估
2. 三大法人買賣超全解析：
   - 外資及陸資、投信、自營商(自行買賣)、自營商(避險)、三大法人合計
3. 大盤技術面與結構分析：
   - 加權指數收盤、跌漲點、幅度
4. 未來台股走勢狀態機研判 (State Machine)：
   - 極短線（T+1 ~ T+3）與波段維度（1~3週）走向研判
   - 資金水位配置與實戰操盤錦囊 (持股者 vs 空手者)
5. 多端輸出：
   - 產出 market_analysis_report.md (高對比戰報)
   - 產出 market_analysis_report.json (供 Web 戰情儀表板讀取)
   - 支援 --line 自動推播視覺化字卡至 LINE / Telegram / Discord
================================================================================
"""

import os
import sys
import json
import datetime
import argparse
import urllib.request
from pathlib import Path

# 最高指揮中心共用守門員與通訊模組
from market_schedule import should_suppress_push
from line_sender import send_to_line

# Windows 命令列編碼保護
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
REPORT_MD_PATH = ROOT_DIR / "market_analysis_report.md"
REPORT_JSON_PATH = ROOT_DIR / "market_analysis_report.json"
CACHE_DIR = ROOT_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

try:
    from market_schedule import should_suppress_push, is_market_open_today, verify_twse_data_settlement
except ImportError:
    def should_suppress_push(force=False, task_type=""):
        return False, "無 market_schedule 模組"
    def is_market_open_today():
        return True, "預設開盤"
    def verify_twse_data_settlement(verbose=False):
        return True

try:
    from line_sender import send_to_line
except ImportError:
    def send_to_line(msg, strategy=None, force=False):
        print(f"[LINE 推播備援] {msg}")
        return True

UNKNOWN = "未知"


def fmt_num(v, spec: str, suffix: str = "") -> str:
    """格式化輔助函式：v 為 None 時回傳 '未知'，否則 format(v, spec) + suffix"""
    if v is None:
        return UNKNOWN
    try:
        return format(v, spec) + suffix
    except Exception:
        return UNKNOWN


def to_roc_date(date_str: str) -> str:
    """轉換 YYYYMMDD 為 ROC 日期格式 (如 115/11/06)"""
    y = int(date_str[:4]) - 1911
    m = int(date_str[4:6])
    d = int(date_str[6:8])
    return f"{y}/{m:02d}/{d:02d}"


def clean_num(val) -> float:
    """清理包含逗號與空白之數字字串為 float"""
    if val is None:
        return 0.0
    s = str(val).replace(",", "").replace(" ", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _get_json(url: str) -> dict:
    """模組層 JSON 網路請求函式，供離線測試 monkeypatch"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_twse_margin(date_str: str) -> dict:
    """抓取證交所上市融資融券彙總統計表 (MI_MARGN)"""
    url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date_str}&selectType=MS&response=json"
    try:
        data = _get_json(url)
    except Exception as e:
        print(f"[!] 抓取 TWSE 信用交易異常: {e}")
        return {}

    if not isinstance(data, dict):
        return {}

    data_date = data.get("date")
    if data_date != date_str:
        return {"data_date": data_date, "error": f"日期不符: 請求 {date_str}, 資料 {data_date}"}

    if data.get("stat") != "OK":
        return {"data_date": data_date, "error": f"stat != OK: {data.get('stat')}"}

    tables = data.get("tables", [])
    if not tables:
        return {"data_date": data_date}

    t0 = tables[0]
    rows = t0.get("data", [])
    result = {"data_date": data_date, "checks": {}}
    for r in rows:
        if len(r) >= 6:
            item_name = r[0]
            if item_name.startswith("融資金額"):
                buy_val = clean_num(r[1])
                sell_val = clean_num(r[2])
                repay_val = clean_num(r[3])
                prev_val = clean_num(r[4])
                today_val = clean_num(r[5])

                prev_amt = prev_val / 100000.0  # 轉為億元
                today_amt = today_val / 100000.0
                diff_amt = today_amt - prev_amt
                pct = (diff_amt / prev_amt * 100.0) if prev_amt > 0 else 0.0

                result["margin_buy_billion"] = round(buy_val / 100000.0, 2)
                result["margin_sell_billion"] = round(sell_val / 100000.0, 2)
                result["margin_repay_billion"] = round(repay_val / 100000.0, 2)
                result["margin_prev_billion"] = round(prev_amt, 2)
                result["margin_today_billion"] = round(today_amt, 2)
                result["margin_diff_billion"] = round(diff_amt, 2)
                result["margin_diff_pct"] = round(pct, 2)

                # 勾稽：前日餘額 + 買進 − 賣出 − 現金償還 == 今日餘額
                result["checks"]["margin_amount"] = (
                    int(prev_val) + int(buy_val) - int(sell_val) - int(repay_val) == int(today_val)
                )

            elif item_name.startswith("融資("):
                buy_lots = clean_num(r[1])
                sell_lots = clean_num(r[2])
                repay_lots = clean_num(r[3])
                prev_lots = clean_num(r[4])
                today_lots = clean_num(r[5])
                diff_lots = today_lots - prev_lots

                result["margin_lots_buy"] = int(buy_lots)
                result["margin_lots_sell"] = int(sell_lots)
                result["margin_lots_repay"] = int(repay_lots)
                result["margin_lots_prev"] = int(prev_lots)
                result["margin_lots_today"] = int(today_lots)
                result["margin_lots_diff"] = int(diff_lots)

                # 勾稽：前日餘額 + 買進 − 賣出 − 現金償還 == 今日餘額
                result["checks"]["margin_lots"] = (
                    int(prev_lots) + int(buy_lots) - int(sell_lots) - int(repay_lots) == int(today_lots)
                )

            elif item_name.startswith("融券("):
                buy_lots = clean_num(r[1])
                sell_lots = clean_num(r[2])
                repay_lots = clean_num(r[3])
                prev_lots = clean_num(r[4])
                today_lots = clean_num(r[5])
                diff_lots = today_lots - prev_lots

                result["short_buy_lots"] = int(buy_lots)
                result["short_sell_lots"] = int(sell_lots)
                result["short_repay_lots"] = int(repay_lots)
                result["short_prev_lots"] = int(prev_lots)
                result["short_today_lots"] = int(today_lots)
                result["short_diff_lots"] = int(diff_lots)

                # 融券列：前日 − 買進(回補) + 賣出 − 現券償還 == 今日
                result["checks"]["short_lots"] = (
                    int(prev_lots) - int(buy_lots) + int(sell_lots) - int(repay_lots) == int(today_lots)
                )

    return result


def fetch_tpex_margin(date_str: str) -> dict:
    """抓取櫃買中心上櫃股票融資融券餘額概況表"""
    roc_date = to_roc_date(date_str)
    url = f"https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&o=json&d={roc_date}"
    try:
        data = _get_json(url)
    except Exception as e:
        print(f"[!] 抓取 TPEx 信用交易異常: {e}")
        return {}

    if not isinstance(data, dict):
        return {}

    data_date = data.get("date")
    if data_date != date_str:
        return {"data_date": data_date, "error": f"日期不符: 請求 {date_str}, 資料 {data_date}"}

    tables = data.get("tables", [])
    if not tables:
        return {"data_date": data_date}

    summary = tables[0].get("summary", [])
    result = {"data_date": data_date, "checks": {}}
    for r in summary:
        if len(r) >= 7:
            item_name = r[1]
            if item_name.startswith("融資金"):
                prev_val = clean_num(r[2])
                buy_val = clean_num(r[3])
                sell_val = clean_num(r[4])
                repay_val = clean_num(r[5])
                today_val = clean_num(r[6])

                prev_amt = prev_val / 100000.0  # 轉為億元
                today_amt = today_val / 100000.0
                diff_amt = today_amt - prev_amt
                pct = (diff_amt / prev_amt * 100.0) if prev_amt > 0 else 0.0

                result["margin_buy_billion"] = round(buy_val / 100000.0, 2)
                result["margin_sell_billion"] = round(sell_val / 100000.0, 2)
                result["margin_repay_billion"] = round(repay_val / 100000.0, 2)
                result["margin_prev_billion"] = round(prev_amt, 2)
                result["margin_today_billion"] = round(today_amt, 2)
                result["margin_diff_billion"] = round(diff_amt, 2)
                result["margin_diff_pct"] = round(pct, 2)

                # 勾稽：融資 前+買−賣−償==今
                result["checks"]["tpex_margin_amount"] = (
                    int(prev_val) + int(buy_val) - int(sell_val) - int(repay_val) == int(today_val)
                )

            elif item_name.startswith("合計"):
                # 融資張數 r[2]..r[6]
                prev_lots = clean_num(r[2])
                buy_lots = clean_num(r[3])
                sell_lots = clean_num(r[4])
                repay_lots = clean_num(r[5])
                today_lots = clean_num(r[6])

                result["margin_lots_buy"] = int(buy_lots)
                result["margin_lots_sell"] = int(sell_lots)
                result["margin_lots_repay"] = int(repay_lots)
                result["margin_lots_prev"] = int(prev_lots)
                result["margin_lots_today"] = int(today_lots)
                result["margin_lots_diff"] = int(today_lots - prev_lots)

                result["checks"]["tpex_margin_lots"] = (
                    int(prev_lots) + int(buy_lots) - int(sell_lots) - int(repay_lots) == int(today_lots)
                )

                # 融券 r[10]=前日, r[11]=券賣, r[12]=券買, r[13]=券償, r[14]=今日
                if len(r) >= 15:
                    prev_short = clean_num(r[10])
                    sell_short = clean_num(r[11])
                    buy_short = clean_num(r[12])
                    repay_short = clean_num(r[13])
                    today_short = clean_num(r[14])
                    diff_short = today_short - prev_short

                    result["short_prev_lots"] = int(prev_short)
                    result["short_today_lots"] = int(today_short)
                    result["short_diff_lots"] = int(diff_short)

                    # 勾稽：融券 前+券賣−券買−券償==今
                    result["checks"]["tpex_short_lots"] = (
                        int(prev_short) + int(sell_short) - int(buy_short) - int(repay_short) == int(today_short)
                    )

    return result


def fetch_twse_institutions(date_str: str) -> dict:
    """抓取證交所三大法人買賣超彙總表 (BFI82U)"""
    url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?date={date_str}&response=json"
    try:
        data = _get_json(url)
    except Exception as e:
        print(f"[!] 抓取 TWSE 三大法人異常: {e}")
        return {}

    if not isinstance(data, dict):
        return {}

    data_date = data.get("date")
    if data_date != date_str:
        return {"data_date": data_date, "error": f"日期不符: 請求 {date_str}, 資料 {data_date}"}

    if data.get("stat") != "OK":
        return {"data_date": data_date, "error": f"stat != OK: {data.get('stat')}"}

    rows = data.get("data", [])
    result = {"data_date": data_date, "checks": {}}
    for r in rows:
        if len(r) >= 4:
            name = r[0]
            diff = clean_num(r[3]) / 100000000.0  # 轉為億元
            if name == "自營商(自行買賣)":
                result["dealer_self_billion"] = round(diff, 2)
            elif name == "自營商(避險)":
                result["dealer_hedge_billion"] = round(diff, 2)
            elif name == "投信":
                result["trust_billion"] = round(diff, 2)
            elif name == "外資自營商":
                continue
            elif name.startswith("外資及陸資"):
                result["foreign_billion"] = round(diff, 2)
            elif name == "合計":
                result["total_billion"] = round(diff, 2)

    # 勾稽：foreign+trust+dealer_self+dealer_hedge 與 total 差距 ≤ 0.02 億 → checks["institution_total"] = True
    required_keys = ["foreign_billion", "trust_billion", "dealer_self_billion", "dealer_hedge_billion", "total_billion"]
    if all(k in result for k in required_keys):
        sub_sum = result["foreign_billion"] + result["trust_billion"] + result["dealer_self_billion"] + result["dealer_hedge_billion"]
        diff_total = abs(sub_sum - result["total_billion"])
        result["checks"]["institution_total"] = (diff_total <= 0.02)

    return result


def fetch_twse_index(date_str: str) -> dict:
    """抓取加權指數收盤行情 (MI_INDEX)"""
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=IND&response=json"
    try:
        data = _get_json(url)
    except Exception as e:
        print(f"[!] 抓取 TWSE 指數行情異常: {e}")
        return {}

    if not isinstance(data, dict):
        return {}

    data_date = data.get("date") or data.get("params", {}).get("date")
    if data_date != date_str:
        return {"data_date": data_date, "error": f"日期不符: 請求 {date_str}, 資料 {data_date}"}

    if data.get("stat") != "OK":
        return {"data_date": data_date, "error": f"stat != OK: {data.get('stat')}"}

    tables = data.get("tables", [])
    result = {"data_date": data_date}
    for t in tables:
        for row in t.get("data", []):
            if len(row) >= 5 and "發行量加權股價指數" in row[0]:
                result["close"] = clean_num(row[1])
                sign = -1.0 if "-" in row[2] else 1.0
                result["diff_points"] = round(sign * abs(clean_num(row[3])), 2)
                result["diff_pct"] = round(sign * abs(clean_num(row[4])), 2)
                break
    return result


def resolve_maintenance_ratios(date_str: str, twse_m: dict, tpex_m: dict, idx_info: dict, use_cache: bool = True) -> tuple:
    """
    動態評估全市場融資維持率 (上市維持率, 上櫃維持率)。
    本數值為估算值，非官方正式公告。
    若快取已存在則直接讀取；若輸入缺失則回傳 (None, None)。
    """
    cache_file = CACHE_DIR / f"margin_ratio_{date_str}.json"
    if use_cache and cache_file.exists():
        try:
            c = json.loads(cache_file.read_text(encoding="utf-8"))
            return c.get("twse_ratio"), c.get("tpex_ratio")
        except Exception:
            pass

    idx_pct = idx_info.get("diff_pct")
    twse_diff_pct = twse_m.get("margin_diff_pct")
    tpex_diff_pct = tpex_m.get("margin_diff_pct")

    if idx_pct is None or twse_diff_pct is None or tpex_diff_pct is None:
        return None, None

    # 依大盤跌幅與融資減幅估算 (估算值，非官方)
    base_twse = 160.0 + (idx_pct * 1.5) + (twse_diff_pct * 0.4)
    base_tpex = 155.0 + (idx_pct * 1.8) + (tpex_diff_pct * 0.5)
    twse_ratio = round(max(110.0, min(180.0, base_twse)), 2)
    tpex_ratio = round(max(105.0, min(180.0, base_tpex)), 2)

    if use_cache:
        try:
            cache_file.write_text(json.dumps({"twse_ratio": twse_ratio, "tpex_ratio": tpex_ratio}), encoding="utf-8")
        except Exception:
            pass

    return twse_ratio, tpex_ratio


def evaluate_market_regime(
    idx_pct: float,
    total_margin_diff_billion: float,
    total_margin_diff_pct: float,
    twse_ratio: float = None,
    tpex_ratio: float = None,
    trust_billion: float = None,
    foreign_billion: float = None,
    short_diff_lots: int = None,
) -> dict:
    """
    多空走向與狀態機定性研判 (State Machine Analyzer)
    綜合：指數跌幅、融資減幅倍率、法人對倒動能
    """
    if idx_pct is None or total_margin_diff_billion is None or total_margin_diff_pct is None:
        keys = [
            "panic_level",
            "margin_status",
            "short_term_outlook",
            "mid_term_outlook",
            "cash_ratio_guide",
            "holder_guide",
            "buyer_guide",
        ]
        return {k: "未知（官方資料不足）" for k in keys}

    # 恐慌等級判定 (不得再用維持率 twse_ratio / tpex_ratio 判定恐慌或斷頭)
    d = total_margin_diff_billion
    pct = total_margin_diff_pct
    if d < 0:
        m = f"融資單日減少 {abs(d):.2f} 億（{pct:+.2f}%）"
    elif d > 0:
        m = f"融資單日增加 {d:.2f} 億（{pct:+.2f}%）"
    else:
        m = "融資持平"

    if total_margin_diff_billion <= -400.0 or total_margin_diff_pct <= -5.0:
        panic_level = "🚨 5級極致恐慌（融資單日大減）"
        margin_status = m + "・去槓桿賣壓沉重"
    elif total_margin_diff_billion <= -200.0:
        panic_level = "⚠️ 4級高度警戒（融資大幅減少）"
        margin_status = m
    elif total_margin_diff_billion <= -80.0:
        panic_level = "🟡 3級中度警戒（融資明顯減少）"
        margin_status = m
    else:
        panic_level = "🟢 1-2級平穩（融資變動溫和）"
        margin_status = m

    # 未來趨勢走向研判
    if total_margin_diff_billion <= -300.0 and idx_pct <= -3.0:
        if short_diff_lots is None:
            short_phrase = "融券增減未知"
        elif short_diff_lots < 0:
            short_phrase = f"融券減少 {int(abs(short_diff_lots)):,} 張（空方回補）"
        elif short_diff_lots > 0:
            short_phrase = f"融券增加 {int(short_diff_lots):,} 張（空方加碼）"
        else:
            short_phrase = "融券持平"

        if trust_billion is None:
            trust_phrase = "投信動向未知"
        elif trust_billion > 0:
            trust_phrase = f"投信買超 {trust_billion:.2f} 億（逆勢吃貨）"
        elif trust_billion < 0:
            trust_phrase = f"投信賣超 {abs(trust_billion):.2f} 億"
        else:
            trust_phrase = "投信持平"

        if (short_diff_lots is not None and short_diff_lots < 0) and (trust_billion is not None and trust_billion > 0):
            tail = "融券回補與投信買超同時出現，留意技術性反彈機會。"
        elif short_diff_lots is not None and short_diff_lots < 0:
            tail = "空方回補與法人承接尚未同時出現，反彈訊號未確認。"
        else:
            tail = "空方與法人承接尚未同時出現，反彈訊號未確認。"

        short_term_outlook = (
            f"🟢【超跌反彈觀察】指數 {idx_pct:+.2f}%，{m}；{short_phrase}，{trust_phrase}。" + tail
        )
        mid_term_outlook = "🟡【中期待確認】單日籌碼資料無法判斷中期趨勢，需觀察後續數日融資與法人動向。"
        cash_ratio_guide = "70% ~ 80% (嚴守高現金防禦水位)"
        holder_guide = "避免在融資大減的恐慌時段倉促殺出；若後續出現反彈，再逢高減碼弱勢融資股。"

        conds = "融資大幅減少"
        if trust_billion is not None and trust_billion > 0:
            conds += " + 投信買超"
        if short_diff_lots is not None and short_diff_lots < 0:
            conds += " + 融券回補"
        conds += " + 收長下影線"
        buyer_guide = f"右側出訊號才進場！鎖定【{conds}】之超跌龍頭股，小額現股短打，停損嚴設 -5%。"
    elif idx_pct < 0 and total_margin_diff_billion < 0:
        short_term_outlook = f"🟡【弱勢修正】指數 {idx_pct:+.2f}%，{m}，關注前波低點支撐。"
        mid_term_outlook = "🟡【中期待確認】單日籌碼資料無法判斷中期趨勢，需觀察後續數日融資與法人動向。"
        cash_ratio_guide = "50% ~ 60%"
        holder_guide = "汰弱留強，嚴守停損點位。"
        buyer_guide = "逢拉回測試支撐不破時，小量分批布局。"
    else:
        if trust_billion is not None and foreign_billion is not None:
            s = foreign_billion + trust_billion
            if s > 0:
                inst = f"外資＋投信買超 {s:.2f} 億"
            elif s < 0:
                inst = f"外資＋投信賣超 {abs(s):.2f} 億"
            else:
                inst = "外資＋投信持平"
        else:
            inst = "法人動向未知"

        if idx_pct > 0 and total_margin_diff_billion > 0:
            label = "🔵【多方偏強】"
        elif idx_pct >= 0:
            label = "🟡【指數持穩】"
        else:
            label = "🟠【指數下跌・融資逆增】"

        short_term_outlook = f"{label}指數 {idx_pct:+.2f}%，{m}，{inst}。"
        mid_term_outlook = "🟡【中期待確認】單日籌碼資料無法判斷中期趨勢，需觀察後續數日融資與法人動向。"
        cash_ratio_guide = "20% ~ 30% (高持股水位)"
        holder_guide = "移動停利續抱飆股。"
        buyer_guide = "順勢尋找突破起漲標的。"

    return {
        "panic_level": panic_level,
        "margin_status": margin_status,
        "short_term_outlook": short_term_outlook,
        "mid_term_outlook": mid_term_outlook,
        "cash_ratio_guide": cash_ratio_guide,
        "holder_guide": holder_guide,
        "buyer_guide": buyer_guide,
    }


def run_market_analysis(
    date_str: str = None,
    line: bool = False,
    force: bool = False,
    send_line: bool = None,
    output_dir: Path = None,
    use_cache: bool = True,
) -> dict:
    if send_line is not None:
        line = send_line
    """執行全市場盤後分析主程序"""
    if not date_str:
        date_str = datetime.datetime.now().strftime("%Y%m%d")

    roc_date = to_roc_date(date_str)
    date_display = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:8]}"

    target_dir = Path(output_dir) if output_dir else ROOT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    report_md_path = target_dir / "market_analysis_report.md"
    report_json_path = target_dir / "market_analysis_report.json"

    print("\n" + "=" * 70)
    print(f"📊 [台股盤後總體宏觀與籌碼分析] 日期: {date_display} ({roc_date})")
    print("=" * 70)

    # 1. 檢查開盤守門員
    if not force:
        suppress, reason = should_suppress_push(force=force, task_type="盤後總體分析")
        if suppress:
            print(f"[⏸️ 跳過執行] {reason}")
            return {"status": "skipped", "reason": reason}

    # 2. 抓取官方各維度數據
    print("[*] 正在連線臺灣證券交易所與櫃買中心官方 API...")
    twse_m = fetch_twse_margin(date_str)
    tpex_m = fetch_tpex_margin(date_str)
    inst = fetch_twse_institutions(date_str)
    idx = fetch_twse_index(date_str)

    # 3. 信用交易合併精算 (刪除所有寫死預設值，缺值 = None)
    twse_margin_bal = twse_m.get("margin_today_billion")
    twse_margin_diff = twse_m.get("margin_diff_billion")
    twse_short_bal = twse_m.get("short_today_lots")
    twse_short_diff = twse_m.get("short_diff_lots")

    tpex_margin_bal = tpex_m.get("margin_today_billion")
    tpex_margin_diff = tpex_m.get("margin_diff_billion")
    tpex_short_bal = tpex_m.get("short_today_lots")
    tpex_short_diff = tpex_m.get("short_diff_lots")

    if twse_margin_bal is not None and tpex_margin_bal is not None:
        total_margin_bal = round(twse_margin_bal + tpex_margin_bal, 2)
    else:
        total_margin_bal = None

    if twse_margin_diff is not None and tpex_margin_diff is not None:
        total_margin_diff = round(twse_margin_diff + tpex_margin_diff, 2)
    else:
        total_margin_diff = None

    twse_prev = twse_m.get("margin_prev_billion")
    tpex_prev = tpex_m.get("margin_prev_billion")
    if twse_prev is not None and tpex_prev is not None:
        total_prev_margin = twse_prev + tpex_prev
    else:
        total_prev_margin = None

    if total_margin_diff is not None and total_prev_margin is not None and total_prev_margin > 0:
        total_margin_pct = round((total_margin_diff / total_prev_margin * 100.0), 2)
    else:
        total_margin_pct = None

    if twse_short_diff is not None and tpex_short_diff is not None:
        total_short_diff = twse_short_diff + tpex_short_diff
    else:
        total_short_diff = None

    # 維持率結算
    twse_ratio, tpex_ratio = resolve_maintenance_ratios(date_str, twse_m, tpex_m, idx, use_cache=use_cache)

    # 4. 法人買賣超結算
    foreign_bill = inst.get("foreign_billion")
    trust_bill = inst.get("trust_billion")
    dealer_self_bill = inst.get("dealer_self_billion")
    dealer_hedge_bill = inst.get("dealer_hedge_billion")
    inst_total_bill = inst.get("total_billion")

    # 5. 指數行情結算
    idx_close = idx.get("close")
    idx_diff = idx.get("diff_points")
    idx_pct = idx.get("diff_pct")

    # 6. 狀態機定性評估
    regime = evaluate_market_regime(
        idx_pct=idx_pct,
        total_margin_diff_billion=total_margin_diff,
        total_margin_diff_pct=total_margin_pct,
        twse_ratio=twse_ratio,
        tpex_ratio=tpex_ratio,
        trust_billion=trust_bill,
        foreign_billion=foreign_bill,
        short_diff_lots=total_short_diff,
    )

    # 收集勾稽與資料日期
    checks = {}
    if "checks" in twse_m and isinstance(twse_m["checks"], dict):
        checks.update(twse_m["checks"])
    if "checks" in tpex_m and isinstance(tpex_m["checks"], dict):
        checks.update(tpex_m["checks"])
    if "checks" in inst and isinstance(inst["checks"], dict):
        checks.update(inst["checks"])

    data_dates = {
        "twse_margin": twse_m.get("data_date"),
        "tpex_margin": tpex_m.get("data_date"),
        "institutions": inst.get("data_date"),
        "index": idx.get("data_date"),
    }

    _date_labels = {"twse_margin": "上市信用", "tpex_margin": "上櫃信用", "institutions": "三大法人", "index": "加權指數"}
    data_dates_str = "、".join(f"{_date_labels[k]} {v or UNKNOWN}" for k, v in data_dates.items())

    # 勾稽結果狀態字串
    if any(v is False for v in checks.values()):
        failed_items = [k for k, v in checks.items() if v is False]
        checks_status_str = f"不一致: {', '.join(failed_items)}"
    elif not checks or len(checks) < 7:
        checks_status_str = "未知"
    else:
        checks_status_str = "通過"

    report_data = {
        "date": date_display,
        "roc_date": roc_date,
        "index": {
            "close": idx_close,
            "diff_points": idx_diff,
            "diff_pct": idx_pct,
        },
        "margin": {
            "twse_balance": twse_margin_bal,
            "twse_diff": f"{twse_margin_diff:+.2f} 億" if twse_margin_diff is not None else UNKNOWN,
            "twse_ratio": twse_ratio,
            "twse_short_diff": twse_short_diff,
            "tpex_balance": tpex_margin_bal,
            "tpex_diff": f"{tpex_margin_diff:+.2f} 億" if tpex_margin_diff is not None else UNKNOWN,
            "tpex_ratio": tpex_ratio,
            "tpex_short_diff": tpex_short_diff,
            "total_balance": total_margin_bal,
            "total_diff": (
                f"{total_margin_diff:+.2f} 億 ({total_margin_pct:+.2f}%)"
                if (total_margin_diff is not None and total_margin_pct is not None)
                else (f"{total_margin_diff:+.2f} 億" if total_margin_diff is not None else UNKNOWN)
            ),
            "total_short_diff": total_short_diff,
            "ratio_is_estimate": True,
        },
        "institutions": {
            "foreign": f"{foreign_bill:+.2f} 億" if foreign_bill is not None else UNKNOWN,
            "trust": f"{trust_bill:+.2f} 億" if trust_bill is not None else UNKNOWN,
            "dealer_self": f"{dealer_self_bill:+.2f} 億" if dealer_self_bill is not None else UNKNOWN,
            "dealer_hedge": f"{dealer_hedge_bill:+.2f} 億" if dealer_hedge_bill is not None else UNKNOWN,
            "total": f"{inst_total_bill:+.2f} 億" if inst_total_bill is not None else UNKNOWN,
        },
        "regime": regime,
        "data_dates": data_dates,
        "checks": checks,
        "numbers": {
            "twse_margin_balance": twse_margin_bal,
            "twse_margin_diff": twse_margin_diff,
            "tpex_margin_balance": tpex_margin_bal,
            "tpex_margin_diff": tpex_margin_diff,
            "twse_short_diff": twse_short_diff,
            "tpex_short_diff": tpex_short_diff,
            "total_short_diff": total_short_diff,
            "foreign": foreign_bill,
            "trust": trust_bill,
            "dealer_self": dealer_self_bill,
            "dealer_hedge": dealer_hedge_bill,
            "inst_total": inst_total_bill,
        },
    }

    # 7. 產出 Markdown 戰報
    def get_trend_str(val):
        if val is None:
            return UNKNOWN
        if val > 0:
            return "增加"
        if val < 0:
            return "減少"
        return "持平"

    def format_diff_cell(val, pct=None):
        if val is None:
            return UNKNOWN
        pct_str = f" ({pct:+.2f}%)" if pct is not None else ""
        if val > 0:
            return f'<font color="red">**{val:+.2f} 億{pct_str}**</font>'
        elif val < 0:
            return f'<font color="green">**{val:+.2f} 億{pct_str}**</font>'
        else:
            return f'**0.00 億{pct_str}**'

    def format_inst_row(name, val):
        if val is None:
            amount_str = UNKNOWN
            desc = UNKNOWN
        else:
            if val > 0:
                amount_str = f'<font color="red">**{val:+.2f} 億**</font>'
                desc = "買超"
            elif val < 0:
                amount_str = f'<font color="green">**{val:+.2f} 億**</font>'
                desc = "賣超"
            else:
                amount_str = f'**{val:+.2f} 億**'
                desc = "持平"
        return f"| **{name}** | {amount_str} | {desc} |"

    # 量化解析段落
    if total_margin_pct is not None and idx_pct is not None:
        if idx_pct != 0:
            multiple_str = f"（約為大盤漲跌幅的 {abs(total_margin_pct / idx_pct):.2f} 倍）"
            margin_analysis = f"今日融資增減幅為 **{total_margin_pct:+.2f}%**{multiple_str}。"
        else:
            margin_analysis = f"今日融資增減幅為 **{total_margin_pct:+.2f}%**。"
    elif total_margin_pct is not None:
        margin_analysis = f"今日融資增減幅為 **{total_margin_pct:+.2f}%**。"
    else:
        margin_analysis = f"今日融資增減幅為 **{UNKNOWN}**。"

    if total_short_diff is None:
        short_analysis = "融券增減未知。"
    elif total_short_diff > 0:
        short_analysis = f"融券增加 {abs(total_short_diff):,} 張。"
    elif total_short_diff < 0:
        short_analysis = f"融券減少 {abs(total_short_diff):,} 張。"
    else:
        short_analysis = "融券持平。"

    md_content = f"""# 🏛️ 台股盤後總體宏觀與籌碼全戰報 ({date_display})

> **【資料日期】**：{data_dates_str} | **【資料勾稽】**：{checks_status_str}  
> **【大盤定位】**：加權指數收盤 **{fmt_num(idx_close, ',.2f')}** 點（**{fmt_num(idx_diff, '+,.2f')}** 點 / **{fmt_num(idx_pct, '+.2f', '%')}**）  
> **【恐慌等級】**：**{regime['panic_level']}**  
> **【籌碼本質】**：{regime['margin_status']}  
> **【持股水位建議】**：🛡️ **{regime['cash_ratio_guide']}**

---

### 一、信用交易全市場監控 (Margin Trading Monitor)

| 市場類別 | 融資餘額 (億元) | 單日融資增減 | 融資維持率(估算) | 融券增減 (張) | 融資動向 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **上市 (TWSE)** | **{fmt_num(twse_margin_bal, ',.2f', ' 億')}** | {format_diff_cell(twse_margin_diff)} | **{fmt_num(twse_ratio, '.2f', '%')}** | **{fmt_num(twse_short_diff, '+,', ' 張')}** | {get_trend_str(twse_margin_diff)} |
| **上櫃 (TPEx)** | **{fmt_num(tpex_margin_bal, ',.2f', ' 億')}** | {format_diff_cell(tpex_margin_diff)} | **{fmt_num(tpex_ratio, '.2f', '%')}** | **{fmt_num(tpex_short_diff, '+,', ' 張')}** | {get_trend_str(tpex_margin_diff)} |
| **上市櫃合計** | **{fmt_num(total_margin_bal, ',.2f', ' 億')}** | {format_diff_cell(total_margin_diff, total_margin_pct)} | — | **{fmt_num(total_short_diff, '+,', ' 張')}** | {get_trend_str(total_margin_diff)} |

> 💡 **量化解析**：{margin_analysis} {short_analysis}

---

### 二、三大法人買賣超全景 (Institutional Trading Flow)

| 法人機構別 | 買賣超金額 (億元) | 籌碼博弈與主力動向解讀 |
| :--- | :---: | :--- |
{format_inst_row("外資及陸資", foreign_bill)}
{format_inst_row("投信 (本土基金)", trust_bill)}
{format_inst_row("自營商 (避險)", dealer_hedge_bill)}
{format_inst_row("自營商 (自行買賣)", dealer_self_bill)}
{format_inst_row("三大法人合計", inst_total_bill)}

---

### 三、未來盤勢走向研判 (Market Outlook)

* 🟢 **極短線走向（T+1 ~ T+3）**：  
  {regime['short_term_outlook']}
* 🟡 **波段趨勢走向（未來 1 ~ 3 週）**：  
  {regime['mid_term_outlook']}

---

### 四、實戰操盤錦囊與風控紀律 (Tactical Playbook)

* 🛡️ **【持股滿檔 / 融資受傷者】**：  
  {regime['holder_guide']}
* 🎯 **【空手者 / 欲搶反彈者】**：  
  {regime['buyer_guide']}
* ⚖️ **【核心紀律】**：  
  「不開槓桿、不盲目抄底、右側出訊號再動作；敬畏市場，生存第一！」
"""

    report_md_path.write_text(md_content, encoding="utf-8")
    report_json_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[✓] 成功產出盤後戰報: {report_md_path}")
    print(f"[✓] 成功產出儀表板 JSON: {report_json_path}")

    # 8. LINE / Telegram 推播
    if line:
        if total_short_diff is None:
            short_line = "• 融券增減: 合計 未知"
        elif total_short_diff > 0:
            short_line = f"• 融券增減: 合計 {total_short_diff:+,} 張（增加）"
        elif total_short_diff < 0:
            short_line = f"• 融券增減: 合計 {total_short_diff:+,} 張（減少）"
        else:
            short_line = "• 融券增減: 合計 0 張（持平）"

        line_clean_text = (
            f"🏛️ 台股盤後總體宏觀戰報 ({date_display})\n\n"
            f"🎯 加權指數: {fmt_num(idx_close, ',.2f')} ({fmt_num(idx_diff, '+,.2f')} / {fmt_num(idx_pct, '+.2f', '%')})\n"
            f"🚨 恐慌等級: {regime['panic_level']}\n"
            f"🛡️ 建議持股水位: {regime['cash_ratio_guide']}\n\n"
            f"📊【全市場信用交易】\n"
            f"• 上市融資: {fmt_num(twse_margin_bal, ',.1f', '億')} ({fmt_num(twse_margin_diff, '+.1f', '億')}) 維持率 {fmt_num(twse_ratio, '.1f', '%')}(估算)\n"
            f"• 上櫃融資: {fmt_num(tpex_margin_bal, ',.1f', '億')} ({fmt_num(tpex_margin_diff, '+.1f', '億')}) 維持率 {fmt_num(tpex_ratio, '.1f', '%')}(估算)\n"
            f"• 上市櫃合計: {fmt_num(total_margin_diff, '+.1f', '億')}" + (f" ({fmt_num(total_margin_pct, '+.2f', '%')})" if total_margin_pct is not None else "") + "\n"
            f"{short_line}\n\n"
            f"💼【三大法人買賣超】\n"
            f"• 外資: {fmt_num(foreign_bill, '+.1f', '億')}\n"
            f"• 投信: {fmt_num(trust_bill, '+.1f', '億')}\n"
            f"• 自營自行: {fmt_num(dealer_self_bill, '+.1f', '億')}\n"
            f"• 自營避險: {fmt_num(dealer_hedge_bill, '+.1f', '億')}\n"
            f"• 合計: {fmt_num(inst_total_bill, '+.1f', '億')}\n\n"
            f"💡【後續走向研判】\n"
            f"• 極短線: {regime['short_term_outlook']}\n"
            f"• 波段: {regime['mid_term_outlook']}\n\n"
            f"🎯【操盤對策】\n"
            f"• 空手者: {regime['buyer_guide']}\n"
            f"• 持股者: {regime['holder_guide']}"
        )
        try:
            send_to_line(line_clean_text, strategy="00", force=force)
            print("[✓] 成功推播台股大盤盤後分析至通訊頻道！")
        except Exception as e_push:
            print(f"[!] LINE 推播跳過或失敗: {e_push}")

    return report_data


def main():
    parser = argparse.ArgumentParser(description="台股盤後總體宏觀與籌碼分析引擎")
    parser.add_argument("--date", type=str, default=None, help="指定日期 YYYYMMDD (預設當日)")
    parser.add_argument("--line", action="store_true", help="執行後推播分析結果至通訊頻道")
    parser.add_argument("--force", action="store_true", help="強制執行，忽略休市守衛檢查")
    args = parser.parse_args()

    run_market_analysis(date_str=args.date, line=args.line, force=args.force)


if __name__ == "__main__":
    main()
