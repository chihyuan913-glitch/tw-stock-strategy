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


def market_session_date(value: Any) -> Optional[dt.date]:
    """有時區的時間先換到台北再取日期。不能把 UTC 字串前 10 碼當成交易日。"""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(TAIPEI)
        return value.date()
    if isinstance(value, dt.date):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            return market_session_date(value.to_pydatetime())
        except Exception:
            return None
    return parse_iso_date(value)


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


OPEN_MINUTES = 9 * 60
CLOSE_MINUTES = 13 * 60 + 30


def unreliable_calendar(reason: str = "沒有可信的臺灣休市日曆，不判定過期") -> dict:
    return {"reliable": False, "closed": set(), "years": set(), "reason": reason}


def reliable_calendar(closed: Iterable[dt.date], years: Iterable[int]) -> dict:
    year_set = {int(year) for year in years}
    closed_set = set(closed)
    if not year_set:
        return unreliable_calendar("休市日曆沒有覆蓋年份")
    return {"reliable": True, "closed": closed_set, "years": year_set, "reason": None}


def _in_coverage(day: dt.date, calendar: Mapping[str, Any]) -> bool:
    if not calendar.get("reliable"):
        return False
    start = parse_iso_date(calendar.get("coverage_start"))
    end = parse_iso_date(calendar.get("coverage_end"))
    if start and end:
        return start <= day <= end
    return day.year in set(calendar.get("years") or ())


def is_scheduled_session(day: dt.date, calendar: Mapping[str, Any]) -> Optional[bool]:
    """True 交易、False 休市、None 超出已證明涵蓋。週末未列在開市標記中就不是交易日。"""
    if not _in_coverage(day, calendar):
        return None
    if day in set(calendar.get("explicit_open") or ()):
        return True
    if day in set(calendar.get("closed") or ()):
        return False
    if day.weekday() >= 5:
        return False
    return True


def previous_trading_day(day: dt.date, calendar: Mapping[str, Any]) -> Optional[dt.date]:
    """往前找交易日。走出已證明涵蓋就停，不把未公布年度硬當成開市。"""
    if not calendar.get("reliable"):
        return None
    cursor = day - dt.timedelta(days=1)
    for _ in range(366):
        flag = is_scheduled_session(cursor, calendar)
        if flag is None:
            return None
        if flag:
            return cursor
        cursor -= dt.timedelta(days=1)
    return None


def expected_session(
    now: Optional[dt.datetime],
    calendar: Optional[Mapping[str, Any]],
    rhythm: str = "daily_close",
) -> dict:
    """依交易時段決定『這類資料現在應該對到哪一個交易日』。日曆不可信時不給日期。"""
    observed = now_taipei(now)
    base = {
        "timezone": TIMEZONE_NAME,
        "observed_at": isoformat_taipei(observed),
        "rhythm": rhythm,
        "phase": None,
        "expected_session": None,
        "calendar_reliable": bool(calendar and calendar.get("reliable")),
    }
    if not calendar or not calendar.get("reliable"):
        reason = (calendar or {}).get("reason") or "沒有可信的臺灣休市日曆，不判定過期"
        base.update({"status": "unknown", "reason": reason})
        return base
    today = observed.date()
    today_flag = is_scheduled_session(today, calendar)
    if today_flag is None:
        base.update({"status": "unknown", "reason": "觀察日不在已證明的開休市涵蓋範圍，不判定過期"})
        return base
    closed_today = not today_flag
    minutes = observed.hour * 60 + observed.minute
    if closed_today:
        phase = "weekend" if today.weekday() >= 5 else "holiday"
        expected = previous_trading_day(today, calendar)
    elif minutes < OPEN_MINUTES:
        phase = "before_open"
        expected = previous_trading_day(today, calendar)
    elif minutes < CLOSE_MINUTES:
        phase = "session"
        expected = today if rhythm == "intraday" else previous_trading_day(today, calendar)
    else:
        phase = "after_close"
        expected = today
    if expected is None:
        base.update({"status": "unknown", "phase": phase, "reason": "最近交易日超出日曆覆蓋，不判定過期"})
        return base
    base.update({
        "status": "ok",
        "phase": phase,
        "expected_session": expected.isoformat(),
        "reason": "依交易時段與休市日曆",
    })
    return base


def classify_session_freshness(
    as_of: Any,
    now: Optional[dt.datetime] = None,
    calendar: Optional[Mapping[str, Any]] = None,
    rhythm: str = "daily_close",
) -> dict:
    """沒有資料日、沒有可信日曆，或日期對不齊預期節奏時，不標成已驗證，也不硬判過期。"""
    expectation = expected_session(now, calendar, rhythm=rhythm)
    parsed = parse_iso_date(as_of)
    result = {
        **expectation,
        "as_of": parsed.isoformat() if parsed else None,
        "stale": False,
        "freshness": "unknown",
    }
    if not expectation.get("calendar_reliable") or expectation.get("status") != "ok":
        result["reason"] = expectation.get("reason") or "沒有可信的臺灣休市日曆，不判定過期"
        return result
    if parsed is None:
        result["reason"] = "沒有可驗證的資料日，不以日曆日差標成過期"
        return result
    expected = parse_iso_date(expectation.get("expected_session"))
    if expected is None:
        result["reason"] = "沒有可比較的預期交易日"
        return result
    if parsed == expected:
        result.update({"freshness": "ok", "stale": False, "reason": "對齊這個時段預期的交易日"})
        return result
    if parsed < expected:
        result.update({"freshness": "stale", "stale": True, "reason": "早於這個時段預期的交易日"})
        return result
    result.update({"freshness": "unknown", "stale": False, "reason": "資料日晚於預期交易日，不改寫成已驗證"})
    return result


def module_status(
    *,
    error: Optional[str] = None,
    count: Optional[int] = None,
    as_of: Any = None,
    now: Optional[dt.datetime] = None,
    calendar: Optional[Mapping[str, Any]] = None,
    rhythm: str = "daily_close",
) -> dict:
    """錯誤、零筆、過期分開。沒有日曆時過期旗標維持未知，不硬判。"""
    freshness = classify_session_freshness(as_of, now=now, calendar=calendar, rhythm=rhythm)
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
    if freshness.get("freshness") == "unknown" and not empty:
        status = "unknown"
    elif empty:
        status = "empty"
    else:
        status = freshness["freshness"]
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


def _component(source: Optional[str], as_of: Optional[dt.date], verified: bool) -> dict:
    return {
        "source": source,
        "as_of": as_of.isoformat() if as_of else None,
        "verified": verified,
    }


def _sync_quote_components(price_part: Mapping[str, Any], ma_part: Mapping[str, Any]) -> dict:
    """來源名稱不同不是衝突。兩邊都有交易日且不一致才是衝突；對不齊就未知。"""
    if price_part.get("verified") and ma_part.get("verified"):
        if price_part.get("as_of") and price_part.get("as_of") == ma_part.get("as_of"):
            return {
                "status": "ok",
                "verified": True,
                "reason": "價格與均線同一交易日。這是報價推斷，不是設定檔生效位階。",
            }
        return {
            "status": "conflict",
            "verified": False,
            "reason": "價格與均線的交易日不同，不把推斷當成已驗證。",
        }
    return {
        "status": "unknown",
        "verified": False,
        "reason": "價格或均線缺少可驗證的交易日，不把推斷當成已驗證。",
    }


def assemble_market_view(
    *,
    twse: Optional[Mapping[str, Any]] = None,
    yfinance_bar: Optional[Mapping[str, Any]] = None,
    config: Optional[Mapping[str, Any]] = None,
    twse_error: Optional[str] = None,
    yfinance_error: Optional[str] = None,
    now: Optional[dt.datetime] = None,
    calendar: Optional[Mapping[str, Any]] = None,
) -> dict:
    """組出報價信封。不讀寫磁碟。設定檔更新時間不是行情日，缺漲跌保持未知。"""
    observed = now_taipei(now)
    price = None
    prev = None
    change = None
    change_pct = None
    as_of = None
    quote_time = None
    price_source = None
    price_verified = False
    ma20 = None
    ma60 = None
    ma_source = None
    ma_as_of = None
    ma_verified = False
    price_rhythm = "intraday"
    errors = [err for err in (twse_error, yfinance_error) if err]

    if twse and twse.get("close_raw"):
        price = float(twse["close_raw"])
        prev = float(twse["prev_close"]) if twse.get("prev_close") not in (None, "") else None
        change = twse.get("change")
        change_pct = twse.get("change_pct")
        change = float(change) if change is not None else None
        change_pct = float(change_pct) if change_pct is not None else None
        as_of = parse_iso_date(twse.get("date"))
        quote_time = twse.get("time") or None
        price_source = "twse_mis"
        price_verified = as_of is not None
        price_rhythm = "intraday"
    elif yfinance_bar and yfinance_bar.get("close_raw"):
        price = float(yfinance_bar["close_raw"])
        prev = float(yfinance_bar["prev_close"]) if yfinance_bar.get("prev_close") not in (None, "") else None
        change = yfinance_bar.get("change")
        change_pct = yfinance_bar.get("change_pct")
        change = float(change) if change is not None else None
        change_pct = float(change_pct) if change_pct is not None else None
        as_of = parse_iso_date(yfinance_bar.get("date"))
        quote_time = yfinance_bar.get("time") or None
        price_source = "yfinance"
        price_verified = as_of is not None
        price_rhythm = "daily_close"

    if yfinance_bar and yfinance_bar.get("ma20"):
        ma20 = float(yfinance_bar["ma20"])
        ma60 = float(yfinance_bar.get("ma60") or ma20)
        ma_source = "yfinance"
        ma_as_of = parse_iso_date(yfinance_bar.get("date"))
        ma_verified = ma_as_of is not None

    market = config.get("market_regime") if isinstance(config, Mapping) and isinstance(config.get("market_regime"), Mapping) else {}
    if price is None and market.get("close") not in (None, ""):
        price = float(market["close"])
        prev = None
        change = None
        change_pct = None
        as_of = None
        price_source = "regime_config_snapshot"
        price_verified = False
    if ma20 is None and market.get("ma20") not in (None, ""):
        ma20 = float(market["ma20"])
        ma60 = float(market.get("ma60") or ma20)
        ma_source = "regime_config_snapshot"
        ma_as_of = None
        ma_verified = False

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

    if price_source == "hardcoded_fallback":
        price_verified = False
        as_of = None
    if ma_source == "hardcoded_fallback":
        ma_verified = False
        ma_as_of = None
    price_part = _component(price_source, as_of, price_verified and price_source not in {"hardcoded_fallback", "regime_config_snapshot"})
    ma_part = _component(ma_source, ma_as_of, ma_verified and ma_source not in {"hardcoded_fallback", "regime_config_snapshot"})
    sync = _sync_quote_components(price_part, ma_part)
    inferred = infer_quote_regime(price, ma20, ma60)
    inferred["status"] = sync["status"]
    inferred["verified"] = sync["verified"]
    inferred["reason"] = sync["reason"]
    inferred["label_display"] = inferred["label"] if sync["verified"] else "未知"
    inferred["price_source"] = price_source
    inferred["ma_source"] = ma_source
    inferred["price_as_of"] = price_part["as_of"]
    inferred["ma_as_of"] = ma_part["as_of"]
    if price_source == "hardcoded_fallback":
        inferred["status"] = "fallback"
        inferred["verified"] = False
        inferred["label_display"] = "未知"
        inferred["reason"] = "寫死占位數字，不是已驗證行情"
    elif price_source == "regime_config_snapshot" and not sync["verified"]:
        inferred["status"] = "config_snapshot" if sync["status"] == "unknown" else sync["status"]
        inferred["verified"] = False
        inferred["label_display"] = "未知"
        inferred["reason"] = "沿用設定檔收盤，沒有行情交易日，漲跌未知。這不是設定檔生效位階。"

    freshness = classify_session_freshness(
        as_of.isoformat() if as_of else None,
        now=observed,
        calendar=calendar,
        rhythm=price_rhythm,
    )
    source_aligned = bool(sync.get("verified"))
    current = freshness.get("freshness") == "ok"
    if source_aligned and freshness.get("freshness") == "stale":
        inferred["status"] = "stale"
        inferred["verified"] = False
        inferred["current"] = False
        inferred["source_aligned"] = True
        inferred["label_display"] = "未知"
        inferred["reason"] = "價格與均線日期已對齊，但不是這個時段的當期交易日，不能當成現在可採用的位階。"
    elif inferred.get("status") == "ok" and not current:
        inferred["status"] = "unknown"
        inferred["verified"] = False
        inferred["current"] = False
        inferred["source_aligned"] = source_aligned
        inferred["label_display"] = "未知"
        inferred["reason"] = freshness.get("reason") or "無法確認這筆行情是否為當期"
    else:
        inferred["current"] = current and inferred.get("status") == "ok"
        inferred["source_aligned"] = source_aligned
        if not inferred["current"]:
            inferred["verified"] = False
            if inferred.get("status") != "ok":
                inferred["label_display"] = "未知"
    if price_source == "hardcoded_fallback":
        status = "fallback"
        verified = False
        current = False
    elif price_source == "regime_config_snapshot":
        status = "config_snapshot"
        verified = False
        current = False
    elif not source_aligned:
        status = sync["status"]
        verified = False
        current = False
    elif freshness["stale"]:
        status = "stale"
        verified = False
        current = False
    elif freshness["freshness"] == "unknown":
        status = "unknown"
        verified = False
        current = False
    else:
        status = "ok"
        verified = True
        current = True

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
        "current": current,
        "source_aligned": source_aligned,
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
        "advisor_count": None,
        "available_codes": available,
        "pool_status": pool_status,
        "labels": {
            "rolling_total": f"五日滾動 {len(material)} 檔",
            "latest_day": (
                f"最新交易日 {latest.isoformat()} {latest_count} 檔" if latest else "最新交易日未知"
            ),
            "studio": f"Studio 顯示 {studio_displayed}／上限 {STUDIO_PREVIEW_LIMIT}",
            "advisor": "T+1 實際筆數不由選股池推算",
        },
    }


def _strategy_id_from_source(text: Any) -> Optional[str]:
    import re
    matched = re.search(r"策略\s*(\d{1,2})", str(text or ""))
    if not matched:
        return None
    return matched.group(1).zfill(2)


def summarize_advisor_table(rows: Optional[Sequence[Mapping[str, Any]]]) -> dict:
    """實際筆數只來自決策表。缺檔、空表、重複列分開，上限不拿來填數。"""
    cap_note = f"上限 {ADVISOR_PER_STRATEGY_CAP} 不是實際列數"
    if rows is None:
        return {
            "status": "missing",
            "row_count": 0,
            "unique_symbols": 0,
            "duplicate_rows": 0,
            "by_strategy": [],
            "advisor_cap": ADVISOR_PER_STRATEGY_CAP,
            "note": f"決策表缺檔。{cap_note}",
        }
    material = list(rows)
    if not material:
        return {
            "status": "empty",
            "row_count": 0,
            "unique_symbols": 0,
            "duplicate_rows": 0,
            "by_strategy": [],
            "advisor_cap": ADVISOR_PER_STRATEGY_CAP,
            "note": f"決策表是空的。{cap_note}",
        }
    grouped: dict = {}
    order = []
    for row in material:
        sid = _strategy_id_from_source(row.get("source_strategy")) or "unknown"
        symbol = str(row.get("symbol") or row.get("證券代號") or "").strip() or "unknown"
        if sid not in grouped:
            grouped[sid] = {"strategy_id": sid, "source_label": str(row.get("source_strategy") or ""), "rows": []}
            order.append(sid)
        grouped[sid]["rows"].append(symbol)
    by_strategy = []
    duplicate_rows = 0
    unique_all = set()
    for sid in order:
        symbols = grouped[sid]["rows"]
        counts: dict = {}
        for symbol in symbols:
            counts[symbol] = counts.get(symbol, 0) + 1
            unique_all.add((sid, symbol))
        dup = sum(count - 1 for count in counts.values() if count > 1)
        duplicate_rows += dup
        by_strategy.append({
            "strategy_id": sid,
            "source_label": grouped[sid]["source_label"],
            "row_count": len(symbols),
            "unique_symbols": len(counts),
            "duplicate_rows": dup,
        })
    return {
        "status": "ok",
        "row_count": len(material),
        "unique_symbols": len(unique_all),
        "duplicate_rows": duplicate_rows,
        "by_strategy": by_strategy,
        "advisor_cap": ADVISOR_PER_STRATEGY_CAP,
        "note": cap_note,
    }


def advisor_label_for_strategy(summary: Mapping[str, Any], strategy_id: Optional[str]) -> str:
    cap = summary.get("advisor_cap", ADVISOR_PER_STRATEGY_CAP)
    if summary.get("status") == "missing":
        return f"T+1 決策表缺檔（上限 {cap}，不是實際列數）"
    if summary.get("status") == "empty":
        return f"T+1 決策表空表，實際 0 列（上限 {cap}）"
    sid = strategy_id.zfill(2) if strategy_id and str(strategy_id).isdigit() else strategy_id
    found = None
    for item in summary.get("by_strategy") or []:
        if sid is None or item.get("strategy_id") == sid:
            found = item
            if sid is not None:
                break
    if sid is not None and found is None:
        return f"T+1 策略{sid} 實際 0 列（上限 {cap}）"
    if found is None:
        return f"T+1 實際 {summary.get('row_count', 0)} 列（上限 {cap}，上限不是實際數）"
    dup = f"，其中重複 {found['duplicate_rows']} 列" if found.get("duplicate_rows") else ""
    return f"T+1 策略{found['strategy_id']} 實際 {found['row_count']} 列{dup}（上限 {cap}，上限不是實際數）"


def apply_advisor_actual(contract: Mapping[str, Any], summary: Mapping[str, Any], strategy_id: Optional[str]) -> dict:
    merged = dict(contract)
    labels = dict(merged.get("labels") or {})
    labels["advisor"] = advisor_label_for_strategy(summary, strategy_id)
    merged["labels"] = labels
    merged["advisor_actual"] = summary
    merged["advisor_count"] = None
    return merged


def interpret_market_index_cache(cache: Optional[Mapping[str, Any]]) -> dict:
    """看盤指數快取。沒有 source/as_of 標未知，不改叫寫死後備。占位與可驗證報價分開。"""
    if not isinstance(cache, Mapping):
        return {
            "status": "unknown",
            "source": "unknown",
            "verified": False,
            "index_close": None,
            "index_change_pct": None,
            "as_of_date": None,
            "as_of_display": "資料日未知",
            "quote_time": None,
            "is_realtime": False,
            "note": "沒有指數快取",
        }
    observation = cache.get("observation")
    as_of = parse_iso_date(cache.get("as_of_date") or cache.get("date") or cache.get("feed_date"))
    source = cache.get("source") or None
    if observation == "builtin_placeholder":
        return {
            "status": "placeholder",
            "source": "builtin_placeholder",
            "verified": False,
            "index_close": None,
            "index_change_pct": None,
            "as_of_date": None,
            "as_of_display": "內建占位，不是行情",
            "quote_time": None,
            "is_realtime": False,
            "note": "這組數字是程式內建占位，不拿來顯示成收盤",
        }
    if source in (None, "") and as_of is None:
        return {
            "status": "unknown",
            "source": "unknown",
            "verified": False,
            "index_close": cache.get("index_close"),
            "index_change_pct": cache.get("change_pct"),
            "as_of_date": None,
            "as_of_display": "資料日未知",
            "quote_time": cache.get("time"),
            "is_realtime": False,
            "note": "快取沒有 source 與交易日，不視為寫死後備，也不視為已驗證",
        }
    verified = source == "twse_mis" and as_of is not None
    return {
        "status": "ok" if verified else "unverified_quote",
        "source": source or "unknown",
        "verified": verified,
        "index_close": cache.get("index_close"),
        "index_change_pct": cache.get("change_pct"),
        "as_of_date": as_of.isoformat() if as_of else None,
        "as_of_display": as_of.isoformat() if verified else "資料日未知",
        "quote_time": cache.get("feed_time") or cache.get("time"),
        "is_realtime": verified,
        "note": "可驗證行情" if verified else "有報價但交易日不足，不標成已驗證",
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


def build_batch_status(
    per_strategy: Sequence[Mapping[str, Any]],
    now: Optional[dt.datetime] = None,
    calendar: Optional[Mapping[str, Any]] = None,
    rhythm: str = "daily_close",
) -> dict:
    """一致性與新鮮度分開。同一天不代表當期；沒有可信日曆就不判過期。"""
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
    date_freshness = []
    for iso in ordered:
        judged = classify_session_freshness(iso, now=now, calendar=calendar, rhythm=rhythm)
        date_freshness.append({
            "date": iso,
            "freshness": judged.get("freshness"),
            "stale": bool(judged.get("stale")),
            "reason": judged.get("reason"),
        })
    if not ordered:
        unity = "unknown"
        status = "unknown"
        freshness = "unknown"
        note = "結果檔沒有可解析的選股日期。不用檔案修改時間補交易日。"
    elif len(ordered) == 1:
        unity = "single_date"
        freshness = date_freshness[0]["freshness"]
        if freshness == "ok":
            status = "ok"
            note = f"批次一致於 {ordered[0]}，且對到這個時段的預期交易日。"
        elif freshness == "stale":
            status = "stale"
            note = f"批次一致於 {ordered[0]}，但該日已過期，不能當成當前批次。"
        else:
            status = "unknown"
            note = f"批次一致於 {ordered[0]}，但日曆不足以判斷是否當期。"
    else:
        unity = "conflict"
        status = "conflict"
        if any(item["freshness"] == "unknown" for item in date_freshness):
            freshness = "unknown"
        elif any(item["stale"] for item in date_freshness):
            freshness = "stale"
        else:
            freshness = "ok"
        note = "各策略批次日不同，不合併成單一盤勢日期。"
    return {
        "status": status,
        "unity": unity,
        "freshness": freshness,
        "dates": ordered,
        "date_freshness": date_freshness,
        "by_strategy": listed,
        "note": note,
    }


def build_treemap_index_summary(
    market_idx: Optional[Mapping[str, Any]],
    group_nodes: Optional[Sequence[Mapping[str, Any]]] = None,
    up_count: int = 0,
    down_count: int = 0,
    flat_count: int = 0,
    total_stocks: int = 0,
) -> dict:
    """熱力圖指數摘要。與 interpret_market_index_cache 同一套欄位，不另判來源。"""
    index_view = interpret_market_index_cache(market_idx)
    top_sector = None
    bottom_sector = None
    nodes = list(group_nodes or [])
    if nodes:
        ranked = sorted(nodes, key=lambda group: group.get("median_change") or 0)
        bottom = ranked[0]
        top = ranked[-1]
        top_sector = f"{top['name']} ({top['median_change']:+.2f}%)"
        bottom_sector = f"{bottom['name']} ({bottom['median_change']:+.2f}%)"
    return {
        "index_change_pct": index_view.get("index_change_pct"),
        "index_close": index_view.get("index_close"),
        "as_of_date": index_view.get("as_of_date"),
        "as_of_display": index_view.get("as_of_display"),
        "quote_time": index_view.get("quote_time"),
        "source": index_view.get("source"),
        "status": index_view.get("status"),
        "verified": index_view.get("verified"),
        "is_realtime": index_view.get("is_realtime"),
        "note": index_view.get("note"),
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "top_sector": top_sector,
        "bottom_sector": bottom_sector,
        "sector_rank_source": index_view.get("source"),
        "limit_up_count": None,
        "limit_down_count": None,
        "total_stocks_analyzed": total_stocks,
    }


def illustrative_performance() -> dict:
    return {
        "metrics_status": "illustrative_unverified",
        "verified_performance": False,
        "disclaimer": "目錄常數與合成曲線只是示範，不是回測結果，也不是實盤績效。",
    }
