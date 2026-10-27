# 台股量化選股策略最高總指揮中心 - 進度交接紀錄 (handoff.md)

## 📌 當前狀態 (Current Status)
- **更新時間**：2026-10-27 23:35 (UTC+8)
- **執行環境**：Windows (DESKTOP-OI789R1)
- **系統版本**：v5.0.0 (量化實驗室實盤雙向融合・策略條件全面優化・全新策略10投信認養飆股上線)
- **Git Push 狀態**：已成功推送至 GitHub (Commit: 149d8f7)
- **核心就緒狀態**：
  - [x] **量化實驗室 (Quant Lab) 旗艦升級與真實市場數據對接**：
    - `lab/data/loader.py` 實作真實 yfinance 歷史 OHLCV 自動同步至 SQLite `tw_market.db`，擺脫純模擬數據限制。
    - 實作完整多策略資金配置演算法 (`PortfolioAllocator`: Sharpe Weighted, Inverse Volatility, Risk Parity, Equal Weight)。
    - 實作跨策略共振雷達與自動回測體檢引擎 (`run_lab.py --daily-advisor`)，產出明日 T+1 決策指引清單 (`tomorrow_trading_advisor.csv`)。
  - [x] **選股策略條件重塑與減法整併**：
    - **策略 02 法人起漲動能**：原生內嵌股票期貨（252 檔個股期合約、價差、1口保證金、OI、期貨四大防線與結算提醒），消除與策略 05 的重複選股與推播。
    - **策略 06 爆量突破**：硬性過濾外資與投信淨賣超 (`(f_lots + t_lots) >= 0`)，強制實體紅 K，杜絕散戶接刀與隔日沖倒貨陷阱。
    - **策略 04 可轉債伏擊**：嚴格限制轉換溢價率上限 $\le 15\%$，排除高估值與債轉股套利賣壓標的。
    - **策略 01 布林超跌反轉**：連鎖大盤位階，空頭與震盪防禦期自動收緊下軌至 $+0.5\%$ 並強制要求實質止跌 K 線。
  - [x] **全新高 Alpha 策略正式引進**：
    - **策略 10：台股投信認養集中度爆發飆股策略 (`10_investment_trust_sprint`)**：
      - 4+1 維度硬篩：5日均量 $\ge 1,000$ 張、站穩 20MA 走揚、投信連買 $\ge 3$ 天且 5 日累計 $\ge 800$ 張、實體紅 K、月線正乖離 $\le 6.5\%$。
      - 雙層 Tier-1 認養精選與 Tier-2 點火候補動態降級架構。
      - 配備完整 `screener.py`、`intraday_scanner.py`、`run_daily.bat`、`start_intraday.bat`、`README.md`、`result.csv`。
  - [x] **儀表板與調度器全域支援**：
    - 主戰情儀表板 [`web/index.html`](file:///g:/我的雲端硬碟/台股選股策略/web/index.html) 新增「策略 10：投信認養飆股」按鈕、切換狀態機與「🏆 投信認養集中度指標卡」。
    - 後端伺服器 [`line_bot_server.py`](file:///g:/我的雲端硬碟/台股選股策略/line_bot_server.py) `/api/overview` 與 `/api/strategy/10_investment_trust_sprint` API 支援。
    - 量化實驗室前端 [`lab/web/templates/lab.html`](file:///g:/我的雲端硬碟/台股選股策略/lab/web/templates/lab.html) 與適配器 [`lab/strategies/adapters.py`](file:///g:/我的雲端硬碟/台股選股策略/lab/strategies/adapters.py) 正式註冊策略 10。
    - 總調度器 [`run_all.py`](file:///g:/我的雲端硬碟/台股選股策略/run_all.py) 與 [`run_all_intraday.py`](file:///g:/我的雲端硬碟/台股選股策略/run_all_intraday.py) 完成登記。
  - [x] **自動化測試 100% 通過**：
    - `tests/test_lab_advanced.py` 7 項整合測試合約全數通過。
    - 工作區 `__pycache__` 全數安全清除。

---

## 🎯 下一步目標 (Next Steps)
1. 觀察週一盤中「策略 10：投信認養集中度爆發飆股」盤中到價雷達運作狀況。
2. 評估是否將更多策略回測資料預先寫入本地快取以進一步提升每日體檢計算速度。
