# -*- coding: utf-8 -*-
"""儀表板資料契約。只做純計算，不連線、不選股、不寫檔、不推播。

公開站部署與本倉庫提交不必相同。這裡不讀取線上設定，也不回填舊結果列的追溯欄位。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Mapping, Optional, Sequence

TAIPEI = dt.timezone(dt.timedelta(hours=8))
TIMEZONE_NAME = "Asia/Taipei"

# 與既有首頁占位同一組數字，只允許在來源標成 hardcoded_fallback 時出現。
HARDCODED_TWII = {
    "close_raw": 46292.93,
    "prev_close": 46822.08,
    "change": -529.15,
    "change_pct": -1.13,
    "ma20": 48170.13,
    "ma60": 46113.09,
}

STUDIO_PREVIEW_LIMIT = 5
ADVISOR_PER_STRATEGY_CAP = 2
DEPLOYMENT_SHA_ENV_KEYS = (
    "RENDER_GIT_COMMIT",
    "GIT_COMMIT",
    "SOURCE_VERSION",
    "GITHUB_SHA",
)
PROVENANCE_FIELDS = (
    "strategy_version",
    "config_hash",
    "batch_id",
    "generated_at",
    "rule_snapshot",
)


def now_taipei(now: Optional[dt.datetime] = None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(TAIPEI)
    if now.tzinfo is None:
        return now.replace(tzinfo=TAIPEI)
    return now.astimezone(TAIPEI)


def isoformat_taipei(now: Optional[dt.datetime] = None) -> str:
    return now_taipei(now).isoformat(timespec="seconds")


def parse_iso_date(value: Any) -> Optional[dt.date]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return None
    text = text.replace("/", "-").replace(".", "-")
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 8 and "-" not in text:
        text = f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def last_trading_day(day: dt.date, holidays: Optional[Iterable[dt.date]] = None) -> dt.date:
    """最近一個非週末、非已知休市日。只使用呼叫端給的休市日，不連線。"""
    closed = set(holidays or ())
    cursor = day
    for _ in range(366):
        if cursor.weekday() < 5 and cursor not in closed:
            return cursor
        cursor -= dt.timedelta(days=1)
    return day


def classify_session_freshness(
    as_of: Any,
    now: Optional[dt.datetime] = None,
    holidays: Optional[Iterable[dt.date]] = None,
) -> dict:
    """週末或已知休市日沿用最近交易日，不把該日標成過期。沒有資料日則是未知，不是過期。"""
    observed = now_taipei(now)
    today = observed.date()
    holiday_dates = set(holidays or ())
    expected = last_trading_day(today, holiday_dates)
    parsed = parse_iso_date(as_of)
    base = {
        "timezone": TIMEZONE_NAME,
        "observed_at": isoformat_taipei(observed),
        "expected_session": expected.isoformat(),
        "as_of": parsed.isoformat() if parsed else None,
        "calendar_today_is_session": today == expected,
    }
    if parsed is None:
        base.update({
            "freshness": "unknown",
            "stale": False,
            "reason": "沒有可驗證的資料日，不以日曆日差標成過期",
        })
        return base
    if parsed == expected:
        base.update({
            "freshness": "ok",
            "stale": False,
            "reason": "對齊最近交易日（含週末與已知休市沿用）",
        })
        return base
    if parsed < expected:
        base.update({
            "freshness": "stale",
            "stale": True,
            "reason": "早於最近交易日",
        })
        return base
    base.update({
        "freshness": "unknown",
        "stale": False,
        "reason": "資料日晚於最近交易日，不改寫成已驗證",
    })
    return base


def module_status(
    *,
    error: Optional[str] = None,
    count: Optional[int] = None,
    as_of: Any = None,
    now: Optional[dt.datetime] = None,
    holidays: Optional[Iterable[dt.date]] = None,
) -> dict:
    """錯誤、零筆、過期分開，不互相覆蓋。"""
    freshness = classify_session_freshness(as_of, now=now, holidays=holidays)
    if error:
        return {
            **freshness,
            "status": "error",
            "error": str(error),
            "empty": False,
            "stale": False,
            "freshness": "unknown",
        }
    empty = count == 0
    status = "empty" if empty else freshness["freshness"]
    return {
        **freshness,
        "status": status,
        "error": None,
        "empty": empty,
        "stale": bool(freshness["stale"]),
    }


def deployment_identity(environ: Optional[Mapping[str, str]] = None) -> dict:
    import os
    env = environ if environ is not None else os.environ
    for key in DEPLOYMENT_SHA_ENV_KEYS:
        raw = env.get(key)
        if raw and str(raw).strip():
            return {
                "deployment_sha": str(raw).strip(),
                "deployment_sha_source": key,
                "deployment_sha_status": "available",
                "deployment_matches_repo": None,
                "note": "僅代表此程序環境變數中的提交。不能由此證明公開站與本倉庫一致。",
            }
    return {
        "deployment_sha": None,
        "deployment_sha_source": None,
        "deployment_sha_status": "unknown",
        "deployment_matches_repo": None,
        "note": "環境沒有部署提交變數。公開站與本倉庫是否同一版為未知。",
    }


def format_cash_percent(value: Any) -> str:
    """保留合法的 0。缺值是「未知」，不用 30 或 40%～60% 代替。"""
    if value is None or isinstance(value, bool):
        return "未知"
    if isinstance(value, str):
        text = value.strip().rstrip("%").replace(",", "")
        if text == "":
            return "未知"
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "未知"
    if number == int(number):
        return f"{int(number)}%"
    return f"{number:g}%"


def flatten_effective_regime(config: Optional[Mapping[str, Any]]) -> dict:
    """讀設定檔巢狀位階。這是生效位階，不是報價推斷，也不是策略 best_regime。"""
    if not isinstance(config, Mapping):
        return {
            "status": "unknown",
            "source": "regime_config.json",
            "key": None,
            "label": None,
            "label_display": "生效位階未知",
            "trend": None,
            "cash_target_pct": None,
            "cash_display": "未知",
            "mode": None,
            "updated_at": None,
            "role": "effective_regime",
        }
    market = config.get("market_regime") if isinstance(config.get("market_regime"), Mapping) else {}
    controls = config.get("global_controls") if isinstance(config.get("global_controls"), Mapping) else {}
    key = market.get("key") or None
    label = market.get("label") or None
    if "cash_target_pct" in controls:
        cash = controls.get("cash_target_pct")
    else:
        cash = None
    known = bool(key or label or cash is not None)
    return {
        "status": "ok" if known else "unknown",
        "source": "regime_config.json",
        "key": key,
        "label": label,
        "label_display": label or "生效位階未知",
        "trend": market.get("trend"),
        "cash_target_pct": cash,
        "cash_display": format_cash_percent(cash),
        "mode": controls.get("mode"),
        "updated_at": config.get("updated_at"),
        "role": "effective_regime",
        "note": "設定檔位階與現金欄位。頁面報價推斷與 Studio 策略設計情境是另外兩套。",
    }


def infer_quote_regime(close_raw: float, ma20: float, ma60: float) -> dict:
    """沿用既有首頁門檻，只描述報價推斷，不改門檻、不寫回設定檔。"""
    bias = round((close_raw - ma20) / ma20 * 100, 2) if ma20 else None
    if bias is None:
        label, trend, advice = "報價推斷未知", "未知", "價格或均線不足，不推斷位階。"
    elif bias > 5.0:
        label, trend, advice = (
            "🔴【多頭強攻・短線過熱警戒】",
            "強勢過熱",
            "月線正乖離過大，嚴防短線主力獲利調節，嚴禁盤中追高，聚焦回測守穩標的。",
        )
    elif bias < -5.0 or close_raw < ma60:
        label, trend, advice = (
            "🟣【空頭回檔修正・防禦避險】",
            "空頭防禦",
            "指數摜破均線防守，提高現金水位至 50% 以上，嚴格執行停損，策略 03 避險放空優先。",
        )
    elif bias >= 0.0 and close_raw >= ma20:
        label, trend, advice = (
            "🟢【多頭良性主升波段】",
            "多頭排列",
            "均線呈多頭排列，可積極佈局策略 02 法人起漲股、策略 04 可轉債與策略 05 股期波段。",
        )
    else:
        label, trend, advice = (
            "🟡【高檔震盪整理・箱型洗盤】",
            "箱型震盪",
            "指數跌破月線但守穩季線，類股快速輪動。採高出低進策略，重視布林下軌超跌反彈（策略 01）與低基期標的。",
        )
    return {
        "role": "quote_inferred_regime",
        "label": label,
        "trend": trend,
        "risk_advice": advice,
        "bias_ma20": bias,
    }


def _fmt_price(value: float) -> str:
    return f"{float(value):,.2f}"


def assemble_market_view(
    *,
    twse: Optional[Mapping[str, Any]] = None,
    yfinance_bar: Optional[Mapping[str, Any]] = None,
    config: Optional[Mapping[str, Any]] = None,
    twse_error: Optional[str] = None,
    yfinance_error: Optional[str] = None,
    now: Optional[dt.datetime] = None,
    holidays: Optional[Iterable[dt.date]] = None,
) -> dict:
    """組出報價信封。不讀寫磁碟。價格來源與均線來源不同時標成衝突，不當成生效位階。"""
    observed = now_taipei(now)
    price = None
    prev = None
    change = None
    change_pct = None
    as_of = None
    quote_time = None
    price_source = None
    ma20 = None
    ma60 = None
    ma_source = None
    errors = [err for err in (twse_error, yfinance_error) if err]

    if twse and twse.get("close_raw"):
        price = float(twse["close_raw"])
        prev = float(twse.get("prev_close") or price)
        change = float(twse.get("change") if twse.get("change") is not None else round(price - prev, 2))
        change_pct = float(twse.get("change_pct") if twse.get("change_pct") is not None else (round(change / prev * 100, 2) if prev else 0.0))
        as_of = parse_iso_date(twse.get("date"))
        quote_time = twse.get("time") or None
        price_source = "twse_mis"
    elif yfinance_bar and yfinance_bar.get("close_raw"):
        price = float(yfinance_bar["close_raw"])
        prev = float(yfinance_bar.get("prev_close") or price)
        change = float(yfinance_bar.get("change") if yfinance_bar.get("change") is not None else round(price - prev, 2))
        change_pct = float(yfinance_bar.get("change_pct") if yfinance_bar.get("change_pct") is not None else (round(change / prev * 100, 2) if prev else 0.0))
        as_of = parse_iso_date(yfinance_bar.get("date"))
        quote_time = yfinance_bar.get("time") or None
        price_source = "yfinance"

    if yfinance_bar and yfinance_bar.get("ma20"):
        ma20 = float(yfinance_bar["ma20"])
        ma60 = float(yfinance_bar.get("ma60") or ma20)
        ma_source = "yfinance"

    market = config.get("market_regime") if isinstance(config, Mapping) and isinstance(config.get("market_regime"), Mapping) else {}
    if price is None and market.get("close"):
        price = float(market["close"])
        prev = price
        change = 0.0
        change_pct = 0.0
        as_of = parse_iso_date(config.get("updated_at") if isinstance(config, Mapping) else None)
        price_source = "regime_config_snapshot"
    if ma20 is None and market.get("ma20"):
        ma20 = float(market["ma20"])
        ma60 = float(market.get("ma60") or ma20)
        ma_source = "regime_config_snapshot"

    used_hardcoded = False
    if price is None:
        price = float(HARDCODED_TWII["close_raw"])
        prev = float(HARDCODED_TWII["prev_close"])
        change = float(HARDCODED_TWII["change"])
        change_pct = float(HARDCODED_TWII["change_pct"])
        as_of = None
        price_source = "hardcoded_fallback"
        used_hardcoded = True
    if ma20 is None:
        ma20 = float(HARDCODED_TWII["ma20"])
        ma60 = float(HARDCODED_TWII["ma60"])
        ma_source = "hardcoded_fallback"
        used_hardcoded = True

    mixed = price_source != ma_source
    inferred = infer_quote_regime(price, ma20, ma60)
    if mixed:
        inferred["status"] = "conflict"
        inferred["reason"] = "價格與均線不是同一來源，此推斷不視為生效位階"
    elif price_source == "hardcoded_fallback":
        inferred["status"] = "fallback"
        inferred["reason"] = "寫死占位數字，不是已驗證行情"
    elif price_source == "regime_config_snapshot":
        inferred["status"] = "config_snapshot"
        inferred["reason"] = "沿用設定檔裡的收盤與均線，不是這次交易所回報"
    else:
        inferred["status"] = "ok"
        inferred["reason"] = "僅為報價推斷"
    inferred["price_source"] = price_source
    inferred["ma_source"] = ma_source

    freshness = classify_session_freshness(as_of.isoformat() if as_of else None, now=observed, holidays=holidays)
    if price_source == "hardcoded_fallback":
        status = "fallback"
        verified = False
    elif mixed:
        status = "conflict"
        verified = False
    elif price_source == "regime_config_snapshot":
        status = "config_snapshot"
        verified = False
    elif freshness["stale"]:
        status = "stale"
        verified = True
    else:
        status = "ok" if as_of else "unknown"
        verified = bool(as_of)

    effective = flatten_effective_regime(config if isinstance(config, Mapping) else None)
    return {
        "close_raw": price,
        "close": _fmt_price(price),
        "prev_close": prev,
        "change": change,
        "change_pct": change_pct,
        "date": as_of.isoformat() if as_of else "",
        "time": quote_time or "",
        "ma20": _fmt_price(ma20),
        "ma60": _fmt_price(ma60),
        "ma20_raw": ma20,
        "ma60_raw": ma60,
        "bias_ma20": inferred.get("bias_ma20"),
        "regime": inferred["label"],
        "trend": inferred["trend"],
        "risk_advice": inferred["risk_advice"],
        "source": price_source,
        "price_source": price_source,
        "ma_source": ma_source,
        "as_of": as_of.isoformat() if as_of else None,
        "fetched_at": isoformat_taipei(observed),
        "timezone": TIMEZONE_NAME,
        "status": status,
        "stale": bool(freshness["stale"]) and price_source not in {"hardcoded_fallback"},
        "verified": verified,
        "error": "；".join(errors) if errors and price_source in {"hardcoded_fallback", "regime_config_snapshot"} else None,
        "hardcoded_fallback": used_hardcoded,
        "quote_inferred_regime": inferred,
        "effective_regime": effective,
        "freshness": freshness,
        "deployment_note": "此回應來自目前程序讀到的檔案與行情函式，不代表公開站已是同一提交。",
    }


def _cell(row: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        if key in row and str(row.get(key)).strip():
            return str(row.get(key)).strip()
    return ""


def row_code(row: Mapping[str, Any]) -> str:
    return _cell(row, ("證券代號", "代號", "股票代碼", "code", "symbol"))


def build_count_contract(rows: Sequence[Mapping[str, Any]]) -> dict:
    """三種口徑同時標出，不把其中一個寫成另一個。"""
    material = list(rows or [])
    dates = []
    for row in material:
        parsed = parse_iso_date(_cell(row, ("選股日期", "日期")))
        if parsed:
            dates.append(parsed)
    latest = max(dates) if dates else None
    latest_count = sum(1 for item in dates if item == latest) if latest else 0
    available = sum(1 for row in material if row_code(row))
    studio_displayed = 0
    for row in material[:STUDIO_PREVIEW_LIMIT]:
        code = row_code(row)
        if code.isdigit():
            studio_displayed += 1
    advisor_count = min(ADVISOR_PER_STRATEGY_CAP, available)
    if not material:
        pool_status = "empty"
    elif latest is None:
        pool_status = "unknown"
    else:
        pool_status = "ok"
    return {
        "rolling_total": len(material),
        "latest_trading_date": latest.isoformat() if latest else None,
        "latest_day_count": latest_count if latest else None,
        "studio_limit": STUDIO_PREVIEW_LIMIT,
        "studio_displayed": studio_displayed,
        "advisor_cap": ADVISOR_PER_STRATEGY_CAP,
        "advisor_count": advisor_count,
        "available_codes": available,
        "pool_status": pool_status,
        "labels": {
            "rolling_total": f"五日滾動 {len(material)} 檔",
            "latest_day": (
                f"最新交易日 {latest.isoformat()} {latest_count} 檔" if latest else "最新交易日未知"
            ),
            "studio": f"Studio 顯示 {studio_displayed}／上限 {STUDIO_PREVIEW_LIMIT}",
            "advisor": f"T+1 每策略 {advisor_count}／上限 {ADVISOR_PER_STRATEGY_CAP}",
        },
    }


def legacy_provenance(fieldnames: Optional[Iterable[str]], rows: Optional[Sequence[Mapping[str, Any]]] = None) -> dict:
    names = set(fieldnames or ())
    present = [name for name in PROVENANCE_FIELDS if name in names]
    if not present:
        return {
            "status": "unknown",
            "strategy_version": None,
            "config_hash": None,
            "batch_id": None,
            "generated_at": None,
            "rule_snapshot": None,
            "note": "既有結果列沒有策略版本、設定雜湊、批次號或規則快照。不回填。",
        }
    first = next(iter(rows or ()), {})
    def pick(name: str):
        if name not in names:
            return None
        value = first.get(name) if isinstance(first, Mapping) else None
        text = str(value).strip() if value is not None else ""
        return text or None
    return {
        "status": "partial" if len(present) < len(PROVENANCE_FIELDS) else "present",
        "strategy_version": pick("strategy_version"),
        "config_hash": pick("config_hash"),
        "batch_id": pick("batch_id"),
        "generated_at": pick("generated_at"),
        "rule_snapshot": pick("rule_snapshot"),
        "note": "只轉述結果檔已有欄位。",
    }


def build_batch_status(per_strategy: Sequence[Mapping[str, Any]]) -> dict:
    """各策略日期不同就標衝突，不捏造單一交易日。"""
    listed = []
    all_dates = set()
    for item in per_strategy or ():
        dates = []
        for raw in item.get("dates") or ():
            parsed = parse_iso_date(raw)
            if parsed:
                iso = parsed.isoformat()
                dates.append(iso)
                all_dates.add(iso)
        listed.append({
            "strategy": item.get("strategy"),
            "dates": sorted(set(dates), reverse=True),
            "rolling_total": item.get("rolling_total"),
        })
    ordered = sorted(all_dates, reverse=True)
    if not ordered:
        unity = "unknown"
        status = "unknown"
        note = "結果檔沒有可解析的選股日期"
    elif len(ordered) == 1:
        unity = "single_date"
        status = "ok"
        note = "目前讀到的策略批次是同一日"
    else:
        unity = "conflict"
        status = "conflict"
        note = "各策略批次日不同，不合併成單一盤勢日期"
    return {
        "status": status,
        "unity": unity,
        "dates": ordered,
        "by_strategy": listed,
        "note": note,
    }


def illustrative_performance() -> dict:
    return {
        "metrics_status": "illustrative_unverified",
        "verified_performance": False,
        "disclaimer": "目錄常數與合成曲線只是示範，不是回測結果，也不是實盤績效。",
    }
