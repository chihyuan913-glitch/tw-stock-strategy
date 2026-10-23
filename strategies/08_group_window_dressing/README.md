# 策略 08：台股集團作帳與母子聯動伏擊策略 (Group Window Dressing Ambush Screener)

> 本策略為「台股量化選股策略最高總指揮中心」旗下旗艦級季度/年度週期性集團聯動策略，鎖定台股 25 大核心集團（如華新麗華、鴻海、聯電、國巨等），透過「全域自適應市場位階動態調校」、「作帳行事曆動態時序評估」、「母子動態價差乖離指數 (Spread Anomaly Index)」、「季末結帳出貨防禦」以及「成交額資金流向熱度精算」，精準捕捉季底拉抬與年底集團行情。

---

## 📖 一、核心策略哲學 (Why)

### 1. 台股獨特之集團交叉持股與合併報表作帳生態
在台股資本市場中，大型集團具備錯綜複雜的股權連結與轉投資架構。每逢季底（3月、6月、9月）與年底（11~12月）：
- **合併報表帳面利潤衝刺**：母公司或控股公司為拉高季報/年報投資收益與每股淨值，具備強烈動機透過旗下投資公司與主力籌碼拉抬低基期子公司股價。
- **母子聯動擴散效應**：當同集團旗艦龍頭（如華新 1605、聯電 2303、鴻海 2317）帶量站上月線表態起漲時，法人與內資大戶資金會迅速外溢擴散至集團內部其他中小型補漲股，形成「集團母子共振」。
- **結帳賣壓風險防範**：一旦時序進入季末結帳警戒期（3/23、6/23、9/23、12/23 起），或個股跌破 20MA 月線且法人大額出貨提款，集團資金抽離極易引發高檔多殺多結帳賣壓，順勢轉為空方獵殺。

### 2. 雙向狀態機精準分流
- 🟢 **【作帳點火・母子共振 (GROUP_IGNITION)】**：
  - 條件：站上 20MA 月線、法人大額買超（1日 $\ge 200$ 張 或 5日 $\ge 500$ 張且當日買超）、上影線 $< 45\%$（墓碑線硬防守）。收黑且下影線 $< 25\%$ 不再排除，改為軟評分扣 10 分（法人當日買超 $\ge 1,000$ 張者僅扣 5 分），並於戰報標註「收黑扣分」。
  - 同集團協同轉強動能家數 $\ge 2$ 檔。
- 🔴 **【集團結帳・順勢放空 (GROUP_UNWIND)】**：
  - 條件：摜破 20MA 月線、法人大額賣超（1日 $\le -300$ 張 或 5日 $\le -800$ 張）、收黑或留長上影線（$\ge 35\%$）或近 5 日跌幅 $\le -2.0\%$。

---

## 🧮 二、核心量化演算法升級模組

### 1. 全域自適應市場位階操盤引擎 (Rule 19 規範)
- 自動深度對接根目錄之 [`adaptive_regime_manager.py`](file:///g:/我的雲端硬碟/台股選股策略/adaptive_regime_manager.py)：
  - **動態乖離防追高上限 (`bias_max_pct`)**：多頭良性主升時放寬至 5.5% 享受動能；箱型震盪時緊縮為 3.5%；短線過熱或空頭時嚴格收緊至 3.0%，徹底杜絕高檔追高風險。
  - **動態四大防線停利目標 (`tp1_pct`, `tp2_pct`)**：依大盤位階動態派發波段滿足目標，多頭主升放大至 +6%~+12%，震盪箱型短打獲利 +4%~+7%。
  - **最大停損安全閥**：單筆停損幅度嚴格受限於 $-4.0\% \sim -7.0\%$ 之間，嚴禁超越量化風控 $-7.0\%$ 底線。

### 2. 母子動態價差乖離指數 (Spread Anomaly Index)
精算子公司相對於母公司近 5 日漲幅利差（$\text{Spread} = \text{Ret5d}_{\text{sub}} - \text{Ret5d}_{\text{parent}}$）：
- 🚀 **【低基期落後補漲】**：$\text{Spread} \le -3.0\%$ 且母公司突破走強。母公司拉開比價空間，子公司具備強烈均值回歸補漲動能，軟評分加 5 分，停利目標向上等幅擴展！
- ⚠️ **【正乖離過大・防母提款】**：$\text{Spread} \ge +8.0\%$ 且子公司漲幅 $\ge 10.0\%$。子公司短線飆漲脫離母體，慎防母公司高檔調節處分持股美化單季財報，軟評分扣 5 分，縮緊移動停利。
- 👑 **【動能領頭羊先行】**：$\text{Spread} \ge +2.0\%$ 且為族群最強。
- 🟢 **【母子步調一致】**：價差處於正常健康軌道。

### 3. 季末結帳出貨防禦機制 (Quarter-End Unwind Defense)
- 當行事曆處於 `UNWIND_WARNING_ZONE`（每季末 23 日至月底）：
  - 若個股近 5 日漲幅 $\ge 8.0\%$，且當日爆量出現長上影線（$\ge 30.0\%$）或實體長黑：
  - 強制啟動防禦扣分 **-15 分**，並標註 `unwind_warning` 警示，防範在季底最後一棒盲目追高站哨；若同時跌破月線則優先導向 `GROUP_UNWIND` 空方避險。

### 4. 集團資金流向總成交額精算 (Turnover-Weighted Heat Ranking)
- 集團作帳熱度排行榜精算當日「**集團總成交金額 (億元 NTD)**」：
  $$\text{Turnover} = \sum (\text{Close} \times \text{Volume Lots} \times 1,000) \div 10^8$$
- 排行榜與 LINE 戰訊即時顯示「成交額 (億)」，使市場千億級主流資金聚焦集團與中小型黑馬集團一目了然。

### 5. 25 大核心作帳集團權威字典池 (`group_pool.json`)
涵蓋台股 25 大作帳集團、160 檔上市櫃成分股，100% 通過跨集團唯一性檢驗，嚴格定錨母子公司權威關係：
- 華新麗華 (1605)、鴻海 (2317)、聯電 (2303)、國巨 (2327)、遠東 (1402)、台塑 (1301)、威盛宏達電 (2388)、中美晶環球晶 (5483)、神達聯強聯華 (3706)、金仁寶 (2324)、長榮 (2603)、台泥 (1101)、統一 (1216)、永豐餘 (1907)、光寶 (2301)、裕隆 (2201)、東元 (1504)、奇美群創 (3481)、霖園富邦國泰金控 (2881)、日月光 (3711)、台達電 (2308)、廣達 (2382)、緯創 (3231)、華碩和碩 (2357)、散熱光電AI作帳 (3017)。

---

## 🎯 三、4+1 五維度硬性濾網 (Hard Filters $\le$ 5)

1. **流動性防守**：5 日均量 $\ge 1,000$ 張，排除全額交割、處置、DR 與 ETF。
2. **雙向狀態機**：必須命中 `GROUP_IGNITION` 或 `GROUP_UNWIND`。
3. **母子共振度 (Tier-1)**：多方作帳要求同集團動能家數 $\ge 2$ 檔。
4. **型態防守面**：多方需站上 20MA 且上影線 $< 45\%$；空方需在 20MA 下且法人賣超。
5. **空間防追高**：月線乖離率絕對值 $|\text{Bias20}| \le \text{adaptive\_bias\_max}$（上限 $8.0\%$，隨大盤位階動態緊縮至 $3.0\%\sim 5.5\%$），防過度乖離反撲。

---

## 🛡️ 四、雙層門檻動態降級容錯機制 (Dynamic Fallback Matrix)

- **🎯 Tier-1 母子共振精選**：嚴格滿足上述 4+1 維度，同集團共振爆發。
- **⚠️ Tier-2 集團潛伏候補（與 Tier-1 並行）**：每日除 Tier-1 精選外，另以放寬條件（5 日均量 $\ge 600$ 張、$|\text{Bias20}| \le \min(10.0\%, \text{bias\_cap} + 1.5\%)$；非點火/結帳但站上月線且法人買超者強制賦予多方四大防線）補位最多 6 檔，不與 Tier-1 重複，戰報以「⚠️ T2」標籤區隔，同集團內排在 Tier-1 之後。

---

## ⚡ 五、實戰四大操盤防線價位 (Four-Price Standard)

### 🟢 多方作帳點火 (`GROUP_IGNITION`)
- 🟢 **進場價位 (Entry)**：收盤價基準（伏擊買進上限）
- 🔵 **加碼價位 (Add-on)**：$\max(\text{High} \times 1.01, \text{Close} \times 1.025, \text{Entry} + 0.5)$（母子共振突破加碼）
- 🔴 **停利目標 (TP1 / TP2)**：TP1 $\max(\text{Addon} \times 1.03, \text{Close} \times \text{TP1\_mult})$ ｜ TP2 $\max(\text{TP1} \times 1.04, \text{Close} \times \text{TP2\_mult})$（落後補漲股目標向上延伸）
- 🛑 **嚴格停損 (Stop-Loss)**：$\min(\text{Low} \times 0.985, \text{MA20} \times 0.985, \text{Close} \times \text{SL\_mult}, \text{Entry} - 0.5)$

### 🔴 空方集團結帳 (`GROUP_UNWIND`)
- 🟢 **進場價位 (Entry)**：收盤價基準（放空建倉價）
- 🔵 **加碼價位 (Add-on)**：$\min(\text{Low} \times 0.995, \text{Close} \times 0.975, \text{Entry} - 0.5)$（破底加空追擊）
- 🔴 **停利目標 (TP1 / TP2)**：TP1 $\min(\text{Addon} \times 0.96, \text{Close} \times 0.92)$ ｜ TP2 $\min(\text{TP1} \times 0.94, \text{Close} \times 0.85)$
- 🛑 **嚴格停損 (Stop-Loss)**：$\max(\text{MA20} \times 1.01, \text{High} \times 1.01, \text{Close} \times 1.035, \text{Entry} + 0.5)$

---

## 📁 六、模組檔案結構

```text
strategies/08_group_window_dressing/
├── group_pool.json      # 25 大集團結構化清單 (160 檔成分股)
├── group_ranking.json   # 每日集團作帳熱度排行榜資料檔 (含成交額與分項分)
├── screener.py          # 盤後選股器 (輸出 47 欄位 CSV、Markdown 戰報與 LINE 輪播)
├── intraday_scanner.py  # 盤中即時雷達 (雙向到價告警與 GLOBAL_TICK_GUARD 防毛刺)
├── run_daily.bat        # 每日盤後選股執行檔
├── start_intraday.bat   # 盤中到價雷達一鍵啟動檔
├── result.csv           # 最新選股 47 欄位標準報表
├── result.md            # 視覺化 Markdown 戰報 (含大盤自適應橫幅與集團排行榜)
└── README.md            # 本技術手冊
```

---

## 🚀 七、執行方式

```bash
# 1. 執行盤後選股 (產出 result.csv 與 result.md)
python strategies/08_group_window_dressing/screener.py --export result.csv

# 2. 執行盤後選股並推播至 LINE 專屬視窗
python strategies/08_group_window_dressing/screener.py --line

# 3. 啟動盤中即時雷達
python strategies/08_group_window_dressing/intraday_scanner.py --interval 60

# 4. 測試盤中推播卡片
python strategies/08_group_window_dressing/intraday_scanner.py --test-push --once
```
