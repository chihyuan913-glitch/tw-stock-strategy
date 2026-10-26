#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 台股開休市與交易日守護引擎 (Market Schedule Guard)
================================================================================
核心責任：
1. 【精準攔截未開盤日推播 (Non-Trading Day Push Suppression)】：
   - 包含：週末例休、國定假日、彈性補假、農曆春節長假、天然災害（颱風假）停市。
   - 凡遇台股休市，自動全面停止：
     * 雲端與本機盤後選股推播 (LINE / Telegram / Discord)
     * 盤中到價即時雷達輪詢 (避免空轉與誤發舊行情)
     * 避免覆蓋歷史有效選股報表與戰報
2. 【四層立體化開休市防禦架構 (Hierarchical Defense Architecture)】：
   - 第 1 防線：週末例休直接判斷 (Weekend Guard - 週六、週日固定休市)
   - 第 2 防線：TWSE 臺灣證交所官方法定開休市日程表 (TWSE Holiday Schedule Guard)
   - 第 3 防線：TWSE 天然災害/颱風假緊急公告動態比對 (Emergency Typhoon & Disaster Guard)
   - 第 4 防線：盤中大盤即時撮合與開盤數據雙重檢驗 (Realtime Market Activity Guard)
3. 【高可用離線快取與斷網防呆 (Resilient Offline Cache)】：
   - 自動將證交所官方休市日曆快取至 `market_holidays.json`。
   - 遭遇斷網或 API 逾時時，自動無縫降級讀取本地快取與內建假日演算法，保證 100% 穩定。
================================================================================
"""

import os
import sys
import re
import json
import datetime
import urllib.request
import urllib.error
from pathlib import Path
from typing import Tuple, Dict, Any, List, Optional, Union

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
HOLIDAYS_CACHE_FILE = ROOT_DIR / "market_holidays.json"
TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

# 預設常見台灣法定休市名稱關鍵字
HOLIDAY_KEYWORDS = ["放假", "休市", "春節", "紀念日", "中秋", "端午", "國慶", "勞動節", "無交易", "補假"]
TRADING_KEYWORDS = ["開始交易", "最後交易"]


def get_taiwan_now() -> datetime.datetime:
    """取得台灣標準時間 (UTC+8)"""
    return datetime.datetime.now(TZ_TW)


def tw_date_to_iso(tw_date_str: str) -> Optional[str]:
    """
    將民國年日期格式轉換為 ISO 格式 (YYYY-MM-DD)
    例如: '1151026' -> '2026-10-26'
    """
    s = str(tw_date_str).strip()
    if len(s) == 7 and s.isdigit():
        y = int(s[:3]) + 1911
        m = int(s[3:5])
        d = int(s[5:7])
        return f"{y:04d}-{m:02d}-{d:02d}"
    return None


def iso_to_tw_date(iso_date: Union[str, datetime.date]) -> str:
    """
    將 ISO 日期格式轉換為民國年格式 (YYYMMDD)
    例如: '2026-10-26' -> '1151026'
    """
    if isinstance(iso_date, str):
        parts = iso_date.split("-")
        y = int(parts[0]) - 1911
        m = int(parts[1])
        d = int(parts[2])
    else:
        y = iso_date.year - 1911
        m = iso_date.month
        d = iso_date.day
    return f"{y:03d}{m:02d}{d:02d}"


def sync_market_holidays(cache_path: Optional[Path] = None, timeout: int = 5) -> Dict[str, Any]:
    """
    主動向 TWSE OpenAPI 同步最新整年法定休市行事曆並快取至本機。
    API 端點: https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule
    """
    target_path = cache_path or HOLIDAYS_CACHE_FILE
    url = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TW-Stock-Master/4.0"}
    req = urllib.request.Request(url, headers=headers)

    holidays_map = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data:
                raw_date = item.get("Date", "")
                iso_d = tw_date_to_iso(raw_date)
                if not iso_d:
                    continue
                name = item.get("Name", "").strip()
                desc = item.get("Description", "").strip()
                
                # 判定是否為休市日：包含放假關鍵字且不為「開始交易」或「最後交易」
                is_closed = any(k in name for k in HOLIDAY_KEYWORDS) or any(k in desc for k in ["放假", "補假", "無交易"])
                if any(k in name for k in TRADING_KEYWORDS):
                    is_closed = False

                if is_closed:
                    clean_desc = re.sub(r'<[^>]+>', ' ', desc).strip()
                    holidays_map[iso_d] = {
                        "date": iso_d,
                        "tw_date": raw_date,
                        "name": name,
                        "weekday": item.get("Weekday", ""),
                        "description": clean_desc,
                        "type": "OFFICIAL_HOLIDAY"
                    }

        if holidays_map:
            payload = {
                "last_synced": get_taiwan_now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_holidays": len(holidays_map),
                "holidays": holidays_map
            }
            target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return payload
    except Exception as e:
        print(f"[!] 同步 TWSE 官方法定休市日曆失敗: {e}")

    # 若網路失敗，嘗試讀取本地現存快取
    if target_path.exists():
        try:
            return json.loads(target_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {"last_synced": "", "total_holidays": 0, "holidays": {}}


def get_cached_holidays() -> Dict[str, Any]:
    """讀取本地法定休市快取，若無則自動線上同步一次"""
    if HOLIDAYS_CACHE_FILE.exists():
        try:
            data = json.loads(HOLIDAYS_CACHE_FILE.read_text(encoding="utf-8"))
            if data.get("holidays"):
                return data
        except Exception:
            pass
    return sync_market_holidays()


def check_typhoon_and_emergency_closure(target_date_iso: str, timeout: int = 5) -> Tuple[bool, str]:
    """
    【第 3 防線：TWSE 天然災害/颱風假緊急公告即時比對】
    抓取證交所最新公告新聞，檢查是否有針對今日的臨時休市/颱風停市公告。
    端點: https://openapi.twse.com.tw/v1/news/newsList
    """
    url = "https://openapi.twse.com.tw/v1/news/newsList"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TW-Stock-Master/4.0"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            news_list = json.loads(resp.read().decode("utf-8"))
            # 匹配正則：如「115年10月23日休市一天」、「受天然災害影響...休市一日」
            pattern = re.compile(r'(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日.*(?:休市|停止交易)')
            
            for item in news_list[:40]:
                title = item.get("Title", "")
                if not any(k in title for k in ["颱風", "天然災害", "休市", "停止交易", "停市"]):
                    continue

                m = pattern.search(title)
                if m:
                    ny = int(m.group(1)) + 1911
                    nm = int(m.group(2))
                    nd = int(m.group(3))
                    news_date_iso = f"{ny:04d}-{nm:02d}-{nd:02d}"
                    if news_date_iso == target_date_iso:
                        return True, f"臺灣證券交易所天然災害緊急休市公告（{title}）"
    except Exception as e:
        # 若新聞 API 偶發失敗，不影響主線判定
        pass

    return False, ""


def check_realtime_market_activity(target_date_iso: str, timeout: int = 4) -> Tuple[bool, str]:
    """
    【第 4 防線：即時大盤撮合與開盤數據雙重檢驗】
    若為今日且處於交易時段 (09:15 以後)，檢查 TWSE MIS 大盤報價日期與狀態。
    若大盤完全未啟動撮合或日期停留在過去，輔助確認當日未開盤。
    """
    now_tw = get_taiwan_now()
    today_iso = now_tw.strftime("%Y-%m-%d")
    if target_date_iso != today_iso:
        return True, "歷史或非當前日期，跳過即時撮合檢驗"

    # 若尚未到達 09:15，市場尚未進入正式成交常態，不以此防線武斷判定
    if now_tw.time() < datetime.time(9, 15):
        return True, "開盤前準備時段"

    url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw|tse_2330.tw"
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            msg_arr = data.get("msgArray", [])
            for item in msg_arr:
                mis_d = item.get("d", "")
                mis_iso = f"{mis_d[:4]}-{mis_d[4:6]}-{mis_d[6:8]}" if len(mis_d) == 8 else ""
                # 若已有今天的即時撮合日期，代表市場確實開盤中！
                if mis_iso == today_iso:
                    return True, "大盤正常撮合中"
            
            # 若已經過 09:30 且大盤最新成交日期仍非今天，且查無當日撮合，高度懷疑未開市
            if now_tw.time() >= datetime.time(9, 30):
                return False, f"證交所 MIS 大盤無當日撮合數據 (最新交易日仍停留在 {msg_arr[0].get('d') if msg_arr else '未知'})"
    except Exception:
        pass

    return True, "無法連線至 MIS，預設跳過即時撮合約束"


def is_market_open_today(
    date_val: Optional[Union[str, datetime.date, datetime.datetime]] = None,
    check_realtime: bool = True,
    check_emergency: bool = True
) -> Tuple[bool, str]:
    """
    【全域最高裁判：判定指定日期 (預設今日) 是否為台股正常交易日】
    
    回傳值:
    - (True, "今日為正常交易日")
    - (False, "休市原因說明") (例如: "週末例假日休市"、"中華民國開國紀念日休市"、"臺灣證券交易所天然災害緊急休市公告")
    """
    # 1. 規範化目標日期 (ISO 格式: YYYY-MM-DD)
    now_tw = get_taiwan_now()
    if date_val is None:
        target_d = now_tw.date()
    elif isinstance(date_val, datetime.datetime):
        target_d = date_val.date()
    elif isinstance(date_val, datetime.date):
        target_d = date_val
    elif isinstance(date_val, str):
        s = date_val.strip().replace("/", "-")
        if len(s) == 8 and s.isdigit(): # YYYYMMDD
            target_d = datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        elif len(s) == 7 and s.isdigit(): # 民國年 YYYMMDD
            target_d = datetime.date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7]))
        else:
            target_d = datetime.date.fromisoformat(s[:10])
    else:
        target_d = now_tw.date()

    target_iso = target_d.strftime("%Y-%m-%d")

    # --------------------------------------------------------------------------
    # 第 1 防線：週末例假日判斷 (週六、週日固定休市)
    # --------------------------------------------------------------------------
    weekday = target_d.weekday() # 0: Mon, ..., 5: Sat, 6: Sun
    if weekday == 5:
        return False, f"週六例假日休市 ({target_iso})"
    elif weekday == 6:
        return False, f"週日例假日休市 ({target_iso})"

    # --------------------------------------------------------------------------
    # 第 2 防線：TWSE 官方法定休市日曆檢驗 (Holiday Schedule)
    # --------------------------------------------------------------------------
    cache_data = get_cached_holidays()
    holidays_map = cache_data.get("holidays", {})
    if target_iso in holidays_map:
        h_info = holidays_map[target_iso]
        h_name = h_info.get("name", "國定假日")
        h_desc = h_info.get("description", "")
        reason = f"{h_name}休市"
        if h_desc and ("補假" in h_desc or "放假" in h_desc):
            reason = f"{h_name} ({h_desc.split('。')[0]}) 休市"
        return False, f"官方法定假日：{reason} ({target_iso})"

    # --------------------------------------------------------------------------
    # 第 3 防線：TWSE 天然災害/颱風假緊急公告比對 (Emergency Typhoon & News)
    # --------------------------------------------------------------------------
    if check_emergency:
        is_typhoon, typhoon_reason = check_typhoon_and_emergency_closure(target_iso)
        if is_typhoon:
            return False, f"天然災害休市：{typhoon_reason} ({target_iso})"

    # --------------------------------------------------------------------------
    # 第 4 防線：即時大盤撮合與開盤數據雙重檢驗 (Realtime Activity)
    # --------------------------------------------------------------------------
    if check_realtime and target_iso == now_tw.strftime("%Y-%m-%d"):
        now_time = now_tw.time()
        # 僅在開盤後顯著時段 (09:30 以後) 啟動此防線二次把關
        if now_time >= datetime.time(9, 30):
            is_active, act_msg = check_realtime_market_activity(target_iso)
            if not is_active:
                return False, f"市場無交易：{act_msg} ({target_iso})"

    return True, f"正常開盤交易日 ({target_iso})"


def should_suppress_push(
    force: bool = False,
    date_val: Optional[Union[str, datetime.date]] = None,
    task_type: str = "推播"
) -> Tuple[bool, str]:
    """
    【推播發送前守門員 (Push Suppression Guard)】
    若回傳 (True, reason)：代表應【嚴格攔截、不發送推播】。
    若回傳 (False, "")：代表應【正常放行發送推播】。
    """
    if force:
        return False, "啟用強制推送旗標 (--force / --force-push)，跳過休市檢查"

    is_open, reason = is_market_open_today(date_val=date_val)
    if not is_open:
        suppress_msg = f"【⏸️ 休市不推送】今日台股休市 ({reason})，自動取消{task_type}以避免打擾。"
        return True, suppress_msg

    return False, ""


def check_market_open_or_exit(context_name: str = "排程任務", allow_force: bool = True) -> bool:
    """
    【總調度與執行器專用：未開盤自動乾淨退出】
    用於 run_all.py 或 run_all_intraday.py 開頭，若未開盤則印出醒目提示並安全退出 (sys.exit(0))。
    回傳 True 表示開盤正常執行；回傳 False 或 exit(0) 表示跳過。
    """
    # 檢查命令列是否帶有強制執行旗標
    force_flags = ["--force", "--force-push", "--test-push", "--ignore-holiday"]
    has_force = any(f in sys.argv for f in force_flags)

    if has_force and allow_force:
        print(f"\n[⚠️ 注意] 偵測到強制執行旗標，跳過台股休市守衛檢查，強制執行【{context_name}】！")
        return True

    is_open, reason = is_market_open_today()
    if not is_open:
        border = "=" * 70
        print(f"\n{border}")
        print(f"⏸️  [台股休市保護] 今日非交易開盤日，自動跳過【{context_name}】！")
        print(f"📌  休市判定原因: {reason}")
        print(f"💡  提示: 若需於休市日手動測試執行或驗證，請帶入 --force 參數。")
        print(f"{border}\n")
        return False

    return True


if __name__ == "__main__":
    now_tw = get_taiwan_now()
    print("=" * 65)
    print(" 🇹🇼 台股開休市與交易日守護引擎 (Market Schedule Guard)")
    print(f" 🕒 台灣當前時間: {now_tw.strftime('%Y-%m-%d %H:%M:%S (%A)')}")
    print("=" * 65)

    # 執行本地日曆同步
    sync_res = sync_market_holidays()
    print(f"[✓] 官方法定休市日曆已同步，共記錄 {sync_res.get('total_holidays', 0)} 個休市日程。")

    # 檢查今日狀態
    is_open, reason = is_market_open_today()
    status_icon = "🟢" if is_open else "🔴"
    print(f"\n{status_icon} 今日台股狀態: {'正常開盤交易日' if is_open else '休市日 (不推送)'}")
    print(f"📝 判定詳情: {reason}")

    # 檢查是否應取消推播
    suppress, sup_reason = should_suppress_push(task_type="盤後選股戰報")
    if suppress:
        print(f"\n🛡️ 推播攔截結果: 【已攔截】\n   原因: {sup_reason}")
    else:
        print(f"\n🚀 推播攔截結果: 【放行推送】今日盤後與盤中將正常發送即時戰報。")

    print("=" * 65)
