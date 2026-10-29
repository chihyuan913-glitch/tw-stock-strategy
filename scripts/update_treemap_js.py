"""
update_treemap_js.py: 升級 web/index.html 內的 renderD3Treemap 與色彩對比度邏輯
徹底解決：
1. 黑色空白部分：改用 root.sum(d => d.children ? 0 : (d.value || 0))，讓子節點完全填滿父節點空間，零黑斑！
2. 加上股票代碼：在所有磚塊中加入 4 碼股票代碼 (例如：台積電 2330、聯電 2303)
3. 解決淺色文字對比度：淺綠/淺紅背景使用深色高對比字體，深紅深綠使用高對比白字，完美對齊 FinLab 專業板塊圖。
"""

INDEX_PATH = "web/index.html"

with open(INDEX_PATH, "r", encoding="utf-8") as f:
    content = f.read()

# 定義全新升級版 renderD3Treemap 與相關函式
new_treemap_code = """    // 5. 使用 D3.js 渲染 Squarified Treemap (對齊 Image 1，零黑斑 + 完整股票代碼)
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
        .attr("style", "max-width: 100%; height: auto; font-family: Inter, Noto Sans TC, sans-serif; background-color: #090d16;");

      // 核心修正：嚴格只對葉子節點 (Leaves) 取值，父節點 (Children) 回傳 0
      // 如此一來，父節點的 value 完全等於子節點的總和，子節點 100% 填滿父節點，徹底消除所有黑色空白！
      const root = d3.hierarchy(rootData)
        .sum(d => (d.children ? 0 : (d.value || 0)))
        .sort((a, b) => b.value - a.value);

      d3.treemap()
        .tile(d3.treemapSquarify.ratio(1.15))
        .size([width, height])
        .paddingTop(d => (d.depth === 1 ? 22 : 0))
        .paddingInner(1.5)
        .paddingOuter(1.5)
        .round(true)
        (root);

      // 取得類股與個股節點
      const sectors = (root.children || []).filter(d => (d.x1 - d.x0 > 20 && d.y1 - d.y0 > 24));

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
        .attr("fill", "#090e1a")
        .attr("stroke", "#1e293b")
        .attr("stroke-width", 0.8);

      // 類股文字 (例如：半導體業 -1.28%)
      sectorGroups.append("text")
        .attr("x", 6)
        .attr("y", 15)
        .attr("fill", d => {
          const med = d.data.median_change || 0;
          return med >= 0 ? '#f87171' : '#34d399';
        })
        .attr("font-size", "11px")
        .attr("font-weight", "800")
        .text(d => {
          const med = d.data.median_change || 0;
          const sign = med > 0 ? '+' : '';
          const w = d.x1 - d.x0;
          if (w < 60) return d.data.name.substring(0, 2);
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
        .attr("rx", 2)
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
          if (typeof runQuickAnalysis === 'function') {
            switchTab('analyzer');
            document.getElementById('analyzerInput').value = d.data.code;
            runQuickAnalysis();
          }
        });

      // 方塊內股票名稱、股票代碼與漲跌幅標籤 (極致高對比度排版)
      leaf.each(function(d) {
        const g = d3.select(this);
        const w = d.x1 - d.x0;
        const h = d.y1 - d.y0;

        if (w < 26 || h < 18) return; // 超微型方塊依靠 Tooltip

        const chg = d.data.change_pct || 0;
        const sign = chg > 0 ? '+' : '';
        const chgStr = `${sign}${chg.toFixed(2)}%`;
        const code = d.data.code || '';
        const name = d.data.name || '';

        // 動態對比色彩：淺綠/淺粉使用深色字；深綠/深紅使用白字
        const textColor = getTextColor(chg, currentTreemapColor);
        const subTextColor = getSubTextColor(chg, currentTreemapColor);

        const isLarge = w > 80 && h > 60;
        const isMedium = w > 48 && h > 36;
        const isSmall = w > 34 && h > 24;

        if (isLarge) {
          // 大型方塊 (如 台積電、聯發科、鴻海、廣達、富邦金)
          // 1. 股票名稱
          g.append("text")
            .attr("x", w / 2)
            .attr("y", h / 2 - 13)
            .attr("text-anchor", "middle")
            .attr("fill", textColor)
            .attr("font-size", "14px")
            .attr("font-weight", "800")
            .style("pointer-events", "none")
            .text(name);

          // 2. 股票代碼
          g.append("text")
            .attr("x", w / 2)
            .attr("y", h / 2 + 1)
            .attr("text-anchor", "middle")
            .attr("fill", subTextColor)
            .attr("font-size", "11px")
            .attr("font-weight", "700")
            .attr("font-family", "monospace")
            .style("pointer-events", "none")
            .text(code);

          // 3. 漲跌幅
          g.append("text")
            .attr("x", w / 2)
            .attr("y", h / 2 + 18)
            .attr("text-anchor", "middle")
            .attr("fill", textColor)
            .attr("font-size", "13px")
            .attr("font-weight", "800")
            .attr("font-family", "monospace")
            .style("pointer-events", "none")
            .text(chgStr);

        } else if (isMedium) {
          // 中型方塊 (如 聯電、欣興、長榮、台達電)
          // 1. 股票名稱
          g.append("text")
            .attr("x", w / 2)
            .attr("y", h / 2 - 8)
            .attr("text-anchor", "middle")
            .attr("fill", textColor)
            .attr("font-size", "11px")
            .attr("font-weight", "bold")
            .style("pointer-events", "none")
            .text(name);

          // 2. 代碼 + 漲跌幅
          const labelText = w > 64 ? `${code} ${chgStr}` : (w > 52 ? `${code}` : `${chgStr}`);
          g.append("text")
            .attr("x", w / 2)
            .attr("y", h / 2 + 8)
            .attr("text-anchor", "middle")
            .attr("fill", textColor)
            .attr("font-size", "9.5px")
            .attr("font-weight", "700")
            .attr("font-family", "monospace")
            .style("pointer-events", "none")
            .text(labelText);

        } else if (isSmall) {
          // 小型方塊
          // 1. 股票代碼
          g.append("text")
            .attr("x", w / 2)
            .attr("y", h / 2 - 3)
            .attr("text-anchor", "middle")
            .attr("fill", textColor)
            .attr("font-size", "9px")
            .attr("font-weight", "800")
            .attr("font-family", "monospace")
            .style("pointer-events", "none")
            .text(code);

          // 2. 漲跌幅
          g.append("text")
            .attr("x", w / 2)
            .attr("y", h / 2 + 8)
            .attr("text-anchor", "middle")
            .attr("fill", textColor)
            .attr("font-size", "8.5px")
            .attr("font-weight", "700")
            .attr("font-family", "monospace")
            .style("pointer-events", "none")
            .text(chgStr);

        } else {
          // 微型方塊：純展示代碼
          g.append("text")
            .attr("x", w / 2)
            .attr("y", h / 2 + 3)
            .attr("text-anchor", "middle")
            .attr("fill", textColor)
            .attr("font-size", "8px")
            .attr("font-weight", "bold")
            .attr("font-family", "monospace")
            .style("pointer-events", "none")
            .text(code);
        }
      });
    }

    // 動態文字對比度運算 (防淺色背景文字失真)
    function getTextColor(pct, mode = 'tw') {
      const absVal = Math.abs(pct || 0);
      // 在淺色平盤或微幅波動區間 (0.05% ~ 1.8%)，使用極深墨黑文字達成高對比
      if (absVal < 1.8) {
        return '#090d16'; // 高對比深黑字
      }
      return '#ffffff';   // 明亮純白字
    }

    function getSubTextColor(pct, mode = 'tw') {
      const absVal = Math.abs(pct || 0);
      if (absVal < 1.8) {
        return '#1e293b'; // 次要資訊深炭灰
      }
      return '#f1f5f9';   // 次要資訊柔亮白
    }"""

# 尋找舊的 renderD3Treemap 開頭
start_marker = "    // 5. 使用 D3.js 渲染 Squarified Treemap (對齊 Image 1)"
end_marker = "    // 取得方塊色彩 (精確對齊 Image 1 的色調)"

if start_marker in content and end_marker in content:
    idx_start = content.find(start_marker)
    idx_end = content.find(end_marker)
    content = content[:idx_start] + new_treemap_code + "\n\n" + content[idx_end:]
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print("[OK] Successfully upgraded renderD3Treemap in web/index.html!")
else:
    print(f"[!] Markers not found: start={start_marker in content}, end={end_marker in content}")
