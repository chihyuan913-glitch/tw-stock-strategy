# -*- coding: utf-8 -*-
"""
盤中下單參數與報價有效性檢核模組 (Order Hint & Quote Validation)
全數使用 decimal.Decimal 進行價位正規化與張數試算，杜絕浮點殘值。
僅產出訊息文字參考，絕不執行下單或連線券商。
"""

from __future__ import annotations

import datetime
import math
import re
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Dict, Optional, Tuple

import intraday_speed

_LONG_EVENTS: Dict[str, Tuple[str, str, Optional[str]]] = {
    "ENTRY": ("買進", "buy", None),
    "ADDON": ("加碼買進", "buy", "原部位 1/3"),
    "TP1": ("賣出", "sell", "持股 1/2"),
    "TP2": ("賣出", "sell", "持股全部"),
    "STOP_LOSS": ("停損賣出", "sell", "持股全部"),
}

_SHORT_EVENTS: Dict[str, Tuple[str, str, Optional[str]]] = {
    "ENTRY": ("放空（融券賣出）", "sell", None),
    "ADDON": ("加空", "sell", "原空單 1/3"),
    "TP1": ("回補（買進）", "buy", "空單 1/2"),
    "TP2": ("回補（買進）", "buy", "空單全部"),
    "STOP_LOSS": ("停損回補（買進）", "buy", "空單全部"),
}


def to_decimal(v: Any) -> Optional[Decimal]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        if not v.is_finite() or v <= 0:
            return None
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s or s == "-":
            return None
        try:
            d = Decimal(s)
        except InvalidOperation:
            return None
        if not d.is_finite() or d <= 0:
            return None
        return d
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(fv) or fv <= 0:
        return None
    try:
        d = Decimal(str(round(fv, 4)))
    except InvalidOperation:
        return None
    if not d.is_finite() or d <= 0:
        return None
    return d


def tick_size(price: Decimal, code: str = "") -> Decimal:
    c = str(code or "").strip()
    if c.startswith("00"):
        return Decimal("0.01") if price < Decimal("50") else Decimal("0.05")
    if price < Decimal("10"):
        return Decimal("0.01")
    if price < Decimal("50"):
        return Decimal("0.05")
    if price < Decimal("100"):
        return Decimal("0.1")
    if price < Decimal("500"):
        return Decimal("0.5")
    if price < Decimal("1000"):
        return Decimal("1")
    return Decimal("5")


def _round_to_tick(d: Decimal, tick: Decimal, side: str) -> Decimal:
    rounding = ROUND_CEILING if side == "buy" else ROUND_FLOOR
    units = (d / tick).to_integral_value(rounding=rounding)
    return units * tick


def normalize_price(v: Any, side: str, code: str = "") -> Optional[Decimal]:
    d = to_decimal(v)
    if d is None:
        return None
    t1 = tick_size(d, code)
    rounded = _round_to_tick(d, t1, side)
    if rounded <= 0:
        return None
    t2 = tick_size(rounded, code)
    if t2 != t1:
        rounded = _round_to_tick(rounded, t2, side)
    if rounded <= 0:
        return None
    return rounded


def limit_prices(prev_close: Any, code: str = "") -> Tuple[Optional[Decimal], Optional[Decimal]]:
    prev = to_decimal(prev_close)
    if prev is None:
        return None, None
    limit_up = normalize_price(prev * Decimal("1.10"), "sell", code)
    limit_down = normalize_price(prev * Decimal("0.90"), "buy", code)
    return limit_down, limit_up


def format_price(d: Decimal) -> str:
    q = d.quantize(Decimal("0.01"))
    s = f"{q:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def quote_is_valid(
    q: Optional[Dict[str, Any]],
    now_tw: datetime.datetime,
    mono_now: float,
) -> Tuple[bool, str]:
    if not isinstance(q, dict):
        return False, "報價為空"
    if to_decimal(q.get("price")) is None:
        return False, "現價無效"
    if q.get("price_source") not in ("z", "trade_z"):
        return False, "非即時成交價 (price_source 無效)"
    now_taipei = intraday_speed.to_taipei(now_tw)
    feed_date = re.sub(r"[-/]", "", str(q.get("feed_date") or "").strip())
    if feed_date != now_taipei.strftime("%Y%m%d"):
        return False, "報價日期非今日"
    fetched_at = q.get("_fetched_at")
    if fetched_at is None:
        return False, "缺少 _fetched_at"
    try:
        age = float(mono_now) - float(fetched_at)
    except (TypeError, ValueError):
        return False, "_fetched_at 無效"
    max_age = intraday_speed.get_int("TW_RADAR_QUOTE_MAX_AGE_SEC")
    if age > max_age:
        return False, f"報價已過期 ({age:.1f}s > {max_age}s)"
    return True, "ok"


def _calc_entry_lots_text(
    code: str,
    order_price_dec: Decimal,
    sl_dec: Optional[Decimal],
    is_short: bool,
    advisor_map: Dict[str, Dict[str, Any]],
) -> str:
    adv_row = advisor_map.get(code)
    if isinstance(adv_row, dict):
        raw_shares = adv_row.get("recommended_shares")
        try:
            shares = int(str(raw_shares or "").strip())
        except (TypeError, ValueError):
            shares = 0
        if shares > 0:
            lots = shares // 1000
            odd = shares % 1000
            if odd > 0:
                return f"{lots} 張＋零股 {odd} 股（明日建議表）"
            return f"{lots} 張（明日建議表）"

    if sl_dec is None or (not is_short and sl_dec >= order_price_dec) or (is_short and sl_dec <= order_price_dec):
        return "不計算（停損價無效）"

    risk_per_lot = abs(order_price_dec - sl_dec) * Decimal("1000")
    if risk_per_lot <= 0:
        return "不計算（停損價無效）"

    budget = Decimal(intraday_speed.get_int("TW_ORDER_RISK_BUDGET_TWD"))
    max_lots = intraday_speed.get_int("TW_ORDER_MAX_LOTS")
    max_notional = Decimal(intraday_speed.get_int("TW_ORDER_MAX_NOTIONAL_TWD"))

    lots_by_risk = int(budget // risk_per_lot)
    notional_per_lot = order_price_dec * Decimal("1000")
    lots_by_notional = int(max_notional // notional_per_lot) if notional_per_lot > 0 else 0
    lots = min(lots_by_risk, max_lots, lots_by_notional)

    if lots <= 0:
        return f"0 張（單張風險 {int(risk_per_lot)} 元超過預算 {int(budget)} 元）"
    return f"{lots} 張"


def build_order_hint(
    item: Dict[str, Any],
    quote: Optional[Dict[str, Any]],
    event_type: str,
    is_short: bool,
    *,
    now_tw: datetime.datetime,
    mono_now: float,
    advisor: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, str]]:
    valid, _ = quote_is_valid(quote, now_tw, mono_now)
    if not valid or quote is None:
        return None

    code = str(item.get("code") or item.get("證券代號") or item.get("代號") or quote.get("code") or "").strip()
    events_map = _SHORT_EVENTS if is_short else _LONG_EVENTS
    ev_key = str(event_type or "").strip().upper()
    if ev_key not in events_map:
        return None

    action, side, fixed_lots = events_map[ev_key]
    order_dec = normalize_price(quote.get("price"), side, code)
    if order_dec is None:
        return None

    limit_down, limit_up = limit_prices(quote.get("prev_close"), code)
    if side == "buy" and limit_up is not None and order_dec > limit_up:
        order_dec = limit_up
    if side == "sell" and limit_down is not None and order_dec < limit_down:
        order_dec = limit_down

    sl_raw = item.get("sl_price") or item.get("停損價位") or item.get("停損回補")
    sl_side = "buy" if is_short else "sell"
    sl_dec = normalize_price(sl_raw, sl_side, code)
    stop_text = format_price(sl_dec) if sl_dec is not None else "未設定"

    if fixed_lots is not None:
        lots_text = fixed_lots
    else:
        adv_map = intraday_speed.load_advisor() if advisor is None else advisor
        lots_text = _calc_entry_lots_text(code, order_dec, sl_dec, is_short, adv_map)

    return {
        "code": code,
        "direction": "空" if is_short else "多",
        "action": action,
        "side": side,
        "order_price": format_price(order_dec),
        "lots_text": lots_text,
        "stop_text": stop_text,
    }
