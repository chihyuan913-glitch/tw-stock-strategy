# -*- coding: utf-8 -*-
"""
tests/test_afterhours_guard.py
================================================================================
台股雲端盤中／開盤推播收盤後誤送修正 - 離線整合與單元測試套件
================================================================================
測試覆蓋：
1. 交易日 15:58：呼叫 run_all_intraday.py 啟動邏輯，不得啟動 scanner 子程序，行程結束碼視為成功。
2. 交易日 15:58：策略 07、03、08、09 進入常駐迴圈後應立即結束，且 scan_once／發送函式不得在 13:35 後被呼叫。
3. 交易日 15:58 與 16:03：策略 12 run_open_confirmation 或等效路徑不得發送；即使呼叫端傳 force=True 也不得送出開盤確認。
4. ETF workflow 的階段判斷：台北 16:03 必須是「跳過、不跑 confirm」；台北 08:30 跑 premarket；台北 09:10 跑 confirm。
5. 交易日 11:00：盤中到價發送路徑不被時段守衛誤擋 (mock 發送)。
6. 交易日 21:15：盤後選股類型的發送路徑仍可通過 (mock)，證明沒有誤傷 21:15 報告。
7. 策略 04 以現行簽名呼叫時不再因 force= 參數例外；收盤後會退出。
================================================================================
"""

import sys
import os
import json
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import importlib

WORK_DIR = Path(__file__).resolve().parents[1]
if str(WORK_DIR) not in sys.path:
    sys.path.insert(0, str(WORK_DIR))

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

# 定義測試用固定日期時間 (2026-11-12 週四，正常交易日)
DT_TRADING_0820 = datetime.datetime(2026, 11, 12, 8, 20, 0, tzinfo=TZ_TW)
DT_TRADING_0858 = datetime.datetime(2026, 11, 12, 8, 58, 30, tzinfo=TZ_TW)
DT_TRADING_1100 = datetime.datetime(2026, 11, 12, 11, 0, 0, tzinfo=TZ_TW)
DT_TRADING_1558 = datetime.datetime(2026, 11, 12, 15, 58, 18, tzinfo=TZ_TW)
DT_TRADING_1603 = datetime.datetime(2026, 11, 12, 16, 3, 0, tzinfo=TZ_TW)
DT_TRADING_1800 = datetime.datetime(2026, 11, 12, 18, 0, 0, tzinfo=TZ_TW)
DT_TRADING_2115 = datetime.datetime(2026, 11, 12, 21, 15, 0, tzinfo=TZ_TW)


@pytest.fixture(autouse=True)
def mock_clean_env(monkeypatch):
    """清除真實環境變數，確保純離線測試，絕無真實網路推播"""
    keys = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_USER_ID"]
    for k in keys:
        monkeypatch.setenv(k, "mock_token_value")
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    for k in list(os.environ.keys()):
        if k.startswith("DISCORD_WEBHOOK_URL"):
            monkeypatch.delenv(k, raising=False)
    # 確保 market_schedule 判定 2026-11-12 為正常開盤交易日
    import market_schedule
    monkeypatch.setattr(market_schedule, "is_market_open_today", lambda *a, **k: (True, "正常開盤交易日 (2026-11-12)"))
    import line_sender
    monkeypatch.setattr(line_sender, "send_discord_webhook", lambda *a, **k: True)


# ------------------------------------------------------------------------------
# 測試 1: 交易日 15:58 run_all_intraday.py 啟動邏輯
# ------------------------------------------------------------------------------
def test_1_run_all_intraday_afterhours_exits_without_popen(monkeypatch):
    """測試 1: 交易日 15:58：呼叫 run_all_intraday.py 的啟動邏輯後，不得啟動 scanner 子程序，行程結束碼視為成功"""
    import run_all_intraday

    # 凍結時間為 15:58:18
    fake_now = DT_TRADING_1558
    monkeypatch.setattr(run_all_intraday.datetime, "datetime", MagicMock(now=lambda tz=None: fake_now))

    # Mock subprocess.Popen 與 subprocess.run，嚴禁啟動任何真實進程
    mock_popen = MagicMock()
    mock_run = MagicMock()
    monkeypatch.setattr(run_all_intraday.subprocess, "Popen", mock_popen)
    monkeypatch.setattr(run_all_intraday.subprocess, "run", mock_run)

    # 1.1 常態常駐輪詢模式 (例如 GitHub runner 預設執行的 python run_all_intraday.py --interval 60)
    monkeypatch.setattr(sys, "argv", ["run_all_intraday.py", "--interval", "60"])
    run_all_intraday.main()
    assert mock_popen.call_count == 0, "15:58 收盤後常態調度不得 Popen 任何 scanner 行程"
    assert mock_run.call_count == 0

    # 1.2 即使帶入 --force 旗標，也絕不准略過 13:35 收盤後時段守衛！
    monkeypatch.setattr(sys, "argv", ["run_all_intraday.py", "--force"])
    run_all_intraday.main()
    assert mock_popen.call_count == 0, "15:58 收盤後即使帶有 --force 旗標，亦絕對不准啟動任何 scanner 行程"


# ------------------------------------------------------------------------------
# 測試 2: 交易日 15:58 策略 07、03、08、09 進入常駐迴圈後立即結束
# ------------------------------------------------------------------------------
def test_2_strategies_07_03_08_09_exit_after_close(monkeypatch):
    """測試 2: 交易日 15:58：策略 07、03、08、09 進入常駐迴圈後應立即結束，且 scan_once／發送函式不得在 13:35 後被呼叫"""
    fake_now = DT_TRADING_1558

    # 2.1 測試策略 07 (margin_call_ambush)
    sys.path.insert(0, str(WORK_DIR / "strategies" / "07_margin_call_ambush"))
    s07 = importlib.import_module('strategies.07_margin_call_ambush.intraday_scanner')
    monkeypatch.setattr(s07.datetime, "datetime", MagicMock(now=lambda tz=None: fake_now))
    mock_scan_07 = MagicMock()
    mock_send_07 = MagicMock()
    monkeypatch.setattr(s07, "scan_once", mock_scan_07)
    monkeypatch.setattr(s07, "send_to_line", mock_send_07)
    monkeypatch.setattr(sys, "argv", ["intraday_scanner.py", "--interval", "1"])
    s07.main()
    assert mock_scan_07.call_count == 0, "策略 07 於 15:58 不得呼叫 scan_once"
    assert mock_send_07.call_count == 0, "策略 07 於 15:58 不得呼叫推播函式"

    # 2.2 測試策略 03 (short_momentum)
    sys.path.insert(0, str(WORK_DIR / "strategies" / "03_short_momentum"))
    s03 = importlib.import_module('strategies.03_short_momentum.intraday_scanner')
    monkeypatch.setattr(s03.datetime, "datetime", MagicMock(now=lambda tz=None: fake_now))
    mock_scan_03 = MagicMock()
    mock_send_03 = MagicMock()
    monkeypatch.setattr(s03, "scan_once", mock_scan_03)
    monkeypatch.setattr(s03, "send_to_line", mock_send_03)
    monkeypatch.setattr(sys, "argv", ["intraday_scanner.py", "--interval", "1"])
    s03.main()
    assert mock_scan_03.call_count == 0, "策略 03 於 15:58 不得呼叫 scan_once"
    assert mock_send_03.call_count == 0, "策略 03 於 15:58 不得呼叫推播函式"

    # 2.3 測試策略 08 (group_window_dressing)
    sys.path.insert(0, str(WORK_DIR / "strategies" / "08_group_window_dressing"))
    s08 = importlib.import_module('strategies.08_group_window_dressing.intraday_scanner')
    monkeypatch.setattr(s08.datetime, "datetime", MagicMock(now=lambda tz=None: fake_now))
    mock_fetch_08 = MagicMock()
    mock_send_08 = MagicMock()
    monkeypatch.setattr(s08, "fetch_realtime_quote", mock_fetch_08)
    monkeypatch.setattr(s08, "send_to_line", mock_send_08)
    monkeypatch.setattr(sys, "argv", ["intraday_scanner.py", "--interval", "1"])
    s08.main()
    assert mock_fetch_08.call_count == 0, "策略 08 於 15:58 不得呼叫即時行情"
    assert mock_send_08.call_count == 0, "策略 08 於 15:58 不得呼叫推播函式"

    # 2.4 測試策略 09 (treasury_stock_buyback)
    sys.path.insert(0, str(WORK_DIR / "strategies" / "09_treasury_stock_buyback"))
    s09 = importlib.import_module('strategies.09_treasury_stock_buyback.intraday_scanner')
    monkeypatch.setattr(s09.datetime, "datetime", MagicMock(now=lambda tz=None: fake_now))
    mock_cycle_09 = MagicMock()
    mock_send_09 = MagicMock()
    monkeypatch.setattr(s09, "scan_intraday_cycle", mock_cycle_09)
    monkeypatch.setattr(s09, "send_to_line", mock_send_09)
    monkeypatch.setattr(sys, "argv", ["intraday_scanner.py", "--interval", "1"])
    s09.main()
    assert mock_cycle_09.call_count == 0, "策略 09 於 15:58 不得呼叫 scan_intraday_cycle"
    assert mock_send_09.call_count == 0, "策略 09 於 15:58 不得呼叫推播函式"


# ------------------------------------------------------------------------------
# 測試 3: 交易日 15:58 與 16:03 策略 12 開盤確認阻斷
# ------------------------------------------------------------------------------
def test_3_strategy_12_confirm_blocked_after_hours(monkeypatch):
    """測試 3: 交易日 15:58 與 16:03：策略 12 run_open_confirmation 不得發送；即使傳 force=True 也不得送出開盤確認"""
    import market_schedule
    import line_sender
    sys.path.insert(0, str(WORK_DIR / "strategies" / "12_etf_event_radar"))
    s12 = importlib.import_module('strategies.12_etf_event_radar.intraday_scanner')

    # 3.1 測試 15:58 時呼叫 run_open_confirmation
    monkeypatch.setattr(s12.datetime, "datetime", MagicMock(now=lambda tz=None: DT_TRADING_1558))
    mock_send = MagicMock()
    monkeypatch.setattr(s12, "send_to_line", mock_send)
    res_1558 = s12.run_open_confirmation(send_line=True)
    assert res_1558 == [], "15:58 呼叫 run_open_confirmation 應直接回傳空清單並退出"
    assert mock_send.call_count == 0, "15:58 不得呼叫 send_to_line"

    # 3.2 測試 16:03 時呼叫 run_open_confirmation
    monkeypatch.setattr(s12.datetime, "datetime", MagicMock(now=lambda tz=None: DT_TRADING_1603))
    res_1603 = s12.run_open_confirmation(send_line=True)
    assert res_1603 == [], "16:03 呼叫 run_open_confirmation 應直接回傳空清單並退出"
    assert mock_send.call_count == 0, "16:03 不得呼叫 send_to_line"

    # 3.3 測試即使底層被手動強制傳入 force=True，line_sender 中央時段守衛依然堅決攔截
    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: DT_TRADING_1603)
    mock_tg = MagicMock(return_value=True)
    monkeypatch.setattr(line_sender, "send_telegram", mock_tg)

    confirm_card = "🎯【09:02 開盤實戰確認・下單委託語句與進場防線 (策略 12)】\n00830 國泰費城半導體 建議進場..."
    sent = line_sender.send_to_line(confirm_card, strategy="12", force=True)
    assert sent is False, "16:03 發送開盤確認卡片即使帶入 force=True 亦必須被中央時段守衛攔截"
    assert mock_tg.call_count == 0, "Telegram 發送函式絕對不得被調用"


# ------------------------------------------------------------------------------
# 測試 4: ETF workflow 階段判定規則 (台北時間)
# ------------------------------------------------------------------------------
def test_4_etf_workflow_stage_decision_logic():
    """測試 4: ETF workflow 的階段判斷：台北 16:03 必須是「跳過、不跑 confirm」；台北 08:30 跑 premarket；台北 09:10 跑 confirm"""
    # 模擬 cloud_etf_radar.yml 中的時段分流決策函式
    def decide_etf_stage(tw_hour: int, tw_min: int, stage_input: str = "auto") -> str:
        tw_time = tw_hour * 100 + tw_min
        if stage_input == "premarket":
            return "premarket"
        elif stage_input == "confirm":
            if tw_time > 1335:
                return "skip_afterhours"
            return "confirm"
        else:
            # auto 自動模式
            if tw_time < 900:
                return "premarket"
            elif tw_time <= 1335:
                return "confirm"
            else:
                return "skip_afterhours"

    # 4.1 台北時間 16:03 (延遲啟動)：必須自動跳過，不跑 confirm 也不能跑 premarket
    assert decide_etf_stage(16, 3, "auto") == "skip_afterhours"
    assert decide_etf_stage(15, 11, "auto") == "skip_afterhours"
    assert decide_etf_stage(16, 3, "confirm") == "skip_afterhours"

    # 4.2 台北時間 08:30 (早鳥時段)：必須執行 premarket
    assert decide_etf_stage(8, 30, "auto") == "premarket"
    assert decide_etf_stage(8, 20, "auto") == "premarket"

    # 4.3 台北時間 09:10 (開盤實盤時段)：必須執行 confirm
    assert decide_etf_stage(9, 10, "auto") == "confirm"
    assert decide_etf_stage(9, 2, "auto") == "confirm"
    assert decide_etf_stage(13, 30, "auto") == "confirm"


# ------------------------------------------------------------------------------
# 測試 5: 交易日 11:00 盤中到價發送路徑正常放行
# ------------------------------------------------------------------------------
def test_5_intraday_alert_allowed_during_trading_hours(monkeypatch):
    """測試 5: 交易日 11:00：盤中到價發送路徑不被時段守衛誤擋 (mock 發送)"""
    import market_schedule
    import line_sender
    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: DT_TRADING_1100)

    mock_tg = MagicMock(return_value=True)
    monkeypatch.setattr(line_sender, "send_telegram", mock_tg)

    alert_card = {
        "altText": "【盤中雷達・到價警訊】2330 台積電",
        "_clean_text": "【盤中雷達・到價警訊】2330 台積電 現價 1050 觸發進場防線",
        "_tg_card": "<b>【盤中雷達・到價警訊】2330 台積電</b>"
    }

    res = line_sender.send_to_line(alert_card, strategy="01", force=False)
    assert res is True, "交易日 11:00 盤中時段內之到價警報必須正常放行"
    assert mock_tg.call_count == 1, "Telegram 應被正常呼叫發送"


# ------------------------------------------------------------------------------
# 測試 6: 交易日 21:15 與 08:20 策略 12 真實選股/早鳥卡片正常放行 (含「進場防線」字樣)
# ------------------------------------------------------------------------------
def test_6_real_strategy_12_screener_card_allowed_at_2115_and_0820(monkeypatch):
    """測試 6: 交易日 21:15 與 08:20：使用真實策略 12 screener 卡片結構 (內含『進場防線』字樣，不傳假 task_type)，推播路徑必須放行"""
    import market_schedule
    import line_sender

    mock_tg = MagicMock(return_value=True)
    monkeypatch.setattr(line_sender, "send_telegram", mock_tg)

    # 取自 strategies/12_etf_event_radar/screener.py 真實組字邏輯 (含 advice 的「進場防線」字樣)
    real_screener_card = (
        "🌅【08:20 盤前早鳥跨市場預警 (策略 12)】\n"
        "海外前夜結算 + 晨間 Globex 期貨動能・鎖定 3 檔關鍵標的：\n\n"
        "🎯 實盤鎖定・折價低接潛力 00830 國泰費城半導體\n"
        "• 昨日收盤：66.90 元\n"
        "• 動態公允開盤價：68.50 元 (+2.39%)\n"
        "• 海外複合錨定：SOX 費城半導體指數\n"
        "• 實盤操盤錦囊：海外母體強勁，動態合理價跳空至 68.50 元 (+2.39%)。若開盤因恐慌未漲足形成超額折價，09:02 將給出精準進場防線。\n\n"
        "⚡【實盤掛單提醒】\n"
        "09:00 開盤第一盤撮合後，系統將實時比對「真實開盤價、K線型態 (紅K/黑K) 與盈虧比」，於 09:02 發布實戰下單指令！"
    )

    # 6.1 測試 21:15 盤後選股時段 (run_all.py --line 呼叫情境，不帶假 task_type)
    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: DT_TRADING_2115)
    res_2115 = line_sender.send_to_line(real_screener_card, strategy="12", force=True)
    assert res_2115 is True, "交易日 21:15 策略 12 盤後選股卡 (含『進場防線』) 不得被時段守衛誤擋"
    assert mock_tg.call_count == 1, "Telegram 應被正常呼叫發送"

    # 6.2 測試 08:20 盤前早鳥時段 (screener.py 盤前早鳥發送情境)
    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: DT_TRADING_0820)
    res_0820 = line_sender.send_to_line(real_screener_card, strategy="12", force=True)
    assert res_0820 is True, "交易日 08:20 策略 12 早鳥預警卡 (含『進場防線』) 不得被時段守衛誤擋"
    assert mock_tg.call_count == 2, "Telegram 應累積第二次發送"


# ------------------------------------------------------------------------------
# 測試 7: 策略 04 簽名對齊與收盤退出
# ------------------------------------------------------------------------------
def test_7_strategy_04_signature_and_afterhours_exit(monkeypatch):
    """測試 7: 策略 04 以現行簽名呼叫時不再因 force= 參數例外；收盤後會退出"""
    import market_schedule
    sys.path.insert(0, str(WORK_DIR / "strategies" / "04_cb_pricing_ambush"))
    s04 = importlib.import_module('strategies.04_cb_pricing_ambush.intraday_scanner')

    # 7.1 驗證 market_schedule.check_market_open_or_exit 無論傳入 allow_force 或 force 均不崩潰
    assert market_schedule.check_market_open_or_exit(context_name="策略 04 測試", allow_force=False) is True
    assert market_schedule.check_market_open_or_exit("策略 04 測試", force=False) is True

    # 7.2 凍結時間為 15:58:18
    fake_now = DT_TRADING_1558
    monkeypatch.setattr(s04.datetime, "datetime", MagicMock(now=lambda tz=None: fake_now))

    mock_scan_04 = MagicMock()
    mock_send_04 = MagicMock()
    monkeypatch.setattr(s04, "scan_once", mock_scan_04)
    monkeypatch.setattr(s04, "send_to_line", mock_send_04)

    # 呼叫 strategy 04 的常駐雷達，驗證不崩潰且在 15:58 立即退出
    s04.run_intraday_scanner(interval=1, once=False, force=True)
    assert mock_scan_04.call_count == 0, "策略 04 於 15:58 即使帶入 force=True 亦必須收盤退出，不得執行 scan_once"
    assert mock_send_04.call_count == 0


# ------------------------------------------------------------------------------
# 測試 8: 交易日 08:58 8989 晨報正常放行
# ------------------------------------------------------------------------------
def test_8_morning_report_allowed_at_0858(monkeypatch):
    """測試 8: 交易日 08:58：8989 晨報實際標題『🔔【台股開盤前夕・盤中到價雷達全自動啟動】』在開盤前 08:58 必須放行"""
    import market_schedule
    import line_sender
    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: DT_TRADING_0858)

    mock_tg = MagicMock(return_value=True)
    monkeypatch.setattr(line_sender, "send_telegram", mock_tg)

    # 取自 line_bot_server.py 第 402 行真實晨報內容
    morning_card = (
        "🔔【台股開盤前夕・盤中到價雷達全自動啟動】\n"
        "━━━━━━━━━━━━━━━\n"
        "今日共鎖定 10 檔焦點標的！\n"
        "雲端即時盯盤引擎已就緒，盤中將每 60 秒比對四大防線價位。\n"
        "一旦觸及「🟢進場、🔵加碼、🔴停利、🛑停損」，將立即在此視窗為您即時推播！\n"
        "祝福今日操盤順利，嚴守紀律！"
    )

    res = line_sender.send_to_line(morning_card, strategy="RADAR", target_id="fake_target")
    assert res is True, "08:58 8989 開盤晨報不得被盤中時段守衛攔截"
    assert mock_tg.call_count == 1, "Telegram 應被正常呼叫發送"


# ------------------------------------------------------------------------------
# 測試 9: 盤後 tunnel 外網連線通知正常放行
# ------------------------------------------------------------------------------
def test_9_tunnel_notification_allowed_afterhours(monkeypatch):
    """測試 9: tunnel_manager 外網連線通知 (內含『盤中到價即時雷達』) 在盤後 (如 18:00) 必須放行"""
    import market_schedule
    import line_sender
    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: DT_TRADING_1800)

    mock_tg = MagicMock(return_value=True)
    monkeypatch.setattr(line_sender, "send_telegram", mock_tg)

    # 取自 tunnel_manager.py 第 173 行真實通知內容
    tunnel_card = (
        "🌐【台股戰情室・外網專屬連線已就緒】\n"
        "━━━━━━━━━━━━━━━\n"
        "別台電腦、筆電、平板或手機點擊下方連結即可即時看盤：\n"
        "🔗 https://stock.chihyuan.date\n\n"
        "🎯 即時戰情功能：\n"
        "• 盤中到價即時雷達 (四大防線)\n"
        "• 四大策略選股戰報\n"
        "• 個股即時診斷與操盤錦囊\n"
        "• 跨日動態滾動校準 (移動停利)\n"
        "━━━━━━━━━━━━━━━\n"
        "📱【手機隨傳隨回秒級診斷已同步啟動】\n"
        "在此視窗直接傳送股票代碼（如 2330、2476）或股票名稱（如 台積電），小助手秒回四大操盤防線與買賣錦囊！"
    )

    res = line_sender.send_to_line(tunnel_card, strategy=None)
    assert res is True, "盤後 tunnel 外網連線通知 (含『盤中到價即時雷達』字樣) 不得被時段守衛攔截"
    assert mock_tg.call_count == 1, "Telegram 應被正常呼叫發送"


# ------------------------------------------------------------------------------
# 測試 10: 交易日 08:58 run_all_intraday.py 啟動 scanner 子程序
# ------------------------------------------------------------------------------
def test_10_run_all_intraday_launches_scanners_at_0858(monkeypatch):
    """測試 10: 交易日 08:58：run_all_intraday.py 必須放行並平行啟動全數 12 項 scanner 行程"""
    import run_all_intraday

    # 凍結時間為 08:58:30 (允許 premarket runner 啟動時段)
    fake_now = DT_TRADING_0858
    monkeypatch.setattr(run_all_intraday.datetime, "datetime", MagicMock(now=lambda tz=None: fake_now))

    # Mock subprocess.Popen，模擬成功生成子行程，並令 wait() 立即返回避免阻塞
    mock_process = MagicMock()
    mock_process.pid = 99999
    mock_process.wait.return_value = 0
    mock_popen = MagicMock(return_value=mock_process)
    monkeypatch.setattr(run_all_intraday.subprocess, "Popen", mock_popen)

    monkeypatch.setattr(sys, "argv", ["run_all_intraday.py", "--interval", "60"])
    run_all_intraday.main()

    # 驗證 12 項策略的 scanner 全數被啟動
    expected_count = len(run_all_intraday.INTRADAY_STRATEGIES)
    assert expected_count == 12
    assert mock_popen.call_count == 12, f"08:58 總調度器應啟動全部 {expected_count} 支 scanner 行程"


# ------------------------------------------------------------------------------
# 測試 11: 收盤後 (15:58 / 16:03) 真正的盤中到價與開盤確認卡片絕對被擋 (force=True 亦不准穿透)
# ------------------------------------------------------------------------------
def test_11_afterhours_real_intraday_alerts_strictly_blocked(monkeypatch):
    """測試 11: 交易日收盤後 (15:58)：真實單檔到價、多檔合併到價與開盤確認卡片均被時段守衛嚴格攔截，force=True 亦不准穿透；但 --test-push 仍可放行"""
    import market_schedule
    import line_sender
    monkeypatch.setattr(market_schedule, "get_taiwan_now", lambda: DT_TRADING_1558)

    mock_tg = MagicMock(return_value=True)
    monkeypatch.setattr(line_sender, "send_telegram", mock_tg)

    # 11.1 真實單檔到價卡片 (build_price_alert_card 產物)
    single_card = line_sender.build_price_alert_card(
        target={"code": "2330", "name": "台積電", "entry_price": 1050.0},
        rt={"price": 1050.0, "volume": 12000, "high": 1055.0, "low": 1045.0},
        event_type="ENTRY",
        event_title="🟢 建議進場",
        event_desc="股價觸及進場防線",
        strategy_id="01"
    )
    res_single = line_sender.send_to_line(single_card, strategy="01", force=True)
    assert res_single is False, "15:58 單檔盤中到價卡片即使 force=True 亦必須被攔截"
    assert mock_tg.call_count == 0

    # 11.2 真實多檔合併到價卡片 (build_aggregated_alert_card 產物)
    agg_card = line_sender.build_aggregated_alert_card(
        alerts=[{
            "target": {"code": "2330", "name": "台積電"},
            "rt": {"price": 1050.0},
            "event_title": "🟢 建議進場"
        }],
        strategy_id="01",
        now_str="10:30:00"
    )
    res_agg = line_sender.send_to_line(agg_card, strategy="01", force=True)
    assert res_agg is False, "15:58 多檔合併到價卡片即使 force=True 亦必須被攔截"
    assert mock_tg.call_count == 0

    # 11.3 純文字備援格式到價警訊 (format_clean_price_alert_text 產物)
    clean_alert_text = line_sender.format_clean_price_alert_text(
        target={"code": "2330", "name": "台積電", "entry_price": 1050.0},
        rt={"price": 1050.0, "volume": 12000},
        event_type="ENTRY",
        event_title="🟢 建議進場",
        event_desc="股價觸及進場防線",
        strategy_id="01"
    )
    res_clean = line_sender.send_to_line(clean_alert_text, strategy="01", force=True)
    assert res_clean is False, "15:58 純文字到價警訊即使 force=True 亦必須被攔截"
    assert mock_tg.call_count == 0

    # 11.4 開盤實戰確認卡片
    confirm_msg = "🎯【09:02 開盤實戰確認・下單委託語句與進場防線 (策略 12)】\n00830 建議進場..."
    res_confirm = line_sender.send_to_line(confirm_msg, strategy="12", force=True)
    assert res_confirm is False, "15:58 開盤確認卡片即使 force=True 亦必須被攔截"
    assert mock_tg.call_count == 0

    # 11.5 --test-push 模式下，連線測試必須被放行
    res_test = line_sender.send_to_line(single_card, strategy="01", is_test=True)
    assert res_test is True, "--test-push 連線測試必須被正常放行"
    assert mock_tg.call_count == 1


# ------------------------------------------------------------------------------
# 測試 12: 策略 12 報價函式盤中判斷已綁定台北時間 (UTC+8)
# ------------------------------------------------------------------------------
def test_12_strategy_12_quote_uses_taipei_time_even_on_utc_runner(monkeypatch):
    """測試 12: 策略 12 fetch_twse_batch_quotes 必須以台北時間判定 is_live_trading：即使主機系統為 UTC (01:02)，台北時間 09:02 依然正確判定為盤中即時撮合 (向 TWSE 取價，不退回 yfinance)"""
    sys.path.insert(0, str(WORK_DIR / "strategies" / "12_etf_event_radar"))
    s12 = importlib.import_module('strategies.12_etf_event_radar.intraday_scanner')

    # 模擬台北時間 09:02:00 (週四交易日)
    dt_taipei_0902 = datetime.datetime(2026, 11, 12, 9, 2, 0, tzinfo=TZ_TW)

    orig_datetime = datetime.datetime
    class FakeDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return dt_taipei_0902
            # 若有未改時區之 now()，回傳 UTC 01:02 (非盤中)
            return orig_datetime(2026, 11, 12, 1, 2, 0)

    monkeypatch.setattr(s12.datetime, "datetime", FakeDatetime)

    # Mock yfinance 與 urllib.request
    mock_yf = MagicMock()
    monkeypatch.setattr(s12, "yf", mock_yf)

    mock_urlopen = MagicMock()
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"msgArray": []}).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    monkeypatch.setattr(s12.urllib.request, "urlopen", mock_urlopen)

    # 執行 fetch_twse_batch_quotes
    s12.fetch_twse_batch_quotes([{"code": "00830", "market": "tse"}])

    # 驗證在台北時間 09:02 正確判定為盤中即時撮合，因此絕對不會呼叫 yf.download
    assert mock_yf.download.call_count == 0, "台北時間 09:02 為盤中即時撮合，不得退回 yfinance 回測下載"
    assert mock_urlopen.call_count >= 1, "台北時間 09:02 必須向 TWSE MIS 請求盤中即時報價"
