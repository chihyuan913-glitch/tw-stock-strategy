"""
inject_market_pulse_js.py: 將 Market Pulse 與 Treemap 的完整前端邏輯注入 web/index.html
"""

INDEX_PATH = "web/index.html"

with open(INDEX_PATH, "r", encoding="utf-8") as f:
    html = f.read()

pulse_js_code = """
    // ==========================================
    // 模組：FinLab 大盤指標 (Market Pulse 0~10分) 與全市場產業熱力樹狀圖 (Treemap)
    // ==========================================
    let pulseChartInstance = null;
    let pulseRawHistory = [];
    let currentTreemapMetric = 'market_cap';
    let currentTreemapColor = 'tw';
    let currentPulseTimeframe = '3years';

    // 1. 載入大盤 0~10 分多空計分板與走勢資料
    async function loadMarketPulseData(forceRefresh = false) {
      try {
        const url = forceRefresh ? '/api/market_pulse/score?refresh=true' : '/api/market_pulse/score';
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // 更新頂部卡片
        const scoreEl = document.getElementById('pulseScoreNumber');
        const summaryEl = document.getElementById('pulseScoreSummary');
        const dateBadgeEl = document.getElementById('pulseDateBadge');
        const tabBadgeEl = document.getElementById('tabPulseScoreBadge');
        const ratioEl = document.getElementById('signalsBullBearRatio');
        const cashTargetEl = document.getElementById('pulseTargetCash');

        const score = data.score !== undefined ? data.score : 4.0;
        if (scoreEl) {
          scoreEl.innerText = score.toFixed(1);
          // 顏色樣式
          if (score >= 6.0) {
            scoreEl.className = "text-6xl font-black font-mono text-rose-500 tracking-tight";
          } else if (score >= 4.0) {
            scoreEl.className = "text-6xl font-black font-mono text-emerald-400 tracking-tight";
          } else {
            scoreEl.className = "text-6xl font-black font-mono text-emerald-500 tracking-tight";
          }
        }

        if (summaryEl) summaryEl.innerText = data.summary || `${data.signals?.length || 10} 項訊號中 ${data.bullish_count || 4} 項偏多，偏空已持續 ${data.consecutive_days || 2} 個交易日。`;
        if (dateBadgeEl && data.as_of_date) dateBadgeEl.innerText = data.as_of_date;
        if (tabBadgeEl) tabBadgeEl.innerText = `${score.toFixed(1)}分`;
        if (ratioEl) ratioEl.innerText = `偏多 ${data.bullish_count || 4} · 偏空 ${data.bearish_count || 6}`;
        if (cashTargetEl) cashTargetEl.innerText = `${data.cash_target_pct || 50}% 現金`;

        // 渲染 10 項訊號卡片
        renderSignalsList(data.signals || []);

        // 渲染歷史圖表
        pulseRawHistory = data.history || [];
        renderPulseChart(pulseRawHistory, currentPulseTimeframe);

      } catch (err) {
        console.error("載入 Market Pulse 失敗:", err);
      }
    }

    // 2. 渲染 10 項訊號卡片清單 (對齊 Image 2)
    function renderSignalsList(signals) {
      const container = document.getElementById('signalsListContainer');
      if (!container) return;
      container.innerHTML = '';

      signals.forEach((sig, idx) => {
        const isBullish = sig.status === 'bullish';
        const badgeColor = isBullish 
          ? 'bg-rose-500/10 text-rose-400 border border-rose-500/30' 
          : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30';
        const arrow = isBullish ? '↗' : '↘';

        const row = document.createElement('div');
        row.className = "rounded-xl border border-slate-800 bg-slate-900/60 hover:bg-slate-900 transition overflow-hidden";
        row.innerHTML = `
          <div onclick="toggleSignalDetail(${idx})" class="p-3.5 flex items-center justify-between cursor-pointer select-none">
            <div class="flex items-center gap-3">
              <div class="w-9 h-9 rounded-lg bg-slate-800/80 border border-slate-700/80 flex items-center justify-center text-slate-300">
                <i data-lucide="${sig.icon || 'activity'}" class="w-4 h-4 text-slate-300"></i>
              </div>
              <div>
                <div class="text-sm font-bold text-white flex items-center gap-2">
                  <span>${sig.name}</span>
                  <span class="text-[10px] text-slate-500 font-normal border border-slate-700 px-1.5 py-0.2 rounded">${sig.category || '指標'}</span>
                </div>
                <div class="text-xs text-slate-400 mt-0.5">${sig.summary}</div>
              </div>
            </div>
            <div class="flex items-center gap-3">
              <span class="px-2.5 py-1 rounded-md text-xs font-bold font-mono ${badgeColor} flex items-center gap-1">
                <span>${arrow}</span>
                <span>${sig.status_text}</span>
              </span>
              <i data-lucide="chevron-down" id="sig-chevron-${idx}" class="w-4 h-4 text-slate-500 transition-transform duration-200"></i>
            </div>
          </div>
          <div id="sig-detail-${idx}" class="hidden px-4 pb-3.5 pt-1 text-xs text-slate-300 border-t border-slate-800/60 bg-slate-950/40">
            <div class="p-3 rounded-lg bg-slate-900/80 border border-slate-800 space-y-1.5">
              <p><strong class="text-emerald-400">【量化指標定義】</strong> ${sig.details || sig.summary}</p>
              <p class="text-slate-400"><strong>【多空分界法則】</strong> 站上/擴張為偏多 (+1.0分)，跌破/收斂為偏空 (+0.0分)。</p>
            </div>
          </div>
        `;
        container.appendChild(row);
      });

      lucide.createIcons();
    }

    function toggleSignalDetail(idx) {
      const detail = document.getElementById(`sig-detail-${idx}`);
      const chevron = document.getElementById(`sig-chevron-${idx}`);
      if (!detail) return;
      const isHidden = detail.classList.contains('hidden');
      if (isHidden) {
        detail.classList.remove('hidden');
        if (chevron) chevron.style.transform = 'rotate(180deg)';
      } else {
        detail.classList.add('hidden');
        if (chevron) chevron.style.transform = 'rotate(0deg)';
      }
    }

    // 3. 渲染 3 年走勢圖 (Chart.js)
    function renderPulseChart(historyPoints, timeframe = '3years') {
      const canvas = document.getElementById('marketPulseChart');
      if (!canvas) return;

      // 依 timeframe 過濾數據
      let filtered = [...historyPoints];
      const totalLen = filtered.length;
      if (timeframe === 'quarter' && totalLen > 65) {
        filtered = filtered.slice(totalLen - 65);
      } else if (timeframe === 'year' && totalLen > 250) {
        filtered = filtered.slice(totalLen - 250);
      } else if (timeframe === '3years' && totalLen > 750) {
        filtered = filtered.slice(totalLen - 750);
      }

      const labels = filtered.map(p => p.date);
      const scores = filtered.map(p => p.score);
      const twiiNorm = filtered.map(p => p.twii_return_norm || 100);

      if (pulseChartInstance) {
        pulseChartInstance.destroy();
      }

      const ctx = canvas.getContext('2d');
      pulseChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [
            {
              label: '大盤綜合分數',
              data: scores,
              borderColor: '#10b981',
              backgroundColor: 'rgba(16, 185, 129, 0.08)',
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 5,
              pointHoverBackgroundColor: '#10b981',
              tension: 0.1,
              yAxisID: 'yScore',
              segment: {
                borderColor: ctx => {
                  const val = ctx.p1.parsed.y;
                  if (val >= 6.0) return '#ef4444'; // 紅色
                  if (val < 4.0) return '#10b981';  // 綠色
                  return '#64748b';                 // 中性灰
                }
              }
            },
            {
              label: '加權報酬指數',
              data: twiiNorm,
              borderColor: '#94a3b8',
              borderWidth: 1.5,
              borderDash: [3, 3],
              pointRadius: 0,
              pointHoverRadius: 4,
              pointHoverBackgroundColor: '#94a3b8',
              tension: 0.1,
              yAxisID: 'yTwii'
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: {
            mode: 'index',
            intersect: false,
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: '#0f172a',
              titleColor: '#f8fafc',
              bodyColor: '#cbd5e1',
              borderColor: '#334155',
              borderWidth: 1,
              padding: 10,
              callbacks: {
                label: function(context) {
                  if (context.datasetIndex === 0) {
                    return `分數: ${context.parsed.y.toFixed(1)} / 10`;
                  } else {
                    return `加權指數基期: ${context.parsed.y.toFixed(1)}`;
                  }
                }
              }
            }
          },
          scales: {
            x: {
              grid: { color: 'rgba(51, 65, 85, 0.2)' },
              ticks: {
                color: '#64748b',
                maxTicksLimit: 8,
                callback: function(val, index) {
                  const dateStr = this.getLabelForValue(val);
                  return dateStr ? dateStr.substring(0, 7) : '';
                }
              }
            },
            yScore: {
              type: 'linear',
              position: 'left',
              min: 0,
              max: 10,
              grid: {
                color: ctx => {
                  if (ctx.tick.value === 4) return 'rgba(16, 185, 129, 0.4)';
                  if (ctx.tick.value === 6) return 'rgba(239, 68, 68, 0.4)';
                  return 'rgba(51, 65, 85, 0.15)';
                },
                lineWidth: ctx => (ctx.tick.value === 4 || ctx.tick.value === 6) ? 1.5 : 1
              },
              ticks: {
                stepSize: 2,
                color: '#94a3b8'
              }
            },
            yTwii: {
              type: 'linear',
              position: 'right',
              grid: { drawOnChartArea: false },
              ticks: {
                color: '#64748b',
                callback: v => `${Math.round(v)}`
              }
            }
          }
        }
      });
    }

    function changeTimeframe(tf) {
      currentPulseTimeframe = tf;
      ['quarter', 'year', '3years', 'all'].forEach(t => {
        const btn = document.getElementById(`tf-${t}`);
        if (!btn) return;
        if (t === tf) {
          btn.className = "px-2.5 py-1 rounded bg-slate-700 text-white font-bold transition";
        } else {
          btn.className = "px-2.5 py-1 rounded text-slate-400 hover:text-white transition";
        }
      });
      renderPulseChart(pulseRawHistory, tf);
    }

    function toggleMarketMode() {
      showToast("目前已顯示台股加權指數 (^TWII) 多空全景");
    }

    // 4. 載入全市場產業板塊樹狀圖 (Treemap)
    async function loadTreemapData() {
      try {
        const container = document.getElementById('treemapContainer');
        if (!container) return;

        const res = await fetch(`/api/market_pulse/treemap?metric=${currentTreemapMetric}&color_mode=${currentTreemapColor}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // 渲染 D3 樹狀圖
        renderD3Treemap(data.root);

        // 渲染底部報酬率分佈長條圖
        renderDistributionHistogram(data.distribution || [], data.total_stocks || 149);

      } catch (err) {
        console.error("載入 Treemap 失敗:", err);
      }
    }

    // 5. 使用 D3.js 渲染 Squarified Treemap (對齊 Image 1)
    function renderD3Treemap(rootData) {
      const container = document.getElementById('treemapContainer');
      if (!container || !window.d3) return;

      container.innerHTML = '';
      const width = container.clientWidth || 1100;
      const height = Math.max(620, Math.floor(width * 0.55));

      const svg = d3.select(container)
        .append("svg")
        .attr("width", width)
        .attr("height", height)
        .attr("viewBox", [0, 0, width, height])
        .attr("style", "max-width: 100%; height: auto; font-family: Inter, Noto Sans TC, sans-serif;");

      const root = d3.hierarchy(rootData)
        .sum(d => d.value || 0)
        .sort((a, b) => b.value - a.value);

      d3.treemap()
        .tile(d3.treemapSquarify.ratio(1.3))
        .size([width, height])
        .paddingTop(22)
        .paddingInner(1.5)
        .paddingOuter(1.5)
        .round(true)
        (root);

      const tooltip = document.getElementById('treemapTooltip');

      // 取得類股與個股節點
      const sectors = root.children || [];

      // 繪製類股群組 (Sectors)
      const sectorGroups = svg.selectAll(".sector-group")
        .data(sectors)
        .join("g")
        .attr("class", "sector-group")
        .attr("transform", d => `translate(${d.x0},${d.y0})`);

      // 類股頂部標籤背景
      sectorGroups.append("rect")
        .attr("width", d => Math.max(0, d.x1 - d.x0))
        .attr("height", 22)
        .attr("fill", "#0b1120")
        .attr("stroke", "#1e293b")
        .attr("stroke-width", 0.5);

      // 類股文字 (例如：半導體業 -1.28%)
      sectorGroups.append("text")
        .attr("x", 6)
        .attr("y", 15)
        .attr("fill", d => {
          const med = d.data.median_change || 0;
          return med >= 0 ? '#f87171' : '#34d399';
        })
        .attr("font-size", "11px")
        .attr("font-weight", "bold")
        .text(d => {
          const med = d.data.median_change || 0;
          const sign = med > 0 ? '+' : '';
          return `${d.data.name} ${sign}${med}%`;
        });

      // 繪製個股磚塊 (Leaves)
      const leaves = root.leaves();

      const leaf = svg.selectAll(".leaf")
        .data(leaves)
        .join("g")
        .attr("class", "leaf")
        .attr("transform", d => `translate(${d.x0},${d.y0})`);

      // 方塊背景色 (對齊台股紅漲綠跌或國際色彩)
      leaf.append("rect")
        .attr("width", d => Math.max(0, d.x1 - d.x0))
        .attr("height", d => Math.max(0, d.y1 - d.y0))
        .attr("fill", d => getTileColor(d.data.change_pct, currentTreemapColor))
        .attr("stroke", "#090d16")
        .attr("stroke-width", 1.2)
        .attr("rx", 3)
        .style("cursor", "pointer")
        .on("mouseenter", (event, d) => {
          showTreemapTooltip(event, d.data);
        })
        .on("mousemove", (event) => {
          moveTreemapTooltip(event);
        })
        .on("mouseleave", () => {
          hideTreemapTooltip();
        })
        .on("click", (event, d) => {
          // 點擊複製或帶入診斷
          if (typeof runQuickAnalysis === 'function') {
            switchTab('analyzer');
            document.getElementById('analyzerInput').value = d.data.code;
            runQuickAnalysis();
          }
        });

      // 方塊內股票名稱與漲跌幅標籤
      leaf.each(function(d) {
        const g = d3.select(this);
        const w = d.x1 - d.x0;
        const h = d.y1 - d.y0;

        if (w < 34 || h < 24) return; // 太小不繪製文字，依靠 tooltip

        const isLarge = w > 80 && h > 60;
        const isMedium = w > 50 && h > 40;

        const nameSize = isLarge ? "14px" : (isMedium ? "11px" : "9px");
        const changeSize = isLarge ? "12px" : (isMedium ? "10px" : "8px");

        // 股票名稱
        g.append("text")
          .attr("x", w / 2)
          .attr("y", h / 2 - (isMedium ? 4 : 1))
          .attr("text-anchor", "middle")
          .attr("fill", "#ffffff")
          .attr("font-size", nameSize)
          .attr("font-weight", "bold")
          .style("pointer-events", "none")
          .text(d.data.name);

        // 漲跌幅百分比
        const chg = d.data.change_pct || 0;
        const sign = chg > 0 ? '+' : '';
        g.append("text")
          .attr("x", w / 2)
          .attr("y", h / 2 + (isMedium ? 14 : 9))
          .attr("text-anchor", "middle")
          .attr("fill", "#f8fafc")
          .attr("font-size", changeSize)
          .attr("font-weight", "600")
          .attr("font-family", "monospace")
          .style("pointer-events", "none")
          .text(`${sign}${chg.toFixed(2)}%`);
      });
    }

    // 取得方塊色彩 (精確對齊 Image 1 的色調)
    function getTileColor(pct, mode = 'tw') {
      if (pct === undefined || pct === null) return '#334155';
      const absVal = Math.abs(pct);

      if (absVal < 0.05) return '#475569'; // 平盤灰色

      if (mode === 'tw') {
        // 台股模式：漲紅 (Red) 跌綠 (Green)
        if (pct > 0) {
          if (pct >= 7.0) return '#b91c1c'; // 深紅
          if (pct >= 4.0) return '#dc2626'; // 明紅
          if (pct >= 2.0) return '#ef4444'; // 鮮紅
          if (pct >= 1.0) return '#f87171'; // 亮粉紅
          return '#fca5a5';                 // 淺粉紅
        } else {
          if (pct <= -7.0) return '#065f46'; // 深翡翠綠 (聯電 -9.77%)
          if (pct <= -4.0) return '#059669'; // 濃綠 (聯發科 -4.49%)
          if (pct <= -2.0) return '#10b981'; // 綠色 (廣達 -3.64%)
          if (pct <= -1.0) return '#34d399'; // 翡翠綠 (鴻海 -1.39%)
          return '#6ee7b7';                  // 柔和淺綠 (台積電 -0.41%)
        }
      } else {
        // 國際標準：綠漲紅跌
        if (pct > 0) {
          if (pct >= 4.0) return '#059669';
          return '#10b981';
        } else {
          if (pct <= -4.0) return '#b91c1c';
          return '#ef4444';
        }
      }
    }

    // 懸浮提示框 (Tooltip)
    function showTreemapTooltip(event, stock) {
      const tooltip = document.getElementById('treemapTooltip');
      if (!tooltip || !stock) return;

      const chg = stock.change_pct || 0;
      const sign = chg > 0 ? '+' : '';
      const chgColor = chg >= 0 ? 'text-rose-400' : 'text-emerald-400';

      tooltip.innerHTML = `
        <div class="font-extrabold text-white text-sm flex items-center justify-between gap-3 border-b border-slate-700 pb-1.5 mb-1.5">
          <span>${stock.name} (${stock.code})</span>
          <span class="text-[11px] font-normal text-slate-400 px-1.5 py-0.5 rounded bg-slate-800">${stock.sector}</span>
        </div>
        <div class="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs">
          <div class="text-slate-400">收盤價格：<span class="text-white font-bold">$${stock.price}</span></div>
          <div class="text-slate-400">當日漲跌：<span class="${chgColor} font-bold">${sign}${chg.toFixed(2)}%</span></div>
          <div class="text-slate-400">市值估算：<span class="text-slate-200">${stock.market_cap ? stock.market_cap + '億' : '—'}</span></div>
          <div class="text-slate-400">當日成交：<span class="text-slate-200">${stock.volume ? stock.volume + '張' : '—'}</span></div>
        </div>
        <div class="mt-2 text-[10px] text-slate-500 border-t border-slate-800 pt-1">
          點擊可直接帶入「個股即時量化診斷」
        </div>
      `;
      tooltip.classList.remove('hidden');
      moveTreemapTooltip(event);
    }

    function moveTreemapTooltip(event) {
      const tooltip = document.getElementById('treemapTooltip');
      if (!tooltip) return;
      const x = event.pageX + 15;
      const y = event.pageY + 15;
      tooltip.style.left = `${x}px`;
      tooltip.style.top = `${y}px`;
      tooltip.style.position = 'fixed';
    }

    function hideTreemapTooltip() {
      const tooltip = document.getElementById('treemapTooltip');
      if (tooltip) tooltip.classList.add('hidden');
    }

    // 6. 渲染底部報酬率分佈長條圖 (對齊 Image 1 底部)
    function renderDistributionHistogram(distribution, totalStocks) {
      const container = document.getElementById('distributionHistogramContainer');
      const summaryText = document.getElementById('distributionSummaryText');
      if (!container) return;

      container.innerHTML = '';

      let maxCount = 1;
      let upCount = 0;
      let downCount = 0;
      let flatCount = 0;

      distribution.forEach(b => {
        if (b.count > maxCount) maxCount = b.count;
        if (b.label === '0%') {
          flatCount += b.count;
        } else if (b.label.startsWith('+') || b.label === '>+7%') {
          upCount += b.count;
        } else {
          downCount += b.count;
        }
      });

      if (summaryText) {
        const upPct = ((upCount / totalStocks) * 100).toFixed(1);
        const downPct = ((downCount / totalStocks) * 100).toFixed(1);
        summaryText.innerHTML = `
          上漲 <strong class="text-rose-400">${upCount} 檔</strong> (${upPct}%) ‧ 
          平盤 <strong class="text-slate-400">${flatCount} 檔</strong> ‧ 
          下跌 <strong class="text-emerald-400">${downCount} 檔</strong> (${downPct}%)
        `;
      }

      distribution.forEach(b => {
        const barHeight = Math.max(4, Math.round((b.count / maxCount) * 44));
        const bar = document.createElement('div');
        bar.className = "flex-1 flex flex-col items-center justify-end group relative cursor-pointer";
        bar.innerHTML = `
          <div class="w-full rounded-t transition-all group-hover:brightness-125" style="height: ${barHeight}px; background-color: ${b.color};"></div>
          <div class="opacity-0 group-hover:opacity-100 transition absolute -top-8 px-2 py-0.5 rounded bg-slate-900 border border-slate-700 text-[10px] text-white whitespace-nowrap z-20 pointer-events-none">
            ${b.label}: ${b.count}檔 (${b.pct}%)
          </div>
        `;
        container.appendChild(bar);
      });
    }

    // 7. 切換維度與全螢幕
    function changeTreemapMetric(val) {
      currentTreemapMetric = val;
      loadTreemapData();
    }

    function changeTreemapColor(val) {
      currentTreemapColor = val;
      loadTreemapData();
    }

    function refreshMarketPulse(force = true) {
      showToast("正在即時重新計算大盤 10 大指標與產業板塊...");
      loadMarketPulseData(force);
      loadTreemapData();
    }

    function toggleTreemapFullscreen() {
      const el = document.getElementById('treemapCard');
      if (!el) return;
      if (!document.fullscreenElement) {
        el.requestFullscreen().catch(err => {
          console.error("全螢幕請求失敗:", err);
        });
      } else {
        document.exitFullscreen();
      }
    }
"""

if "function loadMarketPulseData" not in html:
    target_end_script = "</script>"
    # 插入在最後一個 </script> 之前
    last_idx = html.rfind(target_end_script)
    if last_idx != -1:
        html = html[:last_idx] + pulse_js_code + "\n  " + html[last_idx:]
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            f.write(html)
        print("[OK] Injected Market Pulse JavaScript logic successfully!")
    else:
        print("[!] Could not find closing </script> tag.")
else:
    print("[*] loadMarketPulseData already present in web/index.html")
