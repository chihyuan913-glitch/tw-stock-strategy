#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人工更新儀表板用的證交所開休市快照。

不加入排程，不呼叫選股、推播或既有 workflow。
只有直接執行此檔才會連線；被 import 時不會抓取。
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
META_PATH = ROOT / "dashboard_twse_calendar.json"
PAYLOAD_PATH = ROOT / "dashboard_twse_calendar.payload.json"


def fetch_payload() -> tuple[bytes, str, str]:
    request = urllib.request.Request(SOURCE_URL, headers={"Accept": "application/json", "User-Agent": "dashboard-twse-calendar-sync"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        last_modified = response.headers.get("Last-Modified") or ""
        http_date = response.headers.get("Date") or ""
    json.loads(raw.decode("utf-8"))
    return raw, last_modified, http_date


def main() -> int:
    raw, last_modified, http_date = fetch_payload()
    fetched = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    PAYLOAD_PATH.write_bytes(raw)
    meta = {
        "source_url": SOURCE_URL,
        "source_html": "https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=html",
        "source_open_data": "https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=open_data",
        "dataset_page": "https://data.gov.tw/en/datasets/11761",
        "fetched_at": fetched,
        "http_date": http_date,
        "http_last_modified": last_modified,
        "content_type": "application/json",
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "payload_bytes": len(raw),
        "coverage_rule": "Single ROC year in the TWSE annual open/closed schedule only. Emergency closures after fetched_at are not included.",
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(meta["payload_sha256"], meta["payload_bytes"], fetched)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
