# 台股選股策略 總指揮中心規範 (Master Command Rules)

本工作區根目錄（`g:\我的雲端硬碟\台股選股策略`）及其主對話視窗為**「台股量化選股策略最高總指揮中心」**。
在此所下達之所有架構指令、選股邏輯規範、程式碼標準與維運原則，**所有向下子資料夾、子模組及衍生策略一律全面適用並強制繼承**。

---

## 一、最高指揮原則 (Supreme Directive)
1. **全域覆蓋與繼承**：
   - 總指揮中心所頒布之規則為最高指導原則。
   - 根目錄及所有子資料夾（現有及未來新增模組）之程式碼、設定檔、策略腳本與文件，均必須無條件遵循本規範。
2. **純粹化 Python 自動化閉環體系 (解除 XQ / TradingView 交付義務)**：
   - **核心範疇**：全面專注於 **Python 量化自動化引擎**，貫通「盤後選股 ＋ 盤中雷達 ＋ LINE 即時戰報 ＋ Excel 報表 ＋ 自動化排程」。
   - **XQ 全球贏家與 TradingView 指標**：正式**不再列入**開發與維護範圍（歷史已產出之 `.xs` 與 `.pine` 檔案統一封存於各策略之 `legacy/` 目錄，僅供歷史備查，不再同步更新或要求交付）。
3. **策略獨立子資料夾規範**：
   - 任何現有及未來新增之策略，**一律正式歸納進獨立子資料夾**（路徑統一設為 `strategies/<序號>_<策略英文名稱>/`）。
   - 根目錄維持乾淨，僅保留共用底層模組（`line_sender.py`、環境變數 `.env`、總調度器 `run_all.py` / `run_all.bat`、排程腳本 `setup_task_scheduler.ps1` 及全域說明文件）。

---

## 二、子資料夾策略模組架構標準
所有位於 `strategies/` 下的各策略模組，均必須遵循以下標準檔案結構與命名：

```text
strategies/<strategy_folder>/
├── screener.py          # 盤後選股器 (標準命名，支援 --line 與 --export)
├── intraday_scanner.py  # 盤中即時雷達 (若具備盤中監控需求時提供)
├── run_daily.bat        # 該策略專屬每日手動/排程執行批次檔
├── start_intraday.bat   # 該策略專屬盤中即時雷達啟動檔 (若適用)
├── result.csv           # 最新選股報表 (UTF-8-BOM 編碼，Excel 開啟不亂碼)
├── result.md            # Markdown 格式戰報 (選用)
├── README.md            # 該策略之核心選股邏輯、進出場濾網與停損停利風控手冊
└── legacy/              # 歷史 XQ XS 腳本與 TradingView 指標封存 (不主動維護)
```

### 共用模組引用規範：
各策略之 Python 程式必須於開頭將專案根目錄加入 `sys.path`，確保無縫引用根目錄之 `line_sender.py` 與 `.env`：
```python
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
```

---

## 三、LINE 推播獨立視窗分流規範 (Multi-Channel Visual Isolation)
1. **視窗隔離原則 (防呆防混淆)**：
   - 為徹底杜絕多空訊號混淆，各策略在 LINE 推播時，**強制支援定向投遞至各自專屬的 LINE 視窗/群組**。
   - 策略一 (布林超跌抄底)、策略二 (法人起漲動能)、策略三 (股期做空避險) 擁有各自獨立的聊天群組。
2. **調用標準**：
   - 各模組調用 `send_to_line` 時，必須傳遞自身策略編號：
     `send_to_line(message, strategy="01")`
3. **高可用 Fallback 機制**：
   - 若 `.env` 未設定該策略之專屬 Group ID（`LINE_TARGET_STRATEGY_XX`），系統自動安全退回使用全域 `LINE_USER_ID`，確保訊息絕對不丟失。

---

## 四、量化篩選與資料抓取通用準則
1. **數據獲取可靠性**：
   - 優先調用免費公開之證交所/櫃買中心官方 API 與 Yahoo Finance，需具備隨機延遲（Anti-Scraping / Rate Limit 防護）與多層重試機制。
2. **流動性與防詐濾網**：
   - 除非特定極短線特殊策略另有明定，所有個股策略預設需通過**流動性防守**（如當日成交量 $\ge 1,000$ 張，防流動性陷阱）。
   - 排除處置股、全額交割股、DR 股與流動性枯竭標的。
3. **進出場風控與個股推播四大價位強制規範 (Four-Price Standard)**：
   - 所有個股推播（盤後選股報表與盤中雷達警示），**一律強制包含以下四大明確價位**：
     * **🟢 進場價位 (Entry Price)**：精準明確之建倉基準價格。
     * **🔵 加碼價位 (Add-on Price)**：突破關鍵K棒高點或突破防線之續強確認加碼價。
     * **🔴 停利價位 (Take-Profit Price)**：TP1 (第一目標) 與 TP2 (第二目標) 反壓區獲利了結價。
     * **🛑 停損價位 (Stop-Loss Price)**：破線或回測失敗之無條件防守撤退價位。
   - 嚴格要求停損防守位（跌破關鍵K棒低點、跌破進場成本 3%~5% 或跌破月線）。
   - 盈虧比評估（Risk/Reward Ratio）必須合理，嚴禁追高無肉或空間不足之危險交易。

---

## 五、指令執行與維護規範
- 總指揮下達全域更新或新策略需求時，AI 助手需主動審視並嚴格依照本規範於 `strategies/` 下建置或維護。
- 重大更動與版本演進需明確記錄於該策略之 `README.md`。
