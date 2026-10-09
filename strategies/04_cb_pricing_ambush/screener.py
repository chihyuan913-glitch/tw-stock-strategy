#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股選股策略 04：可轉債 (CB) 定價伏擊與區間博弈旗艦策略
核心邏輯：
1. 掌握「董事會宣告發行至定價基準日」主力的壓低訂價與籌碼洗盤慣性。
2. 鎖定「申報生效後 T+3~T+8」量縮窒息臨界點（左側伏擊）與「定價公告當日」（右側確認）。
3. 嚴格執行「四大價位標準」：🟢進場、🔵加碼、🔴停利 (TP1/TP2)、🛑停損。
4. 強制規範「個股最後執行日期 (Exit Deadline)」：掛牌滿 3 個月閉鎖期解禁日無論盈虧強制全數出清，防債轉股賣壓！
"""

import sys
import os
import argparse
import datetime
import json
import urllib.request
import pandas as pd
import numpy as np

# 加入專案根目錄至 sys.path 以便共用模組 (如 line_sender)
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# 設定標準輸出編碼為 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

POOL_FILE = os.path.join(os.path.dirname(__file__), 'cb_pool.json')

def load_cb_pool():
    """載入可轉債追蹤母池"""
    if os.path.exists(POOL_FILE):
        with open(POOL_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def fetch_stock_market_data(code, market='TW'):
    """從 Yahoo Finance 抓取個股近期價量資料與均線"""
    suffixes = [market, 'TW', 'TWO']
    for suffix in suffixes:
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{code}.{suffix}?interval=1d&range=3mo'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                result = data['chart']['result'][0]
                quotes = result['indicators']['quote'][0]
                closes = [c for c in quotes['close'] if c is not None]
                vols = [v for v in quotes['volume'] if v is not None]
                highs = [h for h in quotes['high'] if h is not None]
                lows = [l for l in quotes['low'] if l is not None]
                
                if len(closes) >= 15:
                    curr_price = float(closes[-1])
                    ma5 = float(np.mean(closes[-5:]))
                    ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else ma5
                    ma60 = float(np.mean(closes[-60:])) if len(closes) >= 60 else ma20
                    curr_vol = int(vols[-1] // 1000) # 轉為張數
                    vol20 = int(np.mean(vols[-20:]) // 1000) if len(vols) >= 20 else curr_vol
                    vol_ratio = round((curr_vol / vol20 * 100), 1) if vol20 > 0 else 100.0
                    recent_high = float(max(highs[-5:]))
                    recent_low = float(min(lows[-5:]))
                    
                    return {
                        'curr_price': curr_price,
                        'ma5': round(ma5, 2),
                        'ma20': round(ma20, 2),
                        'ma60': round(ma60, 2),
                        'curr_vol': curr_vol,
                        'vol20': vol20,
                        'vol_ratio': vol_ratio,
                        'recent_high': round(recent_high, 2),
                        'recent_low': round(recent_low, 2)
                    }
        except Exception:
            continue
    return None

def score_and_analyze_cb(cb_item, market_data):
    """
    依據 V2.0 旗艦評分矩陣打分並精確推算四大操盤價位與最後執行死線
    """
    price = round(market_data['curr_price'], 1)
    ma5 = market_data['ma5']
    ma20 = market_data['ma20']
    ma60 = market_data['ma60']
    vol = market_data['curr_vol']
    vol_ratio = market_data['vol_ratio']
    
    score = 0
    
    # 1. 募資目的與稀釋度 (25 分)
    if cb_item.get('purpose_type') == 'EXPANSION':
        score += 15
    else:
        score += 7
    dilution = cb_item.get('dilution_rate', 10.0)
    if dilution <= 8.0:
        score += 10
    elif dilution <= 12.0:
        score += 6
    else:
        score += 2

    # 2. 籌碼與量縮指標 (25 分)
    if vol_ratio <= 35.0:
        score += 15
    elif vol_ratio <= 55.0:
        score += 10
    elif vol_ratio <= 75.0:
        score += 5
    else:
        score += 2
        
    if vol >= 1000:
        score += 10
    elif vol >= 500:
        score += 6
    else:
        score += 2

    # 3. 時程與進度狀態 (25 分)
    status = cb_item.get('status')
    if status == 'PRICED':
        score += 25  # 轉換價已定，確定性最高
    elif status == 'EFFECTIVE':
        score += 18  # 申報生效中，即將定價
    elif status == 'ANNOUNCED':
        score += 12  # 剛宣布，壓盤初期
    elif status == 'LISTED':
        score += 10  # 已掛牌，閉鎖期中

    # 4. 技術位階與均線防守 (25 分)
    if price >= ma60:
        score += 15
    elif price >= ma60 * 0.97:
        score += 8
    else:
        score += 2
        
    if price >= ma20:
        score += 10
    elif price >= ma20 * 0.98:
        score += 5
        
    # 計算星級推薦
    if score >= 80:
        stars = "★★★★★"
    elif score >= 65:
        stars = "★★★★☆"
    elif score >= 50:
        stars = "★★★☆☆"
    else:
        stars = "★★☆☆☆"

    # 計算四大操盤防線價位 (依據狀態與轉換價格精確動態錨定)
    conv_price = cb_item.get('conversion_price')
    if not conv_price:
        # 若尚未敲定轉換價，以現價*1.02 作為預估轉換價
        conv_price = round(price * 1.02, 2)
        
    if status == 'PRICED':
        # 右側確認型：轉換價已公告
        entry_low = round(min(conv_price, price * 0.98), 1)
        entry_high = round(price, 1)
        entry_price_str = f"{entry_low} ～ {entry_high}"
        addon_price = round(max(market_data['recent_high'], price * 1.015), 1)
        tp1_price = round(max(conv_price * 1.07, price * 1.06), 1)
        tp2_price = round(max(conv_price * 1.15, price * 1.14), 1)
        sl_price = round(conv_price * 0.96, 1)
    elif status == 'EFFECTIVE':
        # 申報生效等待型：回測季線支撐試單
        entry_low = round(ma60, 1)
        entry_high = round(min(ma20, price), 1)
        entry_price_str = f"{entry_low} ～ {entry_high}"
        addon_price = round(price * 1.02, 1)
        tp1_price = round(price * 1.075, 1)
        tp2_price = round(price * 1.16, 1)
        sl_price = round(ma60 * 0.96, 1)
    else:
        # 剛宣布或已掛牌
        entry_low = round(min(ma60, price * 0.98), 1)
        entry_high = round(price, 1)
        entry_price_str = f"{entry_low} ～ {entry_high}"
        addon_price = round(price * 1.025, 1)
        tp1_price = round(price * 1.07, 1)
        tp2_price = round(price * 1.14, 1)
        sl_price = round(min(ma60 * 0.96, price * 0.95), 1)

    target_130_pct = round(conv_price * 1.30, 1)
    lockup_end = cb_item.get('lockup_end_date', '掛牌滿3個月當日')

    return {
        '代號': cb_item['code'],
        '名稱': cb_item['name'],
        '星級': stars,
        '評分': score,
        '現價': price,
        '進度狀態': cb_item['status_desc'],
        '進場價位': entry_price_str,
        '加碼價位': addon_price,
        '停利TP1': tp1_price,
        '停利TP2': tp2_price,
        '停損價位': sl_price,
        '最後執行死線': lockup_end,
        '空間極限130%': target_130_pct,
        '退場規則說明': cb_item.get('exit_rule', f'{lockup_end} 閉鎖解禁前出清'),
        '轉換價格': conv_price,
        '成交量(張)': vol,
        '20日均量': market_data['vol20'],
        '量縮比率%': vol_ratio,
        '20MA': ma20,
        '60MA': ma60,
        'CB期次': cb_item['cb_name'],
        '資金用途': cb_item['purpose']
    }

def format_line_message(results, date_str=None):
    """格式化產生高品質 LINE 戰報推播訊息（完整遵循四大價位與最後執行死線原則）"""
    if not date_str:
        date_str = datetime.date.today().strftime('%Y/%m/%d')
        
    msg_lines = [
        "╔═══════════════════════╗",
        "║ 📊【台股可轉債】CB定價伏擊旗艦戰報 ║",
        "╚═══════════════════════╝",
        f"📅 交易日期：{date_str} 盤後檢核",
        "🎯 核心邏輯：壓低定價伏擊 ＋ 籌碼洗淨 ＋ 閉鎖期紅利",
        f"🔥 今日入選：共 {len(results)} 檔優選標的",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    
    for i, r in enumerate(results, 1):
        code = r['代號']
        name = r['名稱']
        stars = r['星級']
        score = r['評分']
        price = r['現價']
        status_desc = r['進度狀態']
        cb_name = r['CB期次']
        conv_p = r['轉換價格']
        vol = r['成交量(張)']
        vol_r = r['量縮比率%']
        purpose = r['資金用途']
        
        entry_p = r['進場價位']
        addon_p = r['加碼價位']
        tp1 = r['停利TP1']
        tp2 = r['停利TP2']
        sl = r['停損價位']
        exit_deadline = r['最後執行死線']
        target_130 = r['空間極限130%']
        exit_rule = r['退場規則說明']

        msg_lines.append(
            f"【{i:02d}】📍 {code} {name} ｜ {stars} (評分:{score})\n"
            f"📌 發行期次：{cb_name}\n"
            f"💵 現價收盤：{price} 元 ｜ 進度：{status_desc}\n"
            f"────────────────────\n"
            f"🎯 操盤四大防線：\n"
            f"├ 🟢 進場價位：{entry_p} 元 (伏擊建倉 30%)\n"
            f"├ 🔵 加碼價位：{addon_p} 元 (突破放量確認加碼 70%)\n"
            f"├ 🔴 停利目標：TP1 {tp1} 元 ｜ TP2 {tp2} 元\n"
            f"└ 🛑 停損防守：{sl} 元 (破線無條件退場，嚴守紀律)\n"
            f"────────────────────\n"
            f"⏳ 個股最後執行死線 (強制退場)：\n"
            f"• 時間極限：{exit_deadline}\n"
            f"  └ 說明：{exit_rule}\n"
            f"• 空間極限：{target_130} 元 (轉換價 130% 啟動強制贖回，獲利終止)\n"
            f"────────────────────\n"
            f"📈 籌碼與定價數據：\n"
            f"• 轉換價格：{conv_p} 元\n"
            f"• 量能表現：成交 {vol:,} 張 (量能比 {vol_r}%)\n"
            f"• 資金用途：{purpose}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        
    msg_lines.append("💡【總指揮風控紀律】CB發行壓盤目的達成即反彈！請密切緊盯「最後執行日期」，解禁日前不論盈虧全數出清，拒接轉股拋售！")
    return "\n".join(msg_lines)

def run_screener(date_str=None, export_path=None, send_line=False):
    """執行可轉債定價伏擊選股器"""
    if not date_str:
        date_str = datetime.date.today().strftime('%Y/%m/%d')
        
    print(f"\n[+] 正在載入可轉債追蹤母池：{POOL_FILE} ...")
    pool = load_cb_pool()
    if not pool:
        print("[X] 查無可轉債追蹤母池資料！")
        return []

    print(f"[+] 正在抓取個股實時行情與均線數據 (共 {len(pool)} 檔標的)...")
    results = []
    for item in pool:
        code = item['code']
        market = item.get('market', 'TW')
        m_data = fetch_stock_market_data(code, market)
        if m_data:
            analysis = score_and_analyze_cb(item, m_data)
            results.append(analysis)
        else:
            print(f"[!] 無法獲取 {code} {item['name']} 之行情數據，跳過。")

    if not results:
        print("[!] 無任何有效分析數據。")
        return []

    # 依評分排序
    results.sort(key=lambda x: x['評分'], reverse=True)
    df = pd.DataFrame(results)

    # 終端機格式化展示
    print("\n" + "="*115)
    print(f"🎯【台股可轉債 CB 定價伏擊旗艦選股日報】（結算日期: {date_str}，共 {len(results)} 檔）")
    print("="*115)
    display_cols = ['代號', '名稱', '星級', '評分', '現價', '進場價位', '加碼價位', '停利TP1', '停損價位', '最後執行死線', '進度狀態']
    print(df[display_cols].to_string(index=False))
    print("="*115)

    # 匯出 CSV (UTF-8-SIG 防亂碼)
    if export_path:
        out_file = export_path
        if not os.path.isabs(out_file):
            out_file = os.path.join(os.path.dirname(__file__), export_path)
        df.to_csv(out_file, index=False, encoding='utf-8-sig')
        print(f"[✓] 選股報表已成功匯出至：{out_file} (UTF-8-BOM 編碼，Excel 開啟不亂碼)")

    # 發送 LINE 推播
    if send_line:
        print("\n[+] 正在發送 LINE 推播戰報 (策略代號: 04)...")
        try:
            from line_sender import send_to_line
            line_msg = format_line_message(results, date_str)
            success = send_to_line(line_msg, strategy="04")
            if success:
                print("[✓] LINE 戰報推播發送成功！")
            else:
                print("[!] LINE 推播發送未完成，請檢查 .env 設定。")
        except Exception as e:
            print(f"[X] LINE 發送發生異常: {e}")

    return results

def main():
    parser = argparse.ArgumentParser(description="台股可轉債 (CB) 定價伏擊與區間博弈旗艦選股器 V2.0")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYY/MM/DD)")
    parser.add_argument("--export", type=str, default="result.csv", help="匯出 CSV 檔名 (預設 result.csv)")
    parser.add_argument("--line", action="store_true", help="發送 LINE 推播")
    args = parser.parse_args()

    run_screener(date_str=args.date, export_path=args.export, send_line=args.line)

if __name__ == "__main__":
    main()
