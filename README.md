# 台股量化選股策略 最高總指揮中心 (Master Command Center)

本專案為台股全方位量化選股與即時雷達監控系統，貫通**「盤後自動選股 ＋ 盤中即時雷達 ＋ LINE 手機戰報 ＋ Excel 報表 ＋ 雲端/本機定時排程」**之完整自動化閉環。

---

## 策略模組矩陣 (Strategies Matrix)

所有策略模組均獨立收納於 `strategies/` 子目錄內，權責解耦、結構標準化：

| 編號 | 策略模組目錄 | 策略名稱與方向 | 核心選股與風控特色 | 快速執行檔 |
| :---: | :--- | :--- | :--- | :--- |
| **01** | [`01_bollinger_reversal`](file:///g:/我的雲端硬碟/台股選股策略/strategies/01_bollinger_reversal/) | **布林下軌超跌反轉 (V2.0)**<br>*(抄底多方)* | • 觸及布林下軌 (-5%~+1%)<br>• 止跌K棒 (長下影線 $\ge 25\%$ 或收紅)<br>• 法人逆勢買超佔比 $\ge 1.5\%$<br>• 距 20MA 反彈空間 $\ge 2\%$<br>• 智慧評分與星級標籤 (★★★~★★★★★) | `run_daily.bat`<br>`start_intraday.bat` |
| **02** | [`02_institutional_momentum`](file:///g:/我的雲端硬碟/台股選股策略/strategies/02_institutional_momentum/) | **法人籌碼集中起漲**<br>*(順勢多方)* | • 外資/投信 3 日買超 $> 1000$ 張<br>• 法人買超佔比 $> 10\%$<br>• 股價站上 20MA 且月線走揚<br>• 正乖離 $< 8\%$ 起漲安全區防追高<br>• 提供操盤四大價位 (進場/加碼/停利/停損) | `run_daily.bat`<br>`start_intraday.bat` |
| **03** | [`03_short_momentum`](file:///g:/我的雲端硬碟/台股選股策略/strategies/03_short_momentum/) | **法人出貨破線做空與股期避險**<br>*(避險空方)* | • **全面鎖定期交所股票期貨標的** (免借券、無融券限額、期交稅僅 0.002%)<br>• 外資/投信 3 日大賣 $> 1000$ 張<br>• 跌破 20MA 且月線下彎助跌<br>• 負乖離 $[-8\% \sim 0\%]$ 起跌破線區防軋空<br>• 提供放空操盤四價位與保證金試算 | `run_daily.bat` |

---

## 專案目錄結構

```text
g:\我的雲端硬碟\台股選股策略\
├── GEMINI.md                        # 總指揮中心規範 (最高指導原則)
├── AGENTS.md                        # 代理人規則入口
├── README.md                        # 總指揮中心戰情手冊 (本文件)
├── run_all.py                       # 【核心】全策略一鍵依序執行引擎
├── run_all.bat                      # 【核心】Windows 雙擊一鍵執行所有策略並推播 LINE
├── line_sender.py                   # 共用 LINE Messaging API 推播模組
├── setup_task_scheduler.ps1         # Windows 本機工作排程器自動註冊腳本
├── bookmarks_tw_stock.html          # 台股即時盤勢常用網站書籤
├── requirements.txt                 # Python 依賴清單
├── .env                             # LINE Token 與 User ID 金鑰
├── .github/workflows/               # GitHub Actions 雲端定時排程工作流
│   ├── daily_master_runner.yml      # 全策略每日盤後總巡邏排程 (17:35)
│   ├── daily_screener.yml           # 策略一盤後選股排程
│   ├── intraday_radar.yml           # 策略一盤中即時雷達排程
│   ├── daily_momentum_screener.yml  # 策略二盤後選股排程
│   └── intraday_momentum_radar.yml  # 策略二盤中即時雷達排程
└── strategies/                      # 策略模組庫
    ├── 01_bollinger_reversal/       # 策略一：布林下軌超跌反轉
    ├── 02_institutional_momentum/   # 策略二：法人籌碼集中起漲
    └── 03_short_momentum/           # 策略三：法人出貨破線做空 (股票期貨)
```

---

## 快速使用指南

### 1. 一鍵執行所有策略 (推薦日常使用)
雙擊根目錄的 **`run_all.bat`** 或在終端機執行：
```powershell
python run_all.py --line
```
系統將依序執行三大策略、產出各自的 Excel CSV 報表，並彙整戰報推播至您的 LINE。

### 2. 單獨執行特定策略
進入各策略目錄執行對應程式或批次檔：
```powershell
# 例如單獨執行策略一 (布林超跌反轉)
cd strategies/01_bollinger_reversal
python screener.py --line --export result.csv

# 例如單獨執行策略二 (法人起漲)
cd strategies/02_institutional_momentum
python screener.py --line --export result.csv

# 例如單獨執行策略三 (做空避險)
cd strategies/03_short_momentum
python screener.py --line --export result.csv
```

### 3. 盤中即時巡邏雷達
- **策略一盤中雷達**：雙擊 `strategies/01_bollinger_reversal/start_intraday.bat`
- **策略二盤中雷達**：雙擊 `strategies/02_institutional_momentum/start_intraday.bat`
- 盤中（09:00~13:35）每隔 60~180 秒即時向證交所詢價，符合成交量與進場訊號立即發送 LINE 即時警示。

### 4. 設定 Windows 工作排程器 (無人值守)
以管理員身分開啟 PowerShell 執行：
```powershell
powershell -ExecutionPolicy Bypass -File setup_task_scheduler.ps1
```
即可自動註冊：
1. **每日盤後 17:35** 自動執行 `run_all.bat` 彙總全日選股報表並發送 LINE。
2. **每日開盤 08:55** 自動啟動盤中即時雷達。

### 5. LINE 獨立視窗分流設定 (方案 A：各策略專屬群組)
為了避免多空策略訊號混淆在同一個聊天視窗，系統支援將三大策略分流至各自的 LINE 群組：
1. 在 LINE App 內建立 3 個專屬群組（如「01-超跌抄底」、「02-法人起漲」、「03-做空避險」），並邀請您的 Bot 進群。
2. 執行本機輔助工具 `python get_group_id.py` 獲取各群組的 Group ID (以 C 開頭的 33 字元)。
3. 在 `.env` 中設定專屬目標：
   ```env
   LINE_TARGET_STRATEGY_01=Cxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  # 策略一
   LINE_TARGET_STRATEGY_02=Cxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  # 策略二
   LINE_TARGET_STRATEGY_03=Cxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  # 策略三
   ```
*(若未設定，系統自動使用預設的 `LINE_USER_ID` 個人聊天室發送)*

---

## 架構原則與規範
- 本架構遵循 [GEMINI.md](file:///g:/我的雲端硬碟/台股選股策略/GEMINI.md) 總指揮中心規範。
- 全面聚焦於 Python 量化自動化閉環，XQ XS 腳本與 TradingView 指標已封存至各策略之 `legacy/` 目錄，不再要求維護。
