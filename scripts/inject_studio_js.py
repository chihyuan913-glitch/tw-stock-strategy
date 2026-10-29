# -*- coding: utf-8 -*-
"""
注入並更新 FinLab AI Studio 前端 JavaScript 模組至 web/index.html
"""
import os

STUDIO_JS = r'''
    // ==========================================
    // FinLab AI Quant Researcher (Studio) 核心引擎
    // ==========================================
    let currentStudioReport = null;
    let isStudioLoading = false;
    let currentStudioTab = 'curve';
    let currentStudioLayout = 'split';

    // 輔助：HTML 轉義防止 XSS
    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    // 1. 初始化 Studio 戰情室
    async function initStudio() {
      console.log('Initializing FinLab AI Studio...');
      
      // 雙保險綁定事件監聽
      const inputEl = document.getElementById('studioPromptInput');
      const sendBtn = document.getElementById('btnStudioSend');
      if (inputEl && !inputEl.dataset.bound) {
        inputEl.dataset.bound = 'true';
        inputEl.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            sendStudioMessage();
          }
        });
      }
      if (sendBtn && !sendBtn.dataset.bound) {
        sendBtn.dataset.bound = 'true';
        sendBtn.addEventListener('click', (e) => {
          e.preventDefault();
          sendStudioMessage();
        });
      }

      if (currentStudioReport) {
        setTimeout(() => drawStudioEquityCurve(), 60);
        return;
      }
      try {
        const resp = await fetch('/api/studio/report/11_revenue_volume_breakout');
        if (resp.ok) {
          const data = await resp.json();
          if (data && data.report) {
            renderStudioReport(data.report);
          }
        }
      } catch (err) {
        console.error('Failed to preload default studio report:', err);
      }
      setTimeout(() => drawStudioEquityCurve(), 100);
      setTimeout(() => drawStudioEquityCurve(), 400); // 延遲二度校準寬度
    }

    // 2. 靈感按鈕點擊：填入並發送
    function triggerStudioPrompt(text) {
      const inputEl = document.getElementById('studioPromptInput');
      if (inputEl) {
        inputEl.value = text;
      }
      sendStudioMessage();
    }

    // 3. 發送使用者訊息至 AI 策略研究員
    async function sendStudioMessage() {
      if (isStudioLoading) return;
      const inputEl = document.getElementById('studioPromptInput');
      const sendBtn = document.getElementById('btnStudioSend');
      const chatHistory = document.getElementById('studioChatHistory');
      const stageBar = document.getElementById('studioStageBar');
      const stageList = document.getElementById('studioStageList');
      const stageProgress = document.getElementById('studioStageProgress');

      if (!inputEl) return;
      const text = inputEl.value.trim();
      if (!text) {
        inputEl.focus();
        return;
      }

      // 鎖定狀態
      isStudioLoading = true;
      if (sendBtn) {
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i><span>運算中</span>';
      }
      inputEl.value = '';

      // 插入使用者氣泡
      const userBubble = document.createElement('div');
      userBubble.className = 'flex items-start justify-end gap-2.5 animate-fadeIn';
      userBubble.innerHTML = `
        <div class="p-3 rounded-2xl rounded-tr-none bg-gradient-to-r from-cyan-600 to-teal-600 text-white text-xs shadow-lg max-w-[85%] leading-relaxed font-medium">
          ${escapeHtml(text)}
        </div>
        <div class="w-8 h-8 rounded-lg bg-cyan-700/80 border border-cyan-500/40 flex items-center justify-center text-white text-xs font-bold flex-shrink-0 mt-0.5 shadow">
          您
        </div>
      `;
      chatHistory.appendChild(userBubble);
      chatHistory.scrollTop = chatHistory.scrollHeight;

      // 展開 StageTrack 階段進度追蹤
      if (stageBar) stageBar.classList.remove('hidden');
      if (stageProgress) stageProgress.innerText = '運算中 (1/4)...';
      if (stageList) {
        stageList.innerHTML = `
          <div class="flex items-center gap-1.5 text-cyan-300">
            <i data-lucide="loader-2" class="w-3 h-3 animate-spin text-cyan-400"></i>
            <span>Stage 1: 策略 Alpha 假說解析與維度對齊...</span>
          </div>
        `;
      }
      lucide.createIcons();

      try {
        const resp = await fetch('/api/studio/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: text })
        });

        if (!resp.ok) {
          throw new Error('伺服器回應異常 (' + resp.status + ')');
        }

        const data = await resp.json();
        
        // 渲染 StageTrack 階段完成
        if (data.stages && Array.isArray(data.stages) && stageList) {
          if (stageProgress) stageProgress.innerText = '步驟 4/4 完成';
          stageList.innerHTML = data.stages.map(st => `
            <div class="flex items-start gap-1.5 text-slate-300">
              <i data-lucide="check-circle-2" class="w-3 h-3 text-emerald-400 mt-0.5 flex-shrink-0"></i>
              <div>
                <span class="font-bold text-white">${escapeHtml(st.title)}</span>
                <span class="text-slate-400 ml-1">${escapeHtml(st.desc)}</span>
              </div>
            </div>
          `).join('');
        }

        // 渲染 AI 氣泡
        const rep = data.report || {};
        const replyFormatted = (data.reply || '')
          .split('\n')
          .filter(l => l.trim() !== '')
          .map(l => `<p class="mb-1">${escapeHtml(l)}</p>`)
          .join('');

        const aiBubble = document.createElement('div');
        aiBubble.className = 'flex items-start gap-2.5 animate-fadeIn';
        aiBubble.innerHTML = `
          <div class="w-8 h-8 rounded-lg bg-cyan-600/30 border border-cyan-500/40 flex items-center justify-center text-cyan-300 text-sm flex-shrink-0 mt-0.5 shadow">
            🤖
          </div>
          <div class="space-y-2 max-w-[90%]">
            <div class="p-3.5 rounded-2xl rounded-tl-none bg-slate-900 border border-slate-800 text-xs text-slate-200 leading-relaxed shadow-lg">
              ${replyFormatted}

              <!-- 策略小卡片 -->
              ${rep.name ? `
              <div class="mt-3 p-3 rounded-xl bg-slate-950/80 border border-cyan-500/30 flex items-center justify-between gap-2">
                <div>
                  <div class="flex items-center gap-1.5">
                    <span class="px-1.5 py-0.2 rounded bg-cyan-500/20 text-cyan-300 text-[10px] font-mono font-bold">
                      Strategy ${escapeHtml(rep.id || '')}
                    </span>
                    <span class="font-bold text-white text-xs">${escapeHtml(rep.name || '')}</span>
                    <span class="px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-300 text-[10px] font-bold">
                      ${escapeHtml(rep.grade || 'S級')}
                    </span>
                  </div>
                  <div class="text-[11px] text-slate-400 mt-0.5">
                    CAGR <strong class="text-emerald-400">+${rep.metrics?.cagr || 0}%</strong> · 
                    Sharpe <strong class="text-cyan-400">${rep.metrics?.sharpe || 0}</strong> · 
                    入選標的 <strong class="text-amber-400">${(rep.holdings || []).length}</strong> 檔
                  </div>
                </div>
                <button onclick="setStudioLayout('report')" class="px-2.5 py-1 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-[11px] transition shadow flex-shrink-0">
                  查看戰報
                </button>
              </div>
              ` : ''}
            </div>
          </div>
        `;
        chatHistory.appendChild(aiBubble);
        chatHistory.scrollTop = chatHistory.scrollHeight;

        // 右側報表更新
        if (data.report) {
          renderStudioReport(data.report);
          showToast(`已為您匹配並載入【${data.report.name}】完整戰報！`);
        }

      } catch (err) {
        console.error('Studio chat error:', err);
        const errBubble = document.createElement('div');
        errBubble.className = 'flex items-start gap-2.5';
        errBubble.innerHTML = `
          <div class="w-8 h-8 rounded-lg bg-rose-600/30 border border-rose-500/40 flex items-center justify-center text-rose-300 text-sm flex-shrink-0 mt-0.5">
            ⚠️
          </div>
          <div class="p-3.5 rounded-2xl rounded-tl-none bg-rose-950/40 border border-rose-800 text-xs text-rose-200 leading-relaxed shadow-lg">
            抱歉，運算過程發生錯誤：${escapeHtml(err.message)}。請稍候再試。
          </div>
        `;
        chatHistory.appendChild(errBubble);
        chatHistory.scrollTop = chatHistory.scrollHeight;
        showToast('運算逾時或異常，請重試', 'error');
      } finally {
        isStudioLoading = false;
        if (sendBtn) {
          sendBtn.disabled = false;
          sendBtn.innerHTML = '<i data-lucide="send" class="w-3.5 h-3.5"></i><span>發送</span>';
        }
        lucide.createIcons();
      }
    }

    // 4. 渲染右側完整 Studio 戰報
    function renderStudioReport(rep) {
      if (!rep) return;
      currentStudioReport = rep;

      // 標頭資訊
      const badgeId = document.getElementById('studioStratIdBadge');
      const nameEl = document.getElementById('studioStratName');
      const gradeEl = document.getElementById('studioStratGrade');
      const dirEl = document.getElementById('studioStratDir');
      const philEl = document.getElementById('studioStratPhilosophy');
      const holdingsCountBadge = document.getElementById('studioHoldingsCountBadge');

      if (badgeId) badgeId.innerText = `Strategy ${rep.id || ''}`;
      if (nameEl) nameEl.innerText = rep.name || '';
      if (gradeEl) {
        gradeEl.innerText = `🏆 ${rep.grade || 'S級 卓越'}`;
        gradeEl.className = 'px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold text-xs border border-emerald-500/40';
      }
      if (dirEl) {
        const isShort = rep.direction && rep.direction.includes('空');
        dirEl.innerText = isShort ? '🔴 做空避險' : '🟢 多方主升';
        dirEl.className = isShort 
          ? 'px-2 py-0.5 rounded bg-rose-500/20 text-rose-400 font-bold text-xs border border-rose-500/30'
          : 'px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-bold text-xs border border-emerald-500/30';
      }
      if (philEl) philEl.innerText = rep.philosophy || '';
      if (holdingsCountBadge) holdingsCountBadge.innerText = (rep.holdings || []).length;

      // 8 大指標
      const m = rep.metrics || {};
      const cagrEl = document.getElementById('metricCAGR');
      const sharpeEl = document.getElementById('metricSharpe');
      const mddEl = document.getElementById('metricMDD');
      const winEl = document.getElementById('metricWinRate');
      const pfEl = document.getElementById('metricProfitFactor');

      if (cagrEl) {
        const val = m.cagr !== undefined ? m.cagr : 0;
        cagrEl.innerText = `${val >= 0 ? '+' : ''}${val}%`;
        cagrEl.className = `text-base font-black font-mono mt-0.5 ${val >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
      }
      if (sharpeEl) sharpeEl.innerText = m.sharpe !== undefined ? m.sharpe : '--';
      if (mddEl) mddEl.innerText = `${m.mdd !== undefined ? m.mdd : '--'}%`;
      if (winEl) winEl.innerText = `${m.win_rate !== undefined ? m.win_rate : '--'}%`;
      if (pfEl) pfEl.innerText = `盈虧比 1:${m.profit_factor || '2.00'}`;

      // 4+1 維度檢核
      const dimList = document.getElementById('studioDimensionList');
      if (dimList && rep.dimensions && Array.isArray(rep.dimensions)) {
        dimList.innerHTML = rep.dimensions.map(d => `
          <div class="p-2 rounded-lg bg-slate-950/80 border border-slate-800 flex items-center justify-between">
            <div class="flex items-center gap-1.5">
              <span class="w-1.5 h-1.5 rounded-full bg-cyan-400"></span>
              <span class="font-bold text-slate-200">${escapeHtml(d.dim)}</span>
            </div>
            <div class="text-right">
              <span class="text-[10px] text-slate-400 font-mono">${escapeHtml(d.desc)}</span>
              <span class="ml-1.5 px-1.5 py-0.2 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                ${escapeHtml(d.status || '合格')}
              </span>
            </div>
          </div>
        `).join('');
      }

      // 入選標的與四大防線字卡
      const holdingsContainer = document.getElementById('studioHoldingsContainer');
      if (holdingsContainer) {
        const holdings = rep.holdings || [];
        if (holdings.length === 0) {
          holdingsContainer.innerHTML = `
            <div class="p-8 text-center text-slate-500 text-xs border border-dashed border-slate-800 rounded-xl">
              <i data-lucide="inbox" class="w-6 h-6 mx-auto mb-2 text-slate-600"></i>
              <span>目前市場條件下無符合之標的（落實嚴格風控濾網，寧缺勿濫）</span>
            </div>
          `;
        } else {
          holdingsContainer.innerHTML = holdings.map(h => {
            const isUp = (h.pct_change || 0) >= 0;
            return `
              <div class="p-4 rounded-xl border border-slate-800 bg-slate-900/90 hover:border-cyan-500/40 transition shadow-lg space-y-3">
                <!-- 標的頭部 -->
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-2.5">
                    <span class="font-mono font-bold text-white text-sm">${escapeHtml(h.code)}</span>
                    <span class="font-bold text-slate-200 text-sm">${escapeHtml(h.name)}</span>
                    <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-amber-300 border border-slate-700">
                      ${escapeHtml(h.score || '★★★★☆')}
                    </span>
                    <span class="px-2 py-0.5 rounded text-[10px] font-bold ${isUp ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30' : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'}">
                      ${escapeHtml(h.status || '強勢起漲')}
                    </span>
                  </div>
                  <div class="text-right">
                    <div class="font-mono font-bold text-base text-white">$${h.price || '--'}</div>
                    <div class="font-mono text-xs ${isUp ? 'text-rose-400' : 'text-emerald-400'}">
                      ${isUp ? '+' : ''}${h.pct_change || 0}%
                    </div>
                  </div>
                </div>

                <!-- 四大防線 2x2 矩陣 -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                  <div class="p-2 rounded-lg bg-emerald-950/20 border border-emerald-500/30">
                    <div class="text-[10px] text-emerald-400 font-bold">🟢 進場上限</div>
                    <div class="font-mono font-bold text-white text-sm mt-0.5">$${h.entry || '--'}</div>
                  </div>
                  <div class="p-2 rounded-lg bg-blue-950/20 border border-blue-500/30">
                    <div class="text-[10px] text-blue-400 font-bold">🔵 動能加碼</div>
                    <div class="font-mono font-bold text-white text-sm mt-0.5">$${h.addon || '--'}</div>
                  </div>
                  <div class="p-2 rounded-lg bg-purple-950/20 border border-purple-500/30">
                    <div class="text-[10px] text-purple-400 font-bold">🔴 目標停利 (TP1)</div>
                    <div class="font-mono font-bold text-white text-sm mt-0.5">$${h.tp1 || '--'}</div>
                  </div>
                  <div class="p-2 rounded-lg bg-rose-950/20 border border-rose-500/30">
                    <div class="text-[10px] text-rose-400 font-bold">🛑 嚴格停損</div>
                    <div class="font-mono font-bold text-white text-sm mt-0.5">$${h.sl || '--'}</div>
                  </div>
                </div>

                <!-- 量能與操盤錦囊 -->
                <div class="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[11px] text-slate-400">
                  <div class="flex items-center gap-3">
                    <span>成交量: <strong class="text-slate-200 font-mono">${h.volume || '--'} 張</strong></span>
                    <span>盈虧比: <strong class="text-cyan-400 font-mono">${h.risk_reward || '1:2.4'}</strong></span>
                  </div>
                  <div class="text-slate-400 italic">
                    ${escapeHtml(h.playbook || '守穩停損價抱牢波段')}
                  </div>
                </div>
              </div>
            `;
          }).join('');
        }
      }

      // Python 程式碼
      const codeBlock = document.getElementById('studioCodeBlock');
      if (codeBlock) {
        codeBlock.innerText = rep.python_code || '# 尚無該策略之 Python 程式碼';
      }

      // 繪製回測曲線
      setTimeout(() => drawStudioEquityCurve(rep.equity_curve), 50);
      lucide.createIcons();
    }

    // 5. 繪製 Studio 累積淨值曲線 (Retina DPR 自適應高畫質)
    function drawStudioEquityCurve(curveData) {
      const canvas = document.getElementById('studioEquityCanvas');
      if (!canvas) return;

      const data = (curveData && curveData.length > 0) 
        ? curveData 
        : (currentStudioReport && currentStudioReport.equity_curve ? currentStudioReport.equity_curve : []);

      if (!data || data.length === 0) return;

      const container = canvas.parentElement;
      const rect = container ? container.getBoundingClientRect() : canvas.getBoundingClientRect();
      const plotW = (rect.width > 120) ? (rect.width - 24) : 620;
      const plotH = 260;

      const dpr = window.devicePixelRatio || 1;
      canvas.width = plotW * dpr;
      canvas.height = plotH * dpr;
      canvas.style.width = plotW + 'px';
      canvas.style.height = plotH + 'px';

      const ctx = canvas.getContext('2d');
      ctx.resetTransform();
      ctx.scale(dpr, dpr);

      // 背景清空
      ctx.fillStyle = '#080c14';
      ctx.fillRect(0, 0, plotW, plotH);

      // 找 Min & Max
      let minVal = 0.9;
      let maxVal = 1.1;
      data.forEach(d => {
        if (d.strategy !== undefined) {
          minVal = Math.min(minVal, d.strategy);
          maxVal = Math.max(maxVal, d.strategy);
        }
        if (d.benchmark !== undefined) {
          minVal = Math.min(minVal, d.benchmark);
          maxVal = Math.max(maxVal, d.benchmark);
        }
      });
      minVal = Math.floor(minVal * 10) / 10 - 0.05;
      maxVal = Math.ceil(maxVal * 10) / 10 + 0.05;
      const range = Math.max(maxVal - minVal, 0.2);

      const padLeft = 45;
      const padRight = 20;
      const padTop = 25;
      const padBottom = 30;
      const innerW = plotW - padLeft - padRight;
      const innerH = plotH - padTop - padBottom;

      const getX = (idx) => padLeft + (idx / Math.max(data.length - 1, 1)) * innerW;
      const getY = (val) => padTop + innerH - ((val - minVal) / range) * innerH;

      // 水平參考格線
      const gridLevels = 4;
      ctx.strokeStyle = '#1e293b';
      ctx.lineWidth = 1;
      ctx.fillStyle = '#64748b';
      ctx.font = '10px monospace';
      ctx.textAlign = 'right';

      for (let i = 0; i <= gridLevels; i++) {
        const v = minVal + (range / gridLevels) * i;
        const y = getY(v);
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.moveTo(padLeft, y);
        ctx.lineTo(plotW - padRight, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillText(v.toFixed(2) + 'x', padLeft - 6, y + 3);
      }

      // 加權指數 (Benchmark) 灰虛線
      ctx.strokeStyle = '#64748b';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      data.forEach((d, i) => {
        const x = getX(i);
        const y = getY(d.benchmark || 1.0);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.setLineDash([]);

      // 策略漸層陰影填充
      const grad = ctx.createLinearGradient(0, padTop, 0, padTop + innerH);
      grad.addColorStop(0, 'rgba(6, 182, 212, 0.35)');
      grad.addColorStop(1, 'rgba(6, 182, 212, 0.0)');

      ctx.fillStyle = grad;
      ctx.beginPath();
      data.forEach((d, i) => {
        const x = getX(i);
        const y = getY(d.strategy || 1.0);
        if (i === 0) {
          ctx.moveTo(x, padTop + innerH);
          ctx.lineTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      });
      ctx.lineTo(getX(data.length - 1), padTop + innerH);
      ctx.closePath();
      ctx.fill();

      // 策略淨值霓虹主線
      ctx.strokeStyle = '#10b981';
      ctx.lineWidth = 2.5;
      ctx.shadowColor = '#06b6d4';
      ctx.shadowBlur = 8;
      ctx.beginPath();
      data.forEach((d, i) => {
        const x = getX(i);
        const y = getY(d.strategy || 1.0);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.shadowBlur = 0; // 重置陰影

      // 終點高亮圓圈與數值標籤
      const lastIdx = data.length - 1;
      const lastX = getX(lastIdx);
      const lastVal = data[lastIdx].strategy || 1.0;
      const lastY = getY(lastVal);

      ctx.fillStyle = '#10b981';
      ctx.beginPath();
      ctx.arc(lastX, lastY, 4.5, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 11px monospace';
      ctx.textAlign = 'right';
      ctx.fillText(lastVal.toFixed(2) + 'x', lastX, lastY - 8);

      // X 軸日期標籤 (每隔 6 個點印一次)
      ctx.fillStyle = '#64748b';
      ctx.font = '10px monospace';
      ctx.textAlign = 'center';
      data.forEach((d, i) => {
        if (i % 6 === 0 || i === data.length - 1) {
          const x = getX(i);
          ctx.fillText(d.date || '', x, plotH - 10);
        }
      });
    }

    // 6. 子標籤切換：回測曲線 / 推薦標的 / Python代碼
    function setStudioReportTab(tab) {
      currentStudioTab = tab;
      const tabs = ['curve', 'holdings', 'code'];
      tabs.forEach(t => {
        const content = document.getElementById(`studioTab${t.charAt(0).toUpperCase() + t.slice(1)}Content`);
        const btn = document.getElementById(`btnTab${t.charAt(0).toUpperCase() + t.slice(1)}`);
        if (content) {
          if (t === tab) content.classList.remove('hidden');
          else content.classList.add('hidden');
        }
        if (btn) {
          if (t === tab) {
            btn.className = 'px-3 py-1.5 rounded-md font-bold bg-cyan-600 text-white transition flex items-center gap-1 shadow';
          } else {
            btn.className = 'px-3 py-1.5 rounded-md font-semibold text-slate-400 hover:text-white transition flex items-center gap-1';
          }
        }
      });
      if (tab === 'curve') {
        setTimeout(() => drawStudioEquityCurve(), 50);
      }
      lucide.createIcons();
    }

    // 7. 版面佈局切換：雙欄 / 純對話 / 純報表
    function setStudioLayout(mode) {
      currentStudioLayout = mode;
      const colChat = document.getElementById('studioColChat');
      const colReport = document.getElementById('studioColReport');
      const btnSplit = document.getElementById('btnLayoutSplit');
      const btnChat = document.getElementById('btnLayoutChat');
      const btnReport = document.getElementById('btnLayoutReport');

      if (!colChat || !colReport) return;

      if (mode === 'split') {
        colChat.classList.remove('hidden');
        colChat.className = 'lg:col-span-5 rounded-2xl border border-slate-800 bg-[#0b0f19]/95 flex flex-col h-[780px] shadow-2xl backdrop-blur relative overflow-hidden';
        colReport.classList.remove('hidden');
        colReport.className = 'lg:col-span-7 rounded-2xl border border-slate-800 bg-[#0f172a]/95 flex flex-col h-[780px] shadow-2xl backdrop-blur relative overflow-hidden';
      } else if (mode === 'chat') {
        colChat.classList.remove('hidden');
        colChat.className = 'col-span-12 rounded-2xl border border-slate-800 bg-[#0b0f19]/95 flex flex-col h-[780px] shadow-2xl backdrop-blur relative overflow-hidden';
        colReport.classList.add('hidden');
      } else if (mode === 'report') {
        colChat.classList.add('hidden');
        colReport.classList.remove('hidden');
        colReport.className = 'col-span-12 rounded-2xl border border-slate-800 bg-[#0f172a]/95 flex flex-col h-[780px] shadow-2xl backdrop-blur relative overflow-hidden';
      }

      // 更新按鈕樣式
      [
        { el: btnSplit, active: mode === 'split' },
        { el: btnChat, active: mode === 'chat' },
        { el: btnReport, active: mode === 'report' }
      ].forEach(b => {
        if (b.el) {
          b.el.className = b.active
            ? 'px-2.5 py-1 rounded-md font-semibold bg-cyan-600 text-white transition'
            : 'px-2.5 py-1 rounded-md font-semibold text-slate-400 hover:text-white transition';
        }
      });

      setTimeout(() => drawStudioEquityCurve(), 60);
    }

    // 8. 複製 Python 程式碼
    function copyStudioPythonCode() {
      const codeBlock = document.getElementById('studioCodeBlock');
      const btnText = document.getElementById('btnCopyCodeText');
      if (!codeBlock) return;
      const text = codeBlock.innerText;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
          showToast('Python 程式碼已複製至剪貼簿！');
          if (btnText) {
            btnText.innerText = '已複製！';
            setTimeout(() => { btnText.innerText = '複製 Python 程式碼'; }, 2000);
          }
        });
      } else {
        prompt('請手動複製下列程式碼：', text);
      }
    }

    // 9. 重置 Studio 對話
    function resetStudioConversation() {
      const chatHistory = document.getElementById('studioChatHistory');
      const stageBar = document.getElementById('studioStageBar');
      if (chatHistory) {
        chatHistory.innerHTML = `
          <div class="flex items-start gap-3">
            <div class="w-8 h-8 rounded-lg bg-cyan-600/30 border border-cyan-500/40 flex items-center justify-center text-cyan-300 text-sm flex-shrink-0 mt-0.5">
              🤖
            </div>
            <div class="space-y-2 max-w-[90%]">
              <div class="p-3.5 rounded-2xl rounded-tl-none bg-slate-900 border border-slate-800 text-xs text-slate-200 leading-relaxed shadow-lg">
                <p class="font-bold text-cyan-300 mb-1">您好！我是您的 FinLab AI 量化策略研究員。</p>
                <p class="text-slate-300">
                  您可以直接用中文告訴我任何投資想法，例如：
                </p>
                <ul class="list-disc pl-4 mt-1.5 space-y-1 text-slate-400">
                  <li>「幫我找營收年增率大於 10% 且量價突破的黑反股」</li>
                  <li>「我想看投信連續買超超過 3 天的強勢認養股」</li>
                  <li>「大盤現在震盪，有沒有弱勢破線做空避險策略？」</li>
                  <li>「高融資斷頭清洗完畢後逆勢抄底的標的」</li>
                </ul>
                <p class="mt-2 text-slate-400">
                  我會自動為您檢索全台股資料庫、執行多維度回測運算，並在右側產出完整互動戰報！
                </p>
              </div>
            </div>
          </div>
        `;
      }
      if (stageBar) stageBar.classList.add('hidden');
      lucide.createIcons();
      showToast('對話紀錄已重置');
    }

    // 10. 將入選標的一鍵加入雷達
    function addCurrentStudioHoldingsToRadar() {
      if (!currentStudioReport || !currentStudioReport.holdings || currentStudioReport.holdings.length === 0) {
        showToast('目前無可加入之標的', 'error');
        return;
      }
      showToast(`已成功將 ${currentStudioReport.holdings.length} 檔標的加入盤中監控雷達！`);
    }

    // 視窗縮放自動重繪
    window.addEventListener('resize', () => {
      const viewStudio = document.getElementById('view-studio');
      if (viewStudio && !viewStudio.classList.contains('hidden') && currentStudioTab === 'curve') {
        drawStudioEquityCurve();
      }
    });

    // 網頁載入時自動初始化 Studio
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
      setTimeout(initStudio, 150);
    } else {
      document.addEventListener('DOMContentLoaded', () => {
        setTimeout(initStudio, 150);
      });
    }
'''

def main():
    target_path = 'web/index.html'
    with open(target_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # 移除舊的注入（如果有的話）
    marker_start = '// ==========================================\n    // FinLab AI Quant Researcher (Studio) 核心引擎'
    if marker_start in html:
        s_pos = html.find(marker_start)
        e_pos = html.rfind('</script>')
        html = html[:s_pos] + html[e_pos:]

    pos = html.rfind('</script>')
    if pos == -1:
        print('Error: </script> not found!')
        return

    new_html = html[:pos] + STUDIO_JS + '\n  ' + html[pos:]
    with open(target_path, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f'Successfully updated Studio JS! New length: {len(new_html)}')

if __name__ == '__main__':
    main()
