# -*- coding: utf-8 -*-
"""證交所年度開休市公告解析。只讀快照，不連線、不改選股日曆。"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from dashboard_contract import parse_iso_date, unreliable_calendar

ROOT_DIR = Path(__file__).resolve().parent
SNAPSHOT_PATH = ROOT_DIR / "dashboard_twse_calendar.json"
PAYLOAD_PATH = ROOT_DIR / "dashboard_twse_calendar.payload.json"
SOURCE_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
TRADING_MARKERS = ("開始交易", "最後交易")


def roc_to_gregorian(value: Any) -> Optional[date]:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) != 7:
        return None
    year = int(text[:3]) + 1911
    month = int(text[3:5])
    day = int(text[5:7])
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _is_trading_marker(row: Mapping[str, Any]) -> bool:
    text = f"{row.get('Name') or ''}{row.get('Description') or ''}"
    return any(marker in text for marker in TRADING_MARKERS)


def calendar_from_payload(payload: Any, meta: Optional[Mapping[str, Any]] = None) -> dict:
    """由官方 JSON 列建成日曆。列不完整、跨年混雜或缺少開市標記時不可判定。"""
    meta = meta or {}
    if not isinstance(payload, list) or not payload:
        return unreliable_calendar("開休市公告是空的或不是列表，不判定過期")
    closed = set()
    explicit_open = set()
    years = set()
    for row in payload:
        if not isinstance(row, dict) or "Date" not in row or "Name" not in row:
            return unreliable_calendar("開休市公告缺欄位，不判定過期")
        day = roc_to_gregorian(row.get("Date"))
        if day is None:
            return unreliable_calendar("開休市公告有無法解析的民國日期，不判定過期")
        years.add(day.year)
        if _is_trading_marker(row):
            explicit_open.add(day)
        else:
            closed.add(day)
    if len(years) != 1:
        return unreliable_calendar("開休市公告不是單一年度，不把未列年份硬當成開市")
    year = next(iter(years))
    if not explicit_open or min(day.month for day in closed | explicit_open) != 1 or max(day.month for day in closed | explicit_open) != 12:
        return unreliable_calendar("開休市公告沒有跨全年的開市標記，不宣稱該年完整")
    return {
        "reliable": True,
        "trust": "twse_annual_schedule_snapshot",
        "closed": closed,
        "explicit_open": explicit_open,
        "years": {year},
        "coverage_start": date(year, 1, 1).isoformat(),
        "coverage_end": date(year, 12, 31).isoformat(),
        "authoritative_as_of": meta.get("fetched_at"),
        "source_url": meta.get("source_url") or SOURCE_URL,
        "payload_sha256": meta.get("payload_sha256"),
        "http_last_modified": meta.get("http_last_modified"),
        "last_synced": meta.get("fetched_at"),
        "reason": "僅涵蓋公告中的單一年度。快照之後的臨時休市與公告修訂不在表內，年度表不是未來永不變。",
        "emergency_revision_limit": "authoritative_as_of 之後新增的臨時休市不會自動反映。",
    }


def load_snapshot(meta_path: Path = SNAPSHOT_PATH, payload_path: Path = PAYLOAD_PATH) -> dict:
    if not meta_path.exists() or not payload_path.exists():
        return unreliable_calendar("沒有證交所開休市快照，不判定過期")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        raw = payload_path.read_bytes()
    except Exception:
        return unreliable_calendar("開休市快照無法讀取，不判定過期")
    digest = hashlib.sha256(raw).hexdigest()
    if not meta.get("payload_sha256") or digest != meta.get("payload_sha256"):
        return unreliable_calendar("開休市快照雜湊不符，不判定過期")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return unreliable_calendar("開休市快照 JSON 損壞，不判定過期")
    return calendar_from_payload(payload, meta)
