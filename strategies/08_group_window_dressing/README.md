# 策略 08：台股集團作帳與母子聯動伏擊策略 (Group Window Dressing Ambush Screener)

> 本策略為「台股量化選股策略最高總指揮中心」旗下旗艦級季度/年度週期性集團聯動策略，鎖定台股 25 大核心集團（如華新麗華、鴻海、聯電、國巨等），透過「作帳行事曆動態時序評估」、「集團母子共振度與階層分工引擎」以及「雙向狀態機」，精準捕捉季底拉抬與年底集團行情。

---

## 📖 一、核心策略哲學 (Why)

### 1. 台股獨特之集團交叉持股與合併報表作帳生態
在台股資本市場中，大型集團具備錯綜複雜的股權連結與轉投資架構。每逢季底（3月、6月、9月）與年底（11~12月）：
- **合併報表帳面利潤衝刺**：母公司或控股公司為拉高季報/年報投資收益與每股淨值，具備強烈動機透過旗下投資公司與主力籌碼拉抬低基期子公司股價。
- **母子聯動擴散效應**：當同集團旗艦龍頭（如華新 1605、聯電 2303、鴻海 2317）帶量站上月線表態起漲時，法人與內資大戶資金會迅速外溢擴散至集團內部其他中小型補漲股，形成「集團母子共振」。
- **結帳賣壓風險防範**：一旦時序進入季末結帳警戒期（3/23、6/23、9/23、12/23 起），或個股跌破 20MA 月線且法人大額出貨提款，集團資金抽離極易引發高檔多殺多結帳賣壓，順勢轉為空方獵殺。

### 2. 雙向狀態機精準分流
- 🟢 **【作帳點火・母子共振 (GROUP_IGNITION)】**：
  - 條件：站上 20MA 月線、法人大額買超（1日 $\ge 200$ 張 或 5日 $\ge 500$ 張且當日買超）、收紅或具長下影線（$\ge 25\%$）且上影線 $< 45\%$。
  - 同集團協同轉強動能家數 $\ge 2$ 檔。
- 🔴 **【集團結帳・順勢放空 (GROUP_UNWIND)】**：
  - 條件：摜破 20MA 月線、法人大額賣超（1日 $\le -300$ 張 或 5日 $\le -800$ 張）、收黑或留長上影線（$\ge 35\%$）或近 5 日跌幅 $\le -2.0\%$。

---

## 🧮 二、核心量化演算法引擎

### 1. 25 大核心作帳集團字典池 (`group_pool.json`)
涵蓋台股最具代表性之 25 大作帳集團、超過 160 檔上市櫃成分股，明確定義旗艦母公司 (`FLAGSHIP`) 與子公司 (`SUBSIDIARY`)：
- 華新麗華 (1605)、鴻海 (2317)、聯電 (2303)、國巨 (2327)、遠東 (1402)、台塑 (1301)、威盛宏達電 (2388)、中美晶環球晶 (5483)、神達聯強聯華 (3706)、金仁寶 (2324)、長榮 (2603)、台泥 (1101)、統一 (1216)、永豐餘 (1907)、光寶 (2301)、裕隆 (2201)、東元 (1504)、奇美群創 (3481)、霖園富邦國泰金控 (2881)、日月光 (3711)、台達電 (2308)、廣達 (2382)、緯創 (3231)、華碩和碩 (2357)、散熱光電AI作帳 (3017)。

### 2. 時序行事曆動態評估 (`evaluate_window_dressing_calendar`)
- `UNWIND_WARNING_ZONE` (⚠️ 季末/年末結帳警戒期)：3, 6, 9, 12 月 23 日至月底。
- `Q4_YEAR_END_PEAK` (🔥 Q4 年底集團作帳主升期)：11/1 ~ 12/22。
- `QUARTER_END_SPRINT` (⏳ 季底作帳衝刺期)：3, 6, 9 月 1~22 日 或 10/15 起提前卡位。
- `NORMAL_ROTATION` (🛡️ 非季底集團輪動觀察期)：其他平穩月份。

### 3. 集團母子共振度與階層分工 (`calculate_group_resonance_and_hierarchy`)
- **集團共振率**：
  $$\text{Resonance Ratio} = \frac{\text{同集團轉強檔數}}{\text{集團總成分股數}} \times 100\%$$
- **階層角色分工 (Hierarchy Role)**：
  - 👑 **【集團領頭羊主攻】**：母公司或集團內漲幅最大之龍頭股，引領族群攻堅。
  - 🚀 **【母強子落後補漲】**：母公司已大漲突破，子公司漲幅落後且站穩月線，具備強烈補漲利差。
  - 🟢 **【集團協同齊漲】**：族群內同步走揚之成員。
  - ⚠️ **【高檔結帳提款】**：弱勢破線且法人大額提款標的。

---

## 🎯 三、4+1 五維度硬性濾網 (Hard Filters $\le$ 5)

1. **流動性防守**：5 日均量 $\ge 1,000$ 張，排除全額交割、處置、DR 與 ETF。
2. **雙向狀態機**：必須命中 `GROUP_IGNITION` 或 `GROUP_UNWIND`。
3. **母子共振度 (Tier-1)**：多方作帳要求同集團動能家數 $\ge 2$ 檔。
4. **型態防守面**：多方需站上 20MA 且上影線 $< 45\%$；空方需在 20MA 下且法人賣超。
5. **空間防追高**：月線乖離率絕對值 $|\text{Bias20}| \le 8.0\%$，防過度乖離反撲。

---

## 🛡️ 四、雙層門檻動態降級容錯機制 (Dynamic Fallback Matrix)

- **🎯 Tier-1 母子共振精選**：嚴格滿足上述 4+1 維度，同集團共振爆發。
- **⚠️ Tier-2 集團潛伏候補**：若市場平淡無 Tier-1 標的，自動放寬至 5 日均量 $\ge 600$ 張、$|\text{Bias20}| \le 10.0\%$，依綜合評分、共振率與法人買超排序精選前 6 檔。

---

## ⚡ 五、實戰四大操盤防線價位 (Four-Price Standard)

### 🟢 多方作帳點火 (`GROUP_IGNITION`)
- 🟢 **進場價位 (Entry)**：收盤價基準（伏擊買進上限）
- 🔵 **加碼價位 (Add-on)**：$\max(\text{High} \times 1.01, \text{Close} \times 1.025, \text{Entry} + 0.5)$（母子共振突破加碼）
- 🔴 **停利目標 (TP1 / TP2)**：TP1 $\max(\text{Addon} \times 1.03, \text{Close} \times 1.07)$ ｜ TP2 $\max(\text{TP1} \times 1.04, \text{Close} \times 1.14)$
- 🛑 **嚴格停損 (Stop-Loss)**：$\min(\text{Low} \times 0.985, \text{MA20} \times 0.985, \text{Close} \times 0.955, \text{Entry} - 0.5)$

### 🔴 空方集團結帳 (`GROUP_UNWIND`)
- 🟢 **進場價位 (Entry)**：收盤價基準（放空建倉價）
- 🔵 **加碼價位 (Add-on)**：$\min(\text{Low} \times 0.995, \text{Close} \times 0.975, \text{Entry} - 0.5)$（破底加空追擊）
- 🔴 **停利目標 (TP1 / TP2)**：TP1 $\min(\text{Addon} \times 0.96, \text{Close} \times 0.92)$ ｜ TP2 $\min(\text{TP1} \times 0.94, \text{Close} \times 0.85)$
- 🛑 **嚴格停損 (Stop-Loss)**：$\max(\text{MA20} \times 1.01, \text{High} \times 1.01, \text{Close} \times 1.035, \text{Entry} + 0.5)$

---

## 📁 六、模組檔案結構

```text
strategies/08_group_window_dressing/
├── group_pool.json      # 25 大集團結構化清單 (165 檔成分股)
├── screener.py          # 盤後選股器 (輸出 44 欄位 CSV 與 Markdown 戰報)
├── intraday_scanner.py  # 盤中即時雷達 (雙向到價告警與 GLOBAL_TICK_GUARD 防毛刺)
├── run_daily.bat        # 每日盤後選股執行檔
├── start_intraday.bat   # 盤中到價雷達一鍵啟動檔
├── result.csv           # 最新選股 44 欄位報表
├── result.md            # 視覺化 Markdown 戰報
└── README.md            # 本技術手冊
```

---

## 🚀 七、執行方式

```bash
# 1. 執行盤後選股 (產出 result.csv 與 result.md)
python strategies/08_group_window_dressing/screener.py --export result.csv

# 2. 執行盤後選股並推播至 LINE
python strategies/08_group_window_dressing/screener.py --line

# 3. 啟動盤中即時雷達
python strategies/08_group_window_dressing/intraday_scanner.py --interval 60

# 4. 測試盤中推播卡片
python strategies/08_group_window_dressing/intraday_scanner.py --test-push --once
```
