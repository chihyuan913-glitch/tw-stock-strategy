# -*- coding: utf-8 -*-
"""證交所 MIS 單筆報價解析。沒有網路、不寫檔、不推播。"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Set


def parse_mis_quote_item(
    item: Mapping[str, Any],
    allowed_codes: Optional[Set[str]] = None,
    fallback_clock: Optional[str] = None,
) -> Optional[dict]:
    """與雷達批次查詢同一套欄位。feed_date 只採用交易所 d，不拿本機時鐘填日期。"""
    if not isinstance(item, Mapping):
        return None
    code = str(item.get("c") or "").strip()
    if not code:
        return None
    if allowed_codes is not None and code not in allowed_codes:
        return None
    z_val = item.get("z", "-")
    trade = item.get("trade")
    trade_z = trade.get("z", "-") if isinstance(trade, Mapping) else "-"
    y_val = item.get("y", "-")
    o_val = item.get("o", "-")
    h_val = item.get("h", "-")
    l_val = item.get("l", "-")
    v_val = item.get("v", "-")

    price = None
    if z_val and z_val != "-":
        price = float(z_val)
    elif trade_z and trade_z != "-":
        price = float(trade_z)
    elif y_val and y_val != "-":
        price = float(y_val)

    prev_close = float(y_val) if (y_val and y_val != "-") else price
    change = round(price - prev_close, 2) if (price is not None and prev_close is not None) else 0.0
    change_pct = round((change / prev_close * 100), 2) if (prev_close and prev_close > 0) else 0.0
    feed_time = item.get("t") or None
    return {
        "code": code,
        "name": str(item.get("n") or "").strip(),
        "price": price,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "open": float(o_val) if (o_val and o_val != "-") else None,
        "high": float(h_val) if (h_val and h_val != "-") else None,
        "low": float(l_val) if (l_val and l_val != "-") else None,
        "volume": int(v_val) if (v_val and v_val != "-") else None,
        "time": feed_time or fallback_clock,
        "feed_time": feed_time,
        "feed_date": item.get("d") or None,
    }
