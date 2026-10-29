"""
inject_market_pulse_ui.py: 將 Market Pulse 與 Treemap 視圖注入 web/index.html
"""
import re

INDEX_PATH = "web/index.html"

with open(INDEX_PATH, "r", encoding="utf-8") as f:
    html = f.read()

# 1. 檢查並加入 D3.js 與 Chart.js 到 <head>
if "d3js.org/d3.v7.min.js" not in html:
    head_inject = """  <!-- Chart.js & D3.js for Market Pulse & Treemap -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://d3js.org/d3.v7.min.js"></script>
"""
    html = html.replace("<!-- Lucide Icons -->", head_inject + "  <!-- Lucide Icons -->", 1)

# 2. 加入導航按鈕
tab_btn_target = """        <button onclick="switchTab('advisor')" id="btn-tab-advisor" class="px-4 py-2 rounded-lg text-sm font-semibold transition text-slate-400 hover:text-white hover:bg-slate-800 flex items-center gap-1.5">
          <span>🔮 明日實盤決策</span>
          <span id="tabAdvisorBadge" class="px-1.5 py-0.2 rounded-full bg-emerald-500/20 text-[10px] font-mono text-emerald-400 border border-emerald-500/30">首選</span>
        </button>"""

pulse_tab_btn = """        <button onclick="switchTab('advisor')" id="btn-tab-advisor" class="px-4 py-2 rounded-lg text-sm font-semibold transition text-slate-400 hover:text-white hover:bg-slate-800 flex items-center gap-1.5">
          <span>🔮 明日實盤決策</span>
          <span id="tabAdvisorBadge" class="px-1.5 py-0.2 rounded-full bg-emerald-500/20 text-[10px] font-mono text-emerald-400 border border-emerald-500/30">首選</span>
        </button>
        <button onclick="switchTab('pulse')" id="btn-tab-pulse" class="px-4 py-2 rounded-lg text-sm font-semibold transition text-slate-400 hover:text-white hover:bg-slate-800 flex items-center gap-1.5">
          <span>🗺️ 大盤指標與產業板塊</span>
          <span id="tabPulseScoreBadge" class="px-1.5 py-0.2 rounded-full bg-emerald-500/20 text-[10px] font-mono text-emerald-400 border border-emerald-500/30">4.0分</span>
        </button>"""

if 'id="btn-tab-pulse"' not in html and tab_btn_target in html:
    html = html.replace(tab_btn_target, pulse_tab_btn, 1)

# 3. 注入 view-pulse 視圖區塊 (在 view-advisor 之後)
view_advisor_end = """        <div id="advisorCardGrid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          <!-- 動態載入卡片 -->
        </div>
      </div>
    </div>"""

pulse_view_html = """        <div id="advisorCardGrid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          <!-- 動態載入卡片 -->
        </div>
      </div>
    </div>

    <!-- 視圖：🗺️ 大盤多空指標與產業板塊熱力圖 (FinLab / FinViz Style Market Pulse & Treemap) -->
    <div id="view-pulse" class="space-y-8 hidden">

      <!-- 第一大模組：FinLab 大盤 0~10 分多空計分板與走勢圖 (對齊 Image 2) -->
      <div class="rounded-2xl border border-slate-800 bg-[#0f172a]/95 p-6 shadow-2xl backdrop-blur">
        <!-- 計分板頂部標頭 -->
        <div class="flex flex-wrap items-start justify-between gap-4 border-b border-slate-800/80 pb-5">
          <div>
            <div class="flex items-baseline gap-2">
              <span id="pulseScoreNumber" class="text-6xl font-black font-mono text-emerald-400 tracking-tight">4.0</span>
              <span class="text-2xl font-bold font-mono text-slate-500">/ 10</span>
            </div>
            <p id="pulseScoreSummary" class="text-sm text-slate-300 font-medium mt-1.5">
              10 項訊號中 4 項偏多，偏空已持續 2 個交易日。
            </p>
          </div>
          <div class="flex flex-col items-end gap-2">
            <div class="flex items-center gap-2">
              <span id="pulseDateBadge" class="px-3 py-1 rounded-full bg-slate-800 border border-slate-700 text-xs font-semibold text-slate-300">
                台股 · 10/29
              </span>
              <button onclick="toggleMarketMode()" class="px-3 py-1 rounded-full bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs font-semibold text-slate-300 transition flex items-center gap-1">
                <i data-lucide="arrow-left-right" class="w-3.5 h-3.5"></i>
                <span id="marketModeText">⇆ 大盤 / 櫃買</span>
              </button>
            </div>
            <div class="text-xs text-slate-400">
              建議持股水位：<span id="pulseTargetCash" class="font-bold text-amber-400">50% 現金</span> ‧ <span class="text-emerald-400 font-bold">50% 持股</span>
            </div>
          </div>
        </div>

        <!-- 3 年歷史走勢圖 (Chart.js) -->
        <div class="mt-5">
          <div class="h-64 md:h-72 w-full relative">
            <canvas id="marketPulseChart"></canvas>
          </div>
          <div class="flex flex-wrap items-center justify-between gap-3 text-xs text-slate-400 mt-3 pt-3 border-t border-slate-800/60">
            <div class="flex items-center gap-4">
              <span class="flex items-center gap-1.5"><span class="w-3.5 h-0.5 bg-emerald-400 inline-block"></span> 綜合分數 (0~10分)</span>
              <span class="flex items-center gap-1.5"><span class="w-3.5 h-0.5 border-t border-dashed border-slate-400 inline-block"></span> 加權報酬指數 (標準化)</span>
              <span class="hidden sm:inline text-slate-500">滑過圖表，看任一天</span>
            </div>
            <div class="flex items-center gap-1 bg-slate-800/80 p-0.5 rounded-lg border border-slate-700/60">
              <button onclick="changeTimeframe('quarter')" id="tf-quarter" class="px-2.5 py-1 rounded text-slate-400 hover:text-white transition">季</button>
              <button onclick="changeTimeframe('year')" id="tf-year" class="px-2.5 py-1 rounded text-slate-400 hover:text-white transition">年</button>
              <button onclick="changeTimeframe('3years')" id="tf-3years" class="px-2.5 py-1 rounded bg-slate-700 text-white font-bold transition">3 年</button>
              <button onclick="changeTimeframe('all')" id="tf-all" class="px-2.5 py-1 rounded text-slate-400 hover:text-white transition">全部</button>
            </div>
          </div>
        </div>

        <!-- 組成分數的 10 項訊號 (對齊 Image 2 底部清單) -->
        <div class="mt-8">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-sm font-bold text-white flex items-center gap-2">
              <span>組成分數的訊號</span>
              <span class="px-1.5 py-0.2 rounded-full bg-slate-800 text-[11px] font-mono text-slate-400 border border-slate-700">10</span>
            </h3>
            <span id="signalsBullBearRatio" class="text-xs font-semibold text-slate-400">偏多 4 · 偏空 6</span>
          </div>

          <div id="signalsListContainer" class="space-y-2.5">
            <!-- 動態載入 10 項指標卡片 -->
          </div>
        </div>
      </div>

      <!-- 第二大模組：全市場產業熱力樹狀圖 (FinLab / FinViz Style Treemap，對齊 Image 1) -->
      <div id="treemapCard" class="rounded-2xl border border-slate-800 bg-[#0f172a]/95 p-6 shadow-2xl backdrop-blur">
        <!-- 樹狀圖頂部控制列 -->
        <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800/80 pb-4 mb-4">
          <div>
            <div class="flex items-center gap-2">
              <h2 class="text-lg font-black text-white tracking-wide flex items-center gap-2">
                <span>🗺️ 全市場產業板塊熱力圖</span>
                <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">
                  FinLab / FinViz 雙核心架構
                </span>
              </h2>
            </div>
            <p class="text-xs text-slate-400 mt-0.5">
              21 大 TWSE 官方標準類股 ‧ 150+ 檔核心龍頭 ‧ 中位數 (Median) 族群共振防失真演算法
            </p>
          </div>

          <div class="flex flex-wrap items-center gap-3">
            <!-- 面積維度切換 -->
            <div class="flex items-center gap-1.5 text-xs text-slate-400">
              <span>面積：</span>
              <select id="treemapMetricSelect" onchange="changeTreemapMetric(this.value)" class="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-emerald-500">
                <option value="market_cap">依市值權重 (Market Cap)</option>
                <option value="volume">依成交金額 (Turnover)</option>
              </select>
            </div>

            <!-- 色階切換 -->
            <div class="flex items-center gap-1.5 text-xs text-slate-400">
              <span>色彩：</span>
              <select id="treemapColorSelect" onchange="changeTreemapColor(this.value)" class="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-emerald-500">
                <option value="tw">台股習慣 (紅漲綠跌 預設)</option>
                <option value="intl">國際標準 (綠漲紅跌)</option>
              </select>
            </div>

            <!-- 操作按鈕 -->
            <button onclick="refreshMarketPulse(true)" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs text-slate-200 transition flex items-center gap-1.5" title="即時重新抓取盤後行情">
              <i data-lucide="refresh-cw" class="w-3.5 h-3.5 text-emerald-400"></i>
              <span>即時刷新</span>
            </button>
            <button onclick="toggleTreemapFullscreen()" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs text-slate-200 transition flex items-center gap-1.5" title="全螢幕展示熱力樹狀圖">
              <i data-lucide="maximize" class="w-3.5 h-3.5 text-indigo-400"></i>
              <span>全螢幕</span>
            </button>
          </div>
        </div>

        <!-- 樹狀圖繪製容器 -->
        <div class="w-full overflow-x-auto relative rounded-xl border border-slate-800/80 bg-[#090d16]">
          <div id="treemapContainer" class="w-full min-h-[580px] md:min-h-[640px] relative">
            <!-- D3.js SVG 渲染至此 -->
          </div>
          <!-- 懸浮提示框 (Tooltip) -->
          <div id="treemapTooltip" class="absolute hidden pointer-events-none z-30 px-3.5 py-2.5 rounded-xl bg-slate-900/95 border border-slate-700 shadow-2xl text-xs text-slate-200 backdrop-blur transition-opacity duration-150">
          </div>
        </div>

        <!-- 第三大模組：橫向報酬率分佈長條圖 (Return Distribution Histogram，對齊 Image 1 底部) -->
        <div class="mt-6 pt-5 border-t border-slate-800/80">
          <div class="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span class="flex items-center gap-1.5 font-bold text-slate-300">
              <i data-lucide="bar-chart" class="w-4 h-4 text-emerald-400"></i>
              全市場漲跌家數與報酬率分佈 (Return Distribution)
            </span>
            <span id="distributionSummaryText">上漲 32 檔 (21.5%) ‧ 平盤 4 檔 ‧ 下跌 113 檔 (75.8%)</span>
          </div>

          <!-- 分佈長條圖容器 -->
          <div id="distributionHistogramContainer" class="flex items-end h-14 w-full gap-1 pt-1 pb-1 px-1 bg-slate-950/60 rounded-xl border border-slate-800/80">
            <!-- 動態生成 13 個區間長條 -->
          </div>
          <!-- 橫軸區間標籤 -->
          <div id="distributionLabelsContainer" class="flex justify-between text-[10px] text-slate-500 font-mono mt-1 px-1">
            <span>&lt;-7%</span>
            <span>-3%</span>
            <span>0%</span>
            <span>+3%</span>
            <span>&gt;+7%</span>
          </div>
        </div>
      </div>

    </div>
"""

if 'id="view-pulse"' not in html and view_advisor_end in html:
    html = html.replace(view_advisor_end, pulse_view_html, 1)

# 4. 更新 switchTab 函式
switch_tab_orig = "const tabs = ['strategies', 'advisor', 'radar', 'analyzer', 'weekly', 'hub'];"
switch_tab_new = "const tabs = ['strategies', 'advisor', 'pulse', 'radar', 'analyzer', 'weekly', 'hub'];"

if switch_tab_orig in html:
    html = html.replace(switch_tab_orig, switch_tab_new, 1)

switch_tab_hook_orig = """      if (tab === 'radar') {
        loadRadarWatchlist();
      } else if (tab === 'advisor') {
        loadTomorrowAdvisorData();
      }"""

switch_tab_hook_new = """      if (tab === 'radar') {
        loadRadarWatchlist();
      } else if (tab === 'advisor') {
        loadTomorrowAdvisorData();
      } else if (tab === 'pulse') {
        loadMarketPulseData();
        loadTreemapData();
      }"""

if switch_tab_hook_orig in html:
    html = html.replace(switch_tab_hook_orig, switch_tab_hook_new, 1)

with open(INDEX_PATH, "w", encoding="utf-8") as f:
    f.write(html)

print("[OK] Injected Market Pulse view and tabs into web/index.html")
