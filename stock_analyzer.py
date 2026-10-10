#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股即時量化操盤分析引擎 (Stock Analysis Engine)
功能：
1. 輸入任意台股代碼 (例如 3221, 2330)，秒級抓取最新即時盤面報價與歷史走勢。
2. 計算短中長均線 (5MA, 10MA, 20MA, 60MA)、布林通道 (BB_UP, BB_MID, BB_DN) 與月線乖離率。
3. 嚴格依循總指揮中心「操盤四大防線價位 (Four-Price Standard)」與「4+1 維度風控架構」：
   - 🟢 進場價位 (Entry Price)：黃金回測低接區或帶量突破買點
   - 🔵 加碼價位 (Add-on Price)：突破關鍵高點或加碼防線
   - 🔴 停利價位 (TP1 / TP2)：短波反壓與等幅滿足點
   - 🛑 停損價位 (Stop-Loss Price)：跌破防守線或月線無條件撤退價位
4. 精算盈虧比 (Risk / Reward) 並產出行動指引。
"""

import sys
import os
import re
import json
import urllib.request
import pandas as pd
import numpy as np
import yfinance as yf

# Windows 命令列編碼保護
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def fetch_realtime_mis(code: str) -> dict:
    """向證交所/櫃買中心 mis.twse API 抓取現價與股票名稱"""
    info = {
        'code': code,
        'name': f'台股_{code}',
        'price': None,
        'open': None,
        'high': None,
        'low': None,
        'prev_close': None,
        'volume': None,
        'time': ''
    }
    channel_str = f"tse_{code}.tw|otc_{code}.tw"
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={channel_str}&json=1&delay=0"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for item in data.get('msgArray', []):
                c = item.get('c', '')
                if c == code:
                    n = item.get('n', '').strip()
                    if n:
                        info['name'] = n
                    
                    z_val = item.get('z', '-')
                    y_val = item.get('y', '-')
                    o_val = item.get('o', '-')
                    h_val = item.get('h', '-')
                    l_val = item.get('l', '-')
                    v_val = item.get('v', '-')
                    
                    if y_val and y_val != '-':
                        info['prev_close'] = float(y_val)
                    if z_val and z_val != '-':
                        info['price'] = float(z_val)
                    elif info['prev_close'] is not None:
                        info['price'] = info['prev_close']
                        
                    if o_val and o_val != '-':
                        info['open'] = float(o_val)
                    if h_val and h_val != '-':
                        info['high'] = float(h_val)
                    if l_val and l_val != '-':
                        info['low'] = float(l_val)
                    if v_val and v_val != '-':
                        info['volume'] = int(v_val)
                    info['time'] = item.get('t', '')
                    break
    except Exception:
        pass
    return info

def fetch_history_and_technicals(code: str) -> dict:
    """自 Yahoo Finance 獲取歷史 K 線並計算核心量化指標"""
    df = pd.DataFrame()
    matched_ticker = None
    
    # 靜音 yfinance 的 stderr 輸出
    import io, contextlib
    f_err = io.StringIO()
    
    for ext in ['.TW', '.TWO']:
        ticker = f"{code}{ext}"
        try:
            with contextlib.redirect_stderr(f_err):
                t = yf.Ticker(ticker)
                hist = t.history(period='6mo')
            if not hist.empty and len(hist) >= 20:
                df = hist
                matched_ticker = ticker
                break
        except Exception:
            continue

    if df.empty or len(df) < 20:
        return None

    # 技術指標計算
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA10'] = df['Close'].rolling(10).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean() if len(df) >= 60 else df['MA20']
    
    std20 = df['Close'].rolling(20).std()
    df['BB_UP'] = df['MA20'] + 2 * std20
    df['BB_MID'] = df['MA20']
    df['BB_DN'] = df['MA20'] - 2 * std20
    df['VOL_MA5'] = df['Volume'].rolling(5).mean()

    last_row = df.iloc[-1]
    prev_row = df.iloc[-2] if len(df) >= 2 else last_row

    return {
        'ticker': matched_ticker,
        'df': df,
        'last_close': float(last_row['Close']),
        'last_open': float(last_row['Open']),
        'last_high': float(last_row['High']),
        'last_low': float(last_row['Low']),
        'prev_close': float(prev_row['Close']),
        'volume_sheets': int(last_row['Volume'] / 1000),
        'vol_ma5': int(last_row['VOL_MA5'] / 1000) if pd.notnull(last_row['VOL_MA5']) else 1000,
        'ma5': float(last_row['MA5']),
        'ma10': float(last_row['MA10']),
        'ma20': float(last_row['MA20']),
        'ma60': float(last_row['MA60']),
        'bb_up': float(last_row['BB_UP']),
        'bb_mid': float(last_row['BB_MID']),
        'bb_dn': float(last_row['BB_DN']),
        'high_20d': float(df['High'].tail(20).max()),
        'low_20d': float(df['Low'].tail(20).min()),
        'high_5d': float(df['High'].tail(5).max()),
        'low_5d': float(df['Low'].tail(5).min()),
        'recent_swing_low': float(df['Low'].tail(3).min()),
        'prev_day_high': float(prev_row['High'])
    }

def analyze_stock(code_input: str) -> str:
    """
    綜合分析個股並產出符合總指揮中心四大防線價位之 LINE 戰報卡片
    """
    # 提取代碼
    m = re.search(r'\b(\d{4})\b', str(code_input).strip())
    if not m:
        return "⚠️ 請輸入正確的台股 4 碼股票代碼（例如：3221、2330）。"
    code = m.group(1)

    # 1. 抓取即時報價 (mis.twse)
    realtime = fetch_realtime_mis(code)
    stock_name = realtime['name']

    # 2. 抓取歷史與指標 (Yahoo Finance)
    tech = fetch_history_and_technicals(code)
    if not tech:
        return f"⚠️ 查無股票代碼 【{code}】 之歷史交易數據，請確認代碼是否正確或已下市。"

    # 3. 現價決定：優先使用即時報價，若盤後或未開盤則以最新收盤價為準
    if realtime['price'] and realtime['price'] > 0:
        current_price = realtime['price']
        prev_close = realtime['prev_close'] or tech['prev_close']
    else:
        current_price = tech['last_close']
        prev_close = tech['prev_close']

    chg_val = current_price - prev_close
    chg_pct = (chg_val / prev_close * 100) if prev_close else 0.0
    chg_icon = "🔺" if chg_val > 0 else ("🔻" if chg_val < 0 else "▫️")

    # 指標數值
    ma5 = tech['ma5']
    ma10 = tech['ma10']
    ma20 = tech['ma20']
    ma60 = tech['ma60']
    bb_up = tech['bb_up']
    bb_dn = tech['bb_dn']
    bias20 = ((current_price - ma20) / ma20 * 100) if ma20 else 0.0

    # 4. 趨勢與型態結構判斷
    is_bullish_alignment = (ma5 >= ma10 >= ma20)
    is_above_ma20 = (current_price >= ma20)

    # 5. 精算四大操盤防線價位 (Four-Price Standard)
    # A. 停損價位 (Stop-Loss)
    # 防守線取：近期 5 日低點下緣或進場成本約 -4%~-5%，嚴格控制下檔風險
    if is_above_ma20 and tech['low_5d'] >= ma20:
        sl_base = min(tech['low_5d'] * 0.995, current_price * 0.955)
    elif is_above_ma20:
        sl_base = min(ma20 * 0.985, current_price * 0.955)
    else:
        sl_base = min(tech['low_5d'] * 0.985, current_price * 0.95)
    stop_loss = round(sl_base, 2)

    # B. 建議進場價位 (Entry Price)
    # 策略考量：若乖離過大 (> 8%) 建議拉回 5MA 或 10MA；若在均線附近則以現價~拉回 1% 為建倉區
    if bias20 > 8.0:
        # 過熱，建議拉回低接
        entry_low = round(min(ma10, current_price * 0.965), 2)
        entry_high = round(current_price * 0.985, 2)
        entry_desc = f"⚠️ 正乖離偏高(+{bias20:.1f}%)，切忌追高！建議拉回 {entry_low}~{entry_high} 分批低接"
    elif bias20 < -8.0:
        # 超跌反彈承接
        entry_low = round(max(bb_dn, current_price * 0.98), 2)
        entry_high = round(current_price, 2)
        entry_desc = f"💡 布林下軌超跌區，現價 {entry_low}~{entry_high} 具反彈支撐，可分批建倉"
    else:
        # 正常多頭或整理區間
        entry_low = round(max(ma10, current_price * 0.975), 2)
        entry_high = round(current_price, 2)
        entry_desc = f"🎯 黃金回測支撐區 {entry_low} ~ {entry_high} 元（回測 5MA/10MA 有守分批承接）"

    entry_target_mid = (entry_low + entry_high) / 2.0

    # C. 加碼價位 (Add-on Price)
    # 帶量過今日高點或突破昨高 +1% (右側確認續攻)
    high_ref = max(tech['last_high'], current_price)
    addon_price = round(max(high_ref * 1.01, current_price * 1.025), 2)

    # D. 停利價位 (TP1 & TP2)
    # TP1: 前波 5~20 日高點反壓或現價 +7%~+9% (短線反壓)
    tp1_price = round(max(tech['high_5d'], current_price * 1.07), 2)
    # TP2: 波段等幅滿足點 (現價 +15% ~ +20%)
    tp2_price = round(max(tech['high_20d'] * 1.05, current_price * 1.15), 2)

    # E. 盈虧比 (Risk / Reward) 計算
    reward = tp1_price - entry_target_mid
    risk = max(entry_target_mid - stop_loss, 0.1)
    rr_ratio = reward / risk

    # 綜合評級燈號
    if bias20 > 9.0:
        status_badge = "🟡【短線過熱・等待拉回】"
        action_summary = "目前股價距離月線乖離較大，盤中請勿急追，耐心等待回測 10MA 或 5MA 再行布局，盈虧比更佳。"
    elif rr_ratio >= 2.0 and is_above_ma20:
        status_badge = "🟢【偏多架構・優質進場點】"
        action_summary = "均線架構向上且盈虧比大於 2.0，拉回進場區可分批建倉，嚴守防守線。"
    elif not is_above_ma20:
        status_badge = "🟠【弱勢整理・慎防破底】"
        action_summary = "股價位於月線之下，屬於弱勢震盪或跌深反彈，僅宜小量試單，切莫重壓。"
    else:
        status_badge = "🔵【區間震盪・伺機低接】"
        action_summary = "目前處於整理箱型內，建議在靠近箱底支撐處分批低接，突破加碼點再順勢加碼。"

    # 6. 組裝繁體中文專業 LINE 戰報卡片
    msg = f"""📊【台股即時量化操盤指引】
━━━━━━━━━━━━━━━
標的：{code} {stock_name}
現價：{current_price:.2f} 元 ({chg_icon} {chg_val:+.2f} / {chg_pct:+.2f}%)
位階：{status_badge}
均線：5MA {ma5:.2f} | 20MA {ma20:.2f} (乖離 {bias20:+.1f}%)

🎯 操盤四大防線價位 (標準指引)：
────────────────
🟢 建議進場：{entry_low:.2f} ~ {entry_high:.2f} 元
   ↳ 策略：{entry_desc}
🔵 動能加碼：{addon_price:.2f} 元
   ↳ 帶量突破確認續強時，右側順勢加碼 1/3
🔴 第一停利(TP1)：{tp1_price:.2f} 元
   ↳ 抵達前高反壓區，建議分批獲利 1/2
🔴 波段滿足(TP2)：{tp2_price:.2f} 元
   ↳ 波段滿足目標點
🛑 嚴格停損：{stop_loss:.2f} 元
   ↳ 跌破關鍵防守線無條件出場，嚴守紀律

⚖️ 風控與盈虧比：
• 預期獲利空間：+{reward:.2f} 元 (約 +{(reward/entry_target_mid*100):.1f}%)
• 承擔下檔風險：-{risk:.2f} 元 (約 -{(risk/entry_target_mid*100):.1f}%)
• 盈虧比 (R:R)：{rr_ratio:.1f} : 1
────────────────
💡 操盤建議：
{action_summary}
━━━━━━━━━━━━━━━
⏰ 報價時間：{realtime['time'] or '最新收盤價'}
（本資訊依據技術面量化精算，投資請獨立審慎評估風險）"""

    return msg.strip()

if __name__ == '__main__':
    test_code = sys.argv[1] if len(sys.argv) > 1 else '3221'
    print(f"[*] 測試分析股票：{test_code}")
    print(analyze_stock(test_code))
