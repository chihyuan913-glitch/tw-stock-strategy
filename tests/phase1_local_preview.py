# -*- coding: utf-8 -*-
"""本機靜態預覽。只回固定 JSON，不匯入選股、行情或推播模組。"""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "web" / "index.html"
STARTED = time.time()


def body_for(path: str):
    if path.startswith("/api/overview"):
        time.sleep(2.5)
        return {
            "status": "online",
            "total_candidates": 8,
            "radar_count": 0,
            "market": {
                "close": "40,087.56",
                "change": None,
                "change_pct": None,
                "ma20": "41,000.00",
                "bias_ma20": -2.2,
                "source": "twse_mis",
                "verified": True,
                "current": True,
                "source_aligned": True,
                "stale": False,
                "as_of": "2026-11-06",
                "fetched_at": "2026-11-08T11:00:00+08:00",
                "timezone": "Asia/Taipei",
                "error": None,
                "quote_inferred_regime": {
                    "label": "🟡【高檔震盪整理・箱型洗盤】",
                    "label_display": "🟡【高檔震盪整理・箱型洗盤】",
                    "status": "ok",
                    "verified": True,
                    "current": True,
                    "reason": "價格與均線同一交易日",
                },
                "effective_regime": {
                    "label_display": "🟡【高檔震盪整理・箱型洗盤】",
                    "cash_display": "45%",
                    "key": "SIDEWAYS_CONSOLIDATION",
                },
            },
            "selection_batches": {
                "unity": "conflict",
                "dates": ["2026-11-06", "2026-10-24"],
                "note": "各策略批次日不同，不合併成單一盤勢日期",
            },
        }
    if path.startswith("/api/weekly_report"):
        return {
            "status": "ok",
            "content": "週報預覽：這段文字應在慢速 overview 完成前出現。",
            "generated_at": "2026-11-01 20:27",
            "file_mtime": "2026-11-01T20:28:17+08:00",
            "timezone": "Asia/Taipei",
        }
    if path.startswith("/api/tomorrow_advisor"):
        return {
            "status": "success",
            "items": [],
            "total": 0,
            "effective_regime": {
                "label_display": "🟡【高檔震盪整理・箱型洗盤】",
                "cash_display": "45%",
                "cash_target_pct": 45,
            },
            "selection_batches": {
                "unity": "conflict",
                "dates": ["2026-11-06", "2026-10-24"],
                "note": "各策略批次日不同，不合併成單一盤勢日期",
            },
            "count_contract": {"note": "此表是每策略最多 2 檔的決策切片，不是五日滾動總數。"},
        }
    if path.startswith("/api/strategy/tiers"):
        return {"status": "ok", "regime_label": "🟡【高檔震盪整理・箱型洗盤】"}
    if path.startswith("/api/strategy/"):
        return {
            "name": "預覽策略",
            "desc": "快取明細，不含即時報價",
            "date": "2026-11-06",
            "rows": [],
            "columns": ["證券代號"],
            "count_contract": {
                "labels": {
                    "rolling_total": "五日滾動 8 檔",
                    "latest_day": "最新交易日 2026-11-06 4 檔",
                    "studio": "Studio 顯示 5／上限 5",
                    "advisor": "T+1 策略11 實際 1 列（上限 2，上限不是實際數）",
                }
            },
            "provenance": {"status": "unknown"},
        }
    if path.startswith("/api/studio/report/"):
        return {
            "status": "ok",
            "report": {
                "id": "11",
                "name": "營收創高與量價突破成長動能",
                "grade": "目錄分級",
                "direction": "LONG",
                "philosophy": "預覽",
                "best_regime": "多頭主升 / 財報營收旺季",
                "metrics_disclaimer": "目錄常數與合成曲線只是示範，不是回測結果，也不是實盤績效。",
                "metrics": {"cagr": 38.6, "sharpe": 1.85, "mdd": -11.4, "win_rate": 71.4, "profit_factor": 2.42},
                "dimensions": [],
                "holdings": [
                    {"code": f"{i:04d}", "name": "預覽", "price": 1, "entry": 1, "addon": 1, "tp1": 1, "sl": 1}
                    for i in range(1, 6)
                ],
                "count_contract": {"studio_limit": 5, "rolling_total": 8, "studio_displayed": 5, "advisor_count": 2},
                "equity_curve": [],
                "python_code": "# preview",
            },
        }
    if path.startswith("/api/radar/watchlist"):
        return {"items": [], "today_triggered_items": []}
    if path.startswith("/api/system/network_info"):
        return {}
    if path.startswith("/api/market_pulse/score"):
        return {
            "score": 4.0,
            "summary": "示範：靜態訊號，不是已驗證收盤計分。",
            "as_of_display": "資料日未知",
            "as_of_date": None,
            "verified": False,
            "metrics_status": "illustrative_unverified",
            "bullish_count": 4,
            "bearish_count": 6,
            "signals": [],
            "history": [],
        }
    if path.startswith("/api/market_pulse/treemap"):
        from mis_quote import parse_mis_quote_item
        from realtime_watch_hub import index_cache_from_quotes
        from dashboard_contract import build_treemap_index_summary
        quote = parse_mis_quote_item(
            {"c": "t00", "n": "發行量加權股價指數", "z": "40087.56", "y": "41000", "d": "20261106", "t": "13:24:01"},
            {"t00"},
        )
        summary = build_treemap_index_summary(
            index_cache_from_quotes({"t00": quote}),
            [{"name": "半導體業", "median_change": -1.2}],
            up_count=3,
            down_count=5,
            flat_count=1,
            total_stocks=9,
        )
        return {"summary": summary, "root": {"name": "預覽", "children": []}, "distribution": [], "scatter_data": []}
    return {"status": "empty"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, content_type="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        print(f"{time.time() - STARTED:.3f} GET {path}", flush=True)
        if path in ("/", "/index.html"):
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/dashboard_guards.js":
            self._send(200, (ROOT / "web" / "dashboard_guards.js").read_bytes(), "application/javascript; charset=utf-8")
            return
        self._send(200, body_for(path))

    def do_POST(self):
        print(f"{time.time() - STARTED:.3f} POST {self.path}", flush=True)
        self._send(200, {"ok": True})

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("phase1 preview http://127.0.0.1:8765/", flush=True)
    server.serve_forever()
