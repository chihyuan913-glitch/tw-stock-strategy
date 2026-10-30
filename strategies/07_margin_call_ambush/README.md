# 策略 07：高融資使用率×低維持率斷頭伏擊與浮額清洗策略 (Margin Call Ambush Screener V2.5 旗艦合規升級版)

> 本策略為「台股量化選股策略最高總指揮中心」旗下旗艦級多空雙向對沖策略，鎖定市場最脆弱的融資槓桿部位，透過「官方融資流水帳成本遞推引擎」、「130% 追繳臨界價精算模型」、「全域自適應位階引擎」、「臺灣證交所法定檔位方向性合規取整」、「健康盈虧比強制檢核」與「雙向狀態機」，精準捕捉融資多殺多與籌碼換手後的暴力轉折。

---

## 📖 一、核心策略哲學 (Why)

### 1. 散戶槓桿的致命弱點
在台股市場中，融資戶通常為市場追高殺低的散戶群體。融資交易受限於臺灣法令之 **130% 擔保維持率** 線：
- **當股價持續破底**：維持率快速跌破 140% 進入追繳射程圈。若法人同步提款出貨，融資戶無力補錢將引發券商市價「強制斷頭萬箭齊發」，造成流動性真空與多殺多助跌效應。
- **當浮額清洗告終**：當融資連續大幅退場、浮額徹底洗淨，同時三大法人於下影線逆勢巨量吃貨接走血性籌碼時，代表沉重賣壓消散，籌碼轉入主力巨鯨手中，極易展開 V 型暴力反彈。

### 2. 雙向狀態機精準分流
本策略不盲目做多或做空，而是依據「融資餘額增減速度 × 三大法人動向 × K線型態」將標的自動歸入兩大戰術陣列：
- 🔴 **【斷頭助跌・放空獵殺 (SHORT_BREAKDOWN)】**：股價在月線下、法人棄守、融資死抱未退者，順勢放空或利用個股期貨獵殺。
- 🟢 **【浮額清洗・逆勢抄底 (LONG_REVERSAL)】**：融資急速暴減（斷頭已發生）、法人大舉低接、收長下影線止跌者，右側逆勢抄底佈局。

---

## 🧮 二、官方融資流水帳成本與 130% 追繳梯隊模型

本策略連線臺灣證券交易所 (TWSE) `MI_MARGN` 與櫃買中心 (TPEx)，取得每日買進、賣出、現金償還與餘額流水帳，逐日動態遞推加權融資持倉均價 $\bar{C}$：

$$
\text{保留部位 } \text{rem} = \max(\text{前日餘額} - \text{今日賣出} - \text{今日現償}, 0)
$$

$$
\bar{C}_{t} = \frac{\text{rem} \times \bar{C}_{t-1} + \text{今日買進張數} \times \text{當日典型價}}{\text{rem} + \text{今日買進張數}}
$$

依據法定 6 成融資借款額度，估算當前真實融資擔保維持率 ($MR$)：

$$
MR = \frac{\text{收盤價}}{\bar{C} \times 0.6} \times 100\%
$$

### 🌟 融資 130% 追繳臨界價 (Margin Call Trigger Price) 精算：
融資借款本金以 6 成為基準，當擔保維持率跌至 130% 時，券商將於收盤正式寄發「追繳通知書」：

$$
\text{追繳臨界價} = \bar{C} \times 0.60 \times 1.30 = \bar{C} \times 0.78
$$

$$
\text{距追繳空間\%} = \frac{\text{收盤價} - \text{追繳臨界價}}{\text{收盤價}} \times 100\%
$$

- 若 $\text{距追繳空間\%} \le 0$：標註 `🚨 已深陷追繳斷頭區`，多殺多骨牌狂潮已在發生。
- 若 $0 < \text{距追繳空間\%} \le 5.0\%$：標註 `⚠️ 追繳高危臨界 (<5%)`，空方放空獵殺最佳肉量區。
- 若 $5.0\% < \text{距追繳空間\%} \le 10.0\%$：標註 `🟡 逼近追繳警戒 (5-10%)`。

---

## 🎯 三、4+1 五維度硬性濾網 (Hard Filters $\le$ 5)

本策略恪守《總指揮中心規範》，硬性濾網不超過 5 個，杜絕過度擬合：
1. **流動性防守**：近 5 日平均成交量 $\ge 1,000$ 張，排除處置、全額交割、DR 與 ETF（限定 4 碼普通股）。
2. **槓桿高危線 (Tier-1)**：融資使用率 $\ge 25.0\%$ 且 融資維持率 $\le 142.0\%$ 且 融資餘額 $\ge 3,000$ 張。
3. **雙向狀態機**：必須命中 `SHORT_BREAKDOWN` 或 `LONG_REVERSAL`。
4. **型態防守面**：
   - 空方獵殺：收盤價 $<$ 20MA（月線下空頭排列）。
   - 多方清洗：當日收紅（$\text{Close} \ge \text{Open}$）或 下影線佔比 $\ge 30\%$ 具備實質止跌結構。
5. **空間防追高**：月線乖離率絕對值 $|\text{Bias20}| \le 10.0\%$，防過度乖離反撲。

---

## 🛡️ 四、雙層門檻動態降級容錯機制 (Fallback Mechanism)

為避免盤面全面大漲導致無任何標的命中 Tier-1 絕對門檻，系統自動啟動 Tier-2 降級機制：
- **🎯 Tier-1 絕對斷頭高危**：使用率 $\ge 25\%$、維持率 $\le 142\%$、餘額 $\ge 3,000$ 張。
- **⚠️ Tier-2 相對高危候補**：放寬至 5日均量 $\ge 800$ 張、使用率 $\ge 18\%$、維持率 $\le 165\%$、餘額 $\ge 1,500$ 張。
  - 自動啟用強制分流狀態機 (`force_classify=True`)。
  - 依融資維持率由低至高排序（維持率越低越脆弱），精選前 6 檔標記為候選池。

---

## ⚡ 五、操盤實戰四大防線價位與法定檔位取整 (Four-Price Standard V4.0)

全策略全面遵循總指揮中心第 7 條規範，所有價位**100% 嚴格合規於臺灣證交所 6 階法定升降檔位**（<10: 0.01, 10~50: 0.05, 50~100: 0.10, 100~500: 0.50, 500~1000: 1.00, >=1000: 5.00），並落實方向性取整與盈虧比檢核（$RR \ge 1.5$）：

### 🔴 空方斷頭獵殺 (`SHORT_BREAKDOWN`)
- 🟢 **進場價位 (Entry)**：收盤價向下取整合規檔位（空單建倉上限）
- 🔵 **加碼價位 (Add-on)**：$\min(\text{Low} \times 0.995, \text{Close} \times 0.975)$ 向下取整合規檔位（破底加空追擊）
- 🔴 **停利目標 (TP1 / TP2)**：TP1 $\text{Close} \times 0.92$ (-8%) ｜ TP2 $\text{Close} \times 0.85$ (-15%)，向下取整以利撮合
- 🛑 **嚴格停損 (Stop-Loss)**：$\max(\min(\text{MA20} \times 1.01, \text{Close} \times 1.035), \text{Close} \times 1.025)$ 向上取整合規檔位（突破月線嚴格回補）
- 幾何單調性：$\text{TP2} \le \text{TP1} < \text{Add-on} < \text{Entry} < \text{Stop-Loss}$
- 盈虧比：$RR = \frac{\text{Entry} - \text{TP1}}{\text{Stop-Loss} - \text{Entry}} \ge 1.5$

### 🟢 多方浮額清洗 (`LONG_REVERSAL`)
- 🟢 **進場價位 (Entry)**：收盤價向上取整合規檔位（分批多方買進上限）
- 🔵 **加碼價位 (Add-on)**：$\max(\text{High} \times 1.01, \text{Close} \times 1.025)$ 向上取整合規檔位（突破日高轉強加碼）
- 🔴 **停利目標 (TP1 / TP2)**：TP1 $\max(\text{MA20}, \text{Close} \times 1.07)$ (+7%) ｜ TP2 $\text{Close} \times 1.14$ (+14%)，向下取整以利撮合
- 🛑 **嚴格停損 (Stop-Loss)**：$\min(\text{Low} \times 0.985, \text{Close} \times 0.96)$ 向下取整合規檔位（破止跌低點無條件停損）
- 幾何單調性：$\text{Stop-Loss} < \text{Entry} < \text{Add-on} < \text{TP1} \le \text{TP2}$
- 盈虧比：$RR = \frac{\text{TP1} - \text{Entry}}{\text{Entry} - \text{Stop-Loss}} \ge 1.5$

---

## 🚨 六、盤中即時雷達與 130% 斷頭追繳警報 (Intraday Scanner)

盤中雷達 (`intraday_scanner.py`) 整合 `algo_guards` 之 `GLOBAL_TICK_GUARD` 防毛刺雙重確認機制，完整載入 `result.csv` 之四大防線與 130% 追繳門檻，支援六大到價事件：
1. 🛑 `STOP_LOSS`：突破/跌破停損線，無條件強制離場（最高優先級）。
2. 🎯 `TP2`：波段獲利滿足點，全數獲利了結。
3. 💰 `TP1`：第一目標達標，分批拔檔 1/2。
4. 🚨 `MARGIN_CALL`：現價摜破 130% 追繳警戒線，多殺多賣壓加速！
5. ⚡ `ADDON`：突破加碼價，右側動能確認追擊。
6. 🟢/🔻 `ENTRY`：落入黃金建倉區間，啟動分批佈局。

---

## ⏸️ 七、台股未開盤日全域不推送守門員 (Rule 21)

本策略全面整合根目錄之 `market_schedule.py`：
- 自動檢測週末例休、國定假日、農曆春節長假與颱風假緊急公告。
- 遇休市日，`screener.py` 與 `intraday_scanner.py` 自動靜默退出，不覆蓋既有有效報表，節省運算與通訊資源。
- 支援 `--force` 旗標於非交易日強制執行除錯與驗證。

---

## 📊 八、籌碼K線 (CMoney) 雙群組自選股同步 (Rule 22)

- 盤後選股完成後自動匯出 `籌碼K線_自選股匯入_策略07.txt`。
- 在 Windows 環境下自動將核心標的代碼寫入系統剪貼簿，打開電腦版籌碼K線按下 Ctrl+V 即可 1 秒匯入。
- 支援 `--cmoney` 參數直接連線 CMoney 雲端自選股群組自動更新。

---

## 📁 九、模組檔案結構

```text
strategies/07_margin_call_ambush/
├── screener.py          # 盤後選股器 (支援 --date, --line, --export, --force, --cmoney)
├── intraday_scanner.py  # 盤中即時雷達 (支援 --once, --interval, --test-push, --test-alert, --force)
├── run_daily.bat        # 每日盤後排程執行檔 (自動探測 runtime/python.exe)
├── start_intraday.bat   # 盤中即時雷達一鍵啟動檔
├── result.csv           # 最新選股 45 欄位報表 (UTF-8-BOM 編碼，含 盈虧比、130%追繳價)
├── result.md            # Markdown 視覺化字卡戰報
├── 籌碼K線_自選股匯入_策略07.txt # CMoney 自選股匯入檔 (Windows 剪貼簿連動)
└── README.md            # 本策略完整技術手冊
```

---

## 🚀 十、執行指令

### 1. 盤後選股
```bash
# 產出最新報表 (遇休市自動靜默防禦)
python screener.py

# 強制執行 (略過休市防禦)
python screener.py --force

# 產出報表並推播至 LINE 策略 07 專屬群組
python screener.py --line --force

# 產出報表並同步至 CMoney 籌碼K線雲端自選股
python screener.py --cmoney --force

# 指定歷史日期複盤
python screener.py --date 20261016
```

### 2. 盤中即時到價雷達
```bash
# 單次快篩巡邏
python intraday_scanner.py --once --force

# 盤中 60 秒常駐輪詢盯盤
python intraday_scanner.py --interval 60

# 測試特定到價警報 (例如融資追繳 MARGIN_CALL)
python intraday_scanner.py --test-alert MARGIN_CALL

# 測試 LINE 到價推播連線
python intraday_scanner.py --test-push
```
