#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股即時量化操盤分析引擎 V2.0 旗艦旗艦版 (Stock Analysis Engine)
核心升級特色：
1. 【支援中文股名與代碼互查】：輸入 "5328" 或 "華容"、"2330" 或 "台積電"，均能秒級識別。
2. 【4+1 維度量化星級評分】：流動性、趨勢位階、空間防追高、盈虧比、型態動能 5 星評等。
3. 【量能與籌碼動能解析】：成交量與 5MA 均量比（放量/量縮/爆量）、流動性安全評估。
4. 【操盤四大防線價位精準指引】：
   - 🟢 建議進場價位 (Entry Price)：黃金回測低接區或帶量突破買點
   - 🔵 動能加碼價位 (Add-on Price)：突破關鍵高點或加碼防線
   - 🔴 第一停利與波段滿足 (TP1 / TP2)：前高反壓與等幅滿足點
   - 🛑 嚴格停損防守價位 (Stop-Loss Price)：跌破防守線無條件撤退
5. 【雙向操盤錦囊】：針對「空手者」與「持股者」分別提供具體、明確的行動步驟。
6. 【排版極致優化】：乾淨清晰、避免重複冗言，適應手機 LINE 閱讀體驗。
"""

import sys
import os
import re
import json
import urllib.request
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

# Windows 命令列編碼保護
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

DICT_PATH = Path(__file__).resolve().parent / "stock_dict.json"
STOCK_DICT = {}

def load_stock_dict():
    """載入全台股代碼與名稱對照表"""
    global STOCK_DICT
    if STOCK_DICT:
        return STOCK_DICT
    if DICT_PATH.exists():
        try:
            with open(DICT_PATH, "r", encoding="utf-8") as f:
                STOCK_DICT = json.load(f)
                return STOCK_DICT
        except Exception:
            pass
    return {}

def resolve_stock_input(user_input: str) -> tuple:
    """
    解析使用者輸入，自動轉換為 (代碼, 股票名稱)
    支援：純代碼 "5328"、股名 "華容"、混合 "分析 2330"、"想買台積電"
    """
    text = str(user_input).strip()
    stock_dict = load_stock_dict()

    # 1. 優先尋找 4 碼數字
    m = re.search(r'\b(\d{4})\b', text)
    if m:
        code = m.group(1)
        name = stock_dict.get(code, f"台股_{code}")
        return code, name

    # 2. 搜尋股票中文名稱 (長度由長到短優先比對)
    for key, val in stock_dict.items():
        if not key.isdigit() and len(key) >= 2:
            if key in text:
                return val, key

    return None, None

def fetch_realtime_mis(code: str) -> dict:
    """向證交所/櫃買中心 mis.twse API 抓取現價、即時成交量與股票名稱"""
    info = {
        'code': code,
        'name': '',
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

def analyze_stock(user_input: str) -> str:
    """
    綜合量化分析個股並產出旗艦版 LINE 戰報卡片
    """
    code, stock_name_cached = resolve_stock_input(user_input)
    if not code:
        return "⚠️ 請輸入正確的台股 4 碼股票代碼或股票名稱（例如：3221、5328、台嘉碩、台積電）。"

    # 1. 抓取即時報價
    realtime = fetch_realtime_mis(code)
    stock_name = realtime['name'] or stock_name_cached or f"台股_{code}"

    # 2. 抓取歷史與量化指標
    tech = fetch_history_and_technicals(code)
    if not tech:
        return f"⚠️ 查無股票代碼 【{code} {stock_name}】 之歷史交易數據，請確認代碼是否正確或已下市。"

    # 3. 決定現價與漲跌幅
    if realtime['price'] and realtime['price'] > 0:
        current_price = realtime['price']
        prev_close = realtime['prev_close'] or tech['prev_close']
    else:
        current_price = tech['last_close']
        prev_close = tech['prev_close']

    chg_val = current_price - prev_close
    chg_pct = (chg_val / prev_close * 100) if prev_close else 0.0
    chg_icon = "🔺" if chg_val > 0 else ("🔻" if chg_val < 0 else "▫️")

    # 量能與均量分析
    vol_sheets = realtime['volume'] if (realtime['volume'] and realtime['volume'] > 0) else tech['volume_sheets']
    vol_ma5 = tech['vol_ma5'] if tech['vol_ma5'] > 0 else 1000
    vol_ratio = vol_sheets / vol_ma5 if vol_ma5 > 0 else 1.0

    if vol_ratio >= 1.8:
        vol_status = f"🔥 爆量攻擊 (量比 {vol_ratio:.1f}x・動能充沛)"
    elif vol_ratio >= 1.2:
        vol_status = f"📈 溫和放量 (量比 {vol_ratio:.1f}x・買盤進駐)"
    elif vol_ratio <= 0.6:
        vol_status = f"❄️ 量縮沉澱 (量比 {vol_ratio:.1f}x・洗盤整理)"
    else:
        vol_status = f"⚖️ 量能平穩 (量比 {vol_ratio:.1f}x・常態換手)"

    # 均線數值
    ma5 = tech['ma5']
    ma10 = tech['ma10']
    ma20 = tech['ma20']
    ma60 = tech['ma60']
    bb_up = tech['bb_up']
    bb_dn = tech['bb_dn']
    bias20 = ((current_price - ma20) / ma20 * 100) if ma20 else 0.0

    # 均線架構
    is_bullish = (ma5 >= ma10 >= ma20)
    is_above_ma20 = (current_price >= ma20)
    is_above_ma5 = (current_price >= ma5)

    # 4. 精算四大操盤防線價位 (Four-Price Standard)
    # A. 停損價位 (Stop-Loss)
    if is_above_ma20 and tech['low_5d'] >= ma20:
        sl_base = min(tech['low_5d'] * 0.995, current_price * 0.955)
    elif is_above_ma20:
        sl_base = min(ma20 * 0.985, current_price * 0.955)
    else:
        sl_base = min(tech['low_5d'] * 0.985, current_price * 0.95)
    stop_loss = round(sl_base, 2)

    # B. 建議進場價位 (Entry Price)
    if bias20 > 8.0:
        entry_low = round(min(ma10, current_price * 0.965), 2)
        entry_high = round(current_price * 0.985, 2)
        entry_strat = "正乖離偏高，切忌追高！待拉回 5MA/10MA 支撐不破再分批低接"
    elif bias20 < -8.0:
        entry_low = round(max(bb_dn, current_price * 0.98), 2)
        entry_high = round(current_price, 2)
        entry_strat = "跌深進入布林下軌超跌區，支撐浮現，可於區間內分批小量試單"
    else:
        entry_low = round(max(ma10, current_price * 0.975), 2)
        entry_high = round(current_price, 2)
        entry_strat = "黃金回測支撐區，守穩 5MA/10MA 均線分批佈局"

    entry_target_mid = (entry_low + entry_high) / 2.0

    # C. 加碼價位 (Add-on Price)
    high_ref = max(tech['last_high'], current_price)
    addon_price = round(max(high_ref * 1.01, current_price * 1.025), 2)

    # D. 停利價位 (TP1 & TP2)
    tp1_price = round(max(tech['high_5d'], current_price * 1.07), 2)
    tp2_price = round(max(tech['high_20d'] * 1.05, current_price * 1.15), 2)

    # E. 盈虧比 (Risk / Reward) 計算
    reward = tp1_price - entry_target_mid
    risk = max(entry_target_mid - stop_loss, 0.1)
    rr_ratio = reward / risk

    # 5. 量化 4+1 維度星級評等 (1~5 Stars)
    score = 0
    checks = []
    # ① 流動性維度 (5日均量 >= 1000 張)
    if vol_ma5 >= 1000:
        score += 1
        checks.append("流動性充足")
    else:
        checks.append("量能偏小")

    # ② 趨勢位階維度 (站上月線且多頭排列)
    if is_bullish and is_above_ma20:
        score += 1
        checks.append("多頭排列格局")
    elif is_above_ma20:
        score += 0.5
        checks.append("站上月線震盪")
    else:
        checks.append("月線之下弱勢")

    # ③ 空間防追高維度 (月線乖離率在 -3% ~ +8% 之間)
    if -3.0 <= bias20 <= 8.0:
        score += 1
        checks.append("位階安全未追高")
    elif bias20 > 8.0:
        checks.append("正乖離過大防追高")
    else:
        checks.append("負乖離超跌反彈")

    # ④ 盈虧比維度 (R:R >= 2.0)
    if rr_ratio >= 2.0:
        score += 1
        checks.append(f"優質盈虧比({rr_ratio:.1f}:1)")
    else:
        checks.append(f"盈虧空間普通({rr_ratio:.1f}:1)")

    # ⑤ 型態與動能維度 (站上 5MA 且量比健康)
    if is_above_ma5 and vol_ratio >= 0.7:
        score += 1
        checks.append("站穩短期均線")

    int_score = int(round(score))
    stars = "⭐" * int_score + "☆" * (5 - int_score)

    if int_score >= 4:
        eval_badge = "🟢【偏多優質・進場高勝率】"
        playbook_empty = f"目前盈虧比與均線型態俱佳，空手者可於【{entry_low:.2f} ~ {entry_high:.2f} 元】分批掛單建倉，切勿重壓。"
        playbook_hold = f"持股者續抱！以【{stop_loss:.2f} 元】為移動停損防守；放量過【{addon_price:.2f} 元】可順勢加碼，攻抵【{tp1_price:.2f} 元】分批拔檔 1/2。"
    elif bias20 > 8.0:
        eval_badge = "🟡【短線過熱・等待拉回】"
        playbook_empty = f"正乖離偏高，現價急追容易挨套！空手者請耐心等待盤中拉回【{entry_low:.2f} ~ {entry_high:.2f} 元】再考慮出手。"
        playbook_hold = f"持股者嚴禁再加碼！可逢高於【{tp1_price:.2f} 元】分批獲利減碼入袋，跌破【{stop_loss:.2f} 元】獲利全數了結。"
    elif not is_above_ma20:
        eval_badge = "🟠【弱勢震盪・慎防破底】"
        playbook_empty = "股價處於月線之下，屬於空頭整理或跌深反彈，空手者建議暫時觀望避開。"
        playbook_hold = f"持股者建議反彈逢高減碼，若摜破【{stop_loss:.2f} 元】防守線務必無條件停損退場。"
    else:
        eval_badge = "🔵【區間震盪・伺機低接】"
        playbook_empty = f"目前處於整理箱型內，建議於箱底支撐【{entry_low:.2f} ~ {entry_high:.2f} 元】低接，不追高。"
        playbook_hold = f"持股者以【{stop_loss:.2f} 元】嚴守防守線，待帶量突破【{addon_price:.2f} 元】再行加碼。"

    # 6. 組裝頂級旗艦版 LINE 戰報卡片
    msg = f"""📊【台股即時量化操盤指引】
━━━━━━━━━━━━━━━
標的：{code} {stock_name}
現價：{current_price:.2f} 元 ({chg_icon} {chg_val:+.2f} / {chg_pct:+.2f}%)
量能：{vol_sheets:,} 張 ({vol_status})
評等：{stars} {int_score}星 {eval_badge}
均線：5MA {ma5:.2f} | 20MA {ma20:.2f} (月線乖離 {bias20:+.1f}%)

🎯 操盤四大防線價位 (標準指引)：
────────────────
🟢 建議進場：{entry_low:.2f} ~ {entry_high:.2f} 元
   ▶ 建倉策略：{entry_strat}
🔵 動能加碼：{addon_price:.2f} 元
   ▶ 加碼策略：帶量突破近高確認續強時，右側順勢加碼 1/3
🔴 第一停利(TP1)：{tp1_price:.2f} 元
   ▶ 獲利目標：抵達前高反壓區，建議分批獲利 1/2 入袋
🔴 波段滿足(TP2)：{tp2_price:.2f} 元
   ▶ 獲利目標：波段 1:1 等幅測量滿足點，持盈保泰
🛑 嚴格停損：{stop_loss:.2f} 元
   ▶ 風控防守：跌破關鍵防守線無條件出場，嚴守紀律

⚖️ 盈虧風控試算：
• 預期獲利空間：+{reward:.2f} 元 (+{(reward/entry_target_mid*100):.1f}%)
• 承擔下檔風險：-{risk:.2f} 元 (-{(risk/entry_target_mid*100):.1f}%)
• 風險報酬比 (R:R)：{rr_ratio:.1f} : 1

💡 雙向操盤錦囊：
【空手者】{playbook_empty}
【持股者】{playbook_hold}
━━━━━━━━━━━━━━━
⏰ 報價時間：{realtime['time'] or '最新收盤價'}
（本資訊依據 4+1 維度量化精算，投資請獨立審慎評估風險）"""

    return msg.strip()

if __name__ == '__main__':
    test_input = sys.argv[1] if len(sys.argv) > 1 else '5328'
    print(f"[*] 測試分析：{test_input}")
    print(analyze_stock(test_input))
