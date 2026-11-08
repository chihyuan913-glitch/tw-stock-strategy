# -*- coding: utf-8 -*-
"""加權指數快照。只讀設定檔與呼叫端注入的行情函式，不寫 regime_config.json。"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from dashboard_contract import TAIPEI, assemble_market_view, parse_iso_date

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT_DIR / "regime_config.json"
DEFAULT_HOLIDAYS = ROOT_DIR / "market_holidays.json"


def read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def holiday_dates(path: Path = DEFAULT_HOLIDAYS) -> set:
    data = read_json(path) or {}
    found = set()
    holidays = data.get("holidays") if isinstance(data, dict) else None
    if isinstance(holidays, dict):
        for key, value in holidays.items():
            parsed = parse_iso_date(key)
            if parsed is None and isinstance(value, dict):
                parsed = parse_iso_date(value.get("date"))
            if parsed:
                found.add(parsed)
    return found


def fetch_twse_index() -> dict:
    url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=4) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    msg_list = payload.get("msgArray") or []
    if not msg_list:
        raise RuntimeError("證交所大盤回應沒有 msgArray")
    msg = msg_list[0]
    z_str = msg.get("z", "")
    y_str = msg.get("y", "")
    curr = float(z_str) if z_str and z_str != "-" else (float(y_str) if y_str else 0.0)
    prev = float(y_str) if y_str and y_str != "-" else curr
    if curr <= 0:
        raise RuntimeError("證交所大盤價格無效")
    change = round(curr - prev, 2)
    change_pct = round((change / prev * 100), 2) if prev else 0.0
    return {
        "close_raw": curr,
        "prev_close": prev,
        "change": change,
        "change_pct": change_pct,
        "date": msg.get("d", ""),
        "time": msg.get("t", ""),
    }


def fetch_yfinance_index() -> dict:
    import pandas as pd
    import yfinance as yf

    frame = yf.download("^TWII", period="4mo", interval="1d", progress=False)
    if frame is None or getattr(frame, "empty", True) or len(frame) < 20:
        raise RuntimeError("yfinance 沒有足夠的加權指數日線")
    series = frame["Close"]
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    series = series.dropna()
    last_p = float(series.iloc[-1])
    prev_p = float(series.iloc[-2]) if len(series) >= 2 else last_p
    change = round(last_p - prev_p, 2)
    change_pct = round((change / prev_p * 100), 2) if prev_p else 0.0
    ma20 = float(series.rolling(20).mean().iloc[-1])
    ma60 = float(series.rolling(60).mean().iloc[-1]) if len(series) >= 60 else ma20
    return {
        "close_raw": last_p,
        "prev_close": prev_p,
        "change": change,
        "change_pct": change_pct,
        "date": str(series.index[-1])[:10],
        "time": "13:30:00",
        "ma20": ma20,
        "ma60": ma60,
    }


def build_market_snapshot(
    *,
    config_path: Path = DEFAULT_CONFIG,
    holiday_path: Path = DEFAULT_HOLIDAYS,
    now: Optional[dt.datetime] = None,
    fetch_twse: Optional[Callable[[], Any]] = None,
    fetch_yfinance: Optional[Callable[[], Any]] = None,
    holidays: Optional[Iterable[dt.date]] = None,
) -> dict:
    """讀取快照。config_path 只以讀取模式開啟。"""
    config = read_json(config_path)
    closed = set(holidays) if holidays is not None else holiday_dates(holiday_path)
    twse_fetcher = fetch_twse if fetch_twse is not None else fetch_twse_index
    yf_fetcher = fetch_yfinance if fetch_yfinance is not None else fetch_yfinance_index

    twse = None
    twse_error = None
    try:
        twse = twse_fetcher()
    except Exception as exc:
        twse_error = str(exc)

    yf_bar = None
    yf_error = None
    try:
        yf_bar = yf_fetcher()
    except Exception as exc:
        yf_error = str(exc)

    if isinstance(twse, dict) and twse.get("error") and not twse.get("close_raw"):
        twse_error = twse.get("error")
        twse = None
    if isinstance(yf_bar, dict) and yf_bar.get("error") and not yf_bar.get("close_raw") and not yf_bar.get("ma20"):
        yf_error = yf_bar.get("error")
        yf_bar = None

    return assemble_market_view(
        twse=twse if isinstance(twse, dict) else None,
        yfinance_bar=yf_bar if isinstance(yf_bar, dict) else None,
        config=config,
        twse_error=twse_error,
        yfinance_error=yf_error,
        now=now or dt.datetime.now(TAIPEI),
        holidays=closed,
    )
