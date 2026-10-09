#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股法人籌碼集中起漲選股器 (Institutional Momentum & Chip Concentration Breakout Screener)

【核心篩選條件】
一、籌碼面（主力資金追蹤）：
  1. 外資或投信近 3 個交易日累計買超大於 1,000 張。
  2. 法人買超張數佔當日總成交量比例大於 10%。
  3. 近 5 日主力籌碼呈現淨買超且籌碼集中（少數買方吃下多數賣方籌碼）。

二、技術面（趨勢與流動性）：
  4. 收盤價站上 20 日均線（月線），且月線斜率維持向上 (MA20_today > MA20_yesterday)。
  5. 近 5 日平均成交量大於 1,000 張，確保進出具備基本流動性。

三、風險與進場濾網：
  6. 收盤價與 20 日均線正乖離率小於 8%，確保於起漲區或均線支撐區進場，避免追高被套。
  7. 當日 K 棒無爆量長上影線（上影線長度需小於實體 K 棒的一半），排除隔日沖主力拉高出貨或假突破。
"""

import sys
import os
import argparse
import datetime
import json
import urllib.request
import pandas as pd
import numpy as np
import yfinance as yf

# 加入專案根目錄至 sys.path 以便共用模組 (如 line_sender)
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# 設定標準輸出編碼為 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def get_recent_trading_dates(n_days=5, max_lookback=20):
    """
    從證交所 API 檢測並獲取最近 n_days 個實際有開盤交易的日期 (YYYYMMDD)
    """
    today = datetime.date.today()
    found_dates = []
    
    for i in range(max_lookback):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5:  # 週六與週日休市
            continue
        d_str = d.strftime('%Y%m%d')
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('stat') == 'OK' and len(data.get('data', [])) > 0:
                    found_dates.append(d_str)
                    if len(found_dates) == n_days:
                        break
        except Exception:
            pass
            
    return found_dates


def fetch_institutional_data(dates):
    """
    批次抓取指定日期清單的三大法人買賣超 (T86)
    回傳: chips_history = {code: {date: {'foreign': int, 'trust': int, 'dealer': int, 'total': int, 'name': str}}}
    """
    print(f"[*] 正在抓取近 {len(dates)} 個交易日的三大法人籌碼數據: {dates} ...")
    chips_history = {}
    
    for d_str in dates:
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('stat') == 'OK':
                    for row in data.get('data', []):
                        code = row[0].strip()
                        if len(code) == 4 and code.isdigit():
                            try:
                                name = row[1].strip()
                                foreign = int(row[4].replace(',', ''))
                                trust = int(row[10].replace(',', ''))
                                dealer = int(row[11].replace(',', ''))
                                total = int(row[18].replace(',', ''))
                                
                                if code not in chips_history:
                                    chips_history[code] = {}
                                chips_history[code][d_str] = {
                                    'name': name,
                                    'foreign': foreign,
                                    'trust': trust,
                                    'dealer': dealer,
                                    'total': total
                                }
                            except (ValueError, IndexError):
                                pass
        except Exception as e:
            print(f"[!] 抓取 {d_str} 法人數據失敗: {e}")
            
    return chips_history


def fetch_daily_quotes(date_str):
    """
    抓取指定日期的每日收盤行情 (MI_INDEX)，包含開高低收與成交量
    """
    print(f"[*] 正在連線證交所抓取 {date_str} 盤後行情 (OHLCV)...")
    url_mi = f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=json'
    req_mi = urllib.request.Request(url_mi, headers={'User-Agent': 'Mozilla/5.0'})
    quotes = {}
    
    try:
        with urllib.request.urlopen(req_mi, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for t in data.get('tables', []):
                if '每日收盤行情' in t.get('title', ''):
                    for row in t.get('data', []):
                        code = row[0].strip()
                        if len(code) == 4 and code.isdigit():
                            try:
                                name = row[1].strip()
                                vol = int(row[2].replace(',', ''))
                                close_str = row[8].replace(',', '').strip()
                                open_str = row[5].replace(',', '').strip()
                                high_str = row[6].replace(',', '').strip()
                                low_str = row[7].replace(',', '').strip()
                                
                                if close_str != '--' and open_str != '--' and high_str != '--':
                                    quotes[code] = {
                                        'name': name,
                                        'volume': vol,
                                        'open': float(open_str),
                                        'high': float(high_str),
                                        'low': float(low_str),
                                        'close': float(close_str)
                                    }
                            except (ValueError, IndexError):
                                pass
    except Exception as e:
        print(f"[!] 抓取行情失敗: {e}")
        
    return quotes


def screen_institutional_momentum(date_str=None, min_vol_lots=1000, 
                                  max_bias_pct=8.0, inst_ratio_pct=10.0,
                                  inst_3d_threshold=1000):
    """
    執行完整多條件量化篩選
    """
    # 1. 取得交易日期清單 (最新日 + 前 4 個交易日，共 5 日)
    if date_str:
        # 使用者指定單一日期，推算往前日期
        trading_dates = [date_str]
        # 仍嘗試找前幾日
        all_dates = get_recent_trading_dates(n_days=10)
        if date_str in all_dates:
            idx = all_dates.index(date_str)
            trading_dates = all_dates[idx:idx+5]
        else:
            # 依工作日往前推算 5 日
            dt = datetime.datetime.strptime(date_str, '%Y%m%d').date()
            trading_dates = []
            for i in range(10):
                d = dt - datetime.timedelta(days=i)
                if d.weekday() < 5:
                    trading_dates.append(d.strftime('%Y%m%d'))
                if len(trading_dates) == 5:
                    break
    else:
        trading_dates = get_recent_trading_dates(n_days=5)
        if not trading_dates:
            print("[X] 無法取得最新交易日期，請檢查網路連線或手動指定 --date YYYYMMDD")
            return []

    latest_date = trading_dates[0]
    dates_3d = trading_dates[:min(3, len(trading_dates))]
    dates_5d = trading_dates[:min(5, len(trading_dates))]
    
    print(f"[*] 執行基準日：{latest_date}（近 3 日：{dates_3d}，近 5 日：{dates_5d}）")

    # 2. 獲取籌碼與行情
    chips_history = fetch_institutional_data(dates_5d)
    quotes = fetch_daily_quotes(latest_date)
    
    if not quotes or not chips_history:
        print("[X] 資料獲取不完整，終止篩選。")
        return []

    print(f"[*] 今日共取得 {len(quotes)} 檔個股行情，{len(chips_history)} 檔個股籌碼記錄。")

    # 3. 第一階段快篩：
    #    (1) 當日成交量初步門檻
    #    (2) 外資或投信近 3 個交易日累計買超 > 1,000 張 (1,000,000 股)
    #    (3) 法人買超張數佔當日總成交量比例 > 10%
    #    (4) 當日 K 棒上影線長度 < 實體長度的一半
    print("[*] 執行第一階段籌碼面與 K 棒形態快篩...")
    preliminary_candidates = []
    preliminary_meta = {}

    for code, q in quotes.items():
        if code not in chips_history or latest_date not in chips_history[code]:
            continue
            
        c_today = chips_history[code][latest_date]
        
        # 近 3 日外資累計與投信累計 (股數)
        f_3d_shares = sum(chips_history[code][d]['foreign'] for d in dates_3d if d in chips_history[code])
        t_3d_shares = sum(chips_history[code][d]['trust'] for d in dates_3d if d in chips_history[code])
        
        # 條件 1: 外資或投信近 3 個交易日累計買超 > 1,000 張
        threshold_shares = inst_3d_threshold * 1000
        cond_inst_3d = (f_3d_shares >= threshold_shares) or (t_3d_shares >= threshold_shares)
        if not cond_inst_3d:
            continue
            
        # 條件 2: 法人買超張數佔當日總成交量比例大於 10%
        # 法人當日淨買超股數
        inst_today_shares = c_today['total']
        if q['volume'] <= 0 or inst_today_shares <= 0:
            continue
            
        inst_vol_ratio = (inst_today_shares / q['volume']) * 100.0
        if inst_vol_ratio < inst_ratio_pct:
            continue

        # 條件 7 (風險濾網): 當日 K 棒無爆量長上影線（上影線長度需小於實體 K 棒的一半）
        # 實體 K 棒長度 = abs(Close - Open)
        # 上影線長度 = High - max(Open, Close)
        body = abs(q['close'] - q['open'])
        upper_shadow = q['high'] - max(q['open'], q['close'])
        
        # 避免十字星或平盤除以零，當實體極小時要求上影線不得超過收盤價之 0.5%
        if body > 0.05:
            cond_shadow = (upper_shadow < 0.5 * body)
        else:
            cond_shadow = (upper_shadow <= (q['close'] * 0.005))
            
        if not cond_shadow:
            continue

        # 近 5 日主力法人累計買超
        total_5d_shares = sum(chips_history[code][d]['total'] for d in dates_5d if d in chips_history[code])
        
        preliminary_candidates.append(code)
        preliminary_meta[code] = {
            'quote': q,
            'f_3d_lots': f_3d_shares // 1000,
            't_3d_lots': t_3d_shares // 1000,
            'inst_today_lots': inst_today_shares // 1000,
            'total_5d_lots': total_5d_shares // 1000,
            'inst_ratio': round(inst_vol_ratio, 1),
            'body': round(body, 2),
            'upper_shadow': round(upper_shadow, 2)
        }

    print(f"[*] 通過籌碼累積與 K 棒濾網之初篩標的共 {len(preliminary_candidates)} 檔。")
    if not preliminary_candidates:
        print("[-] 今日無符合籌碼 3 日買超 > 1000 張且佔成交量 > 10% 之標的。")
        return []

    # 4. 第二階段技術面精細過濾 (20MA 站上、月線向上、5日均量 > 1000張、正乖離 < 8%)
    print("[*] 執行第二階段技術面濾網（下載歷史日K計算月線與5日均量）...")
    tickers = [f"{c}.TW" for c in preliminary_candidates]
    try:
        hist_data = yf.download(tickers, period='3mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 歷史數據下載失敗: {e}")
        return []

    results = []

    for code in preliminary_candidates:
        t_key = f"{code}.TW"
        meta = preliminary_meta[code]
        q = meta['quote']
        
        try:
            if len(preliminary_candidates) == 1:
                df = hist_data.dropna()
            else:
                if t_key in hist_data.columns.levels[0]:
                    df = hist_data[t_key].dropna()
                else:
                    continue

            if 'Close' not in df or len(df['Close']) < 21:
                continue

            close_series = df['Close']
            vol_series = df['Volume']
            
            # 月線 (20MA)
            ma20 = close_series.rolling(20).mean()
            ma20_curr = float(ma20.iloc[-1])
            ma20_prev = float(ma20.iloc[-2])
            
            # 條件 4: 收盤價站上 20 日均線（月線），且月線斜率維持向上
            curr_close = float(q['close'])
            is_above_ma20 = (curr_close >= ma20_curr)
            is_slope_up = (ma20_curr > ma20_prev)
            
            if not (is_above_ma20 and is_slope_up):
                continue

            # 條件 5: 近 5 日平均成交量大於 1,000 張
            # yfinance Volume 單位為股
            v5_avg_shares = float(vol_series.rolling(5).mean().iloc[-1])
            v5_avg_lots = v5_avg_shares / 1000.0
            if v5_avg_lots < min_vol_lots:
                continue

            # 條件 6 (風險進場濾網): 收盤價與 20 日均線正乖離率小於 8%
            bias_ma20_pct = ((curr_close - ma20_curr) / ma20_curr) * 100.0
            # 站上月線且在 +8% 乖離之內 (0% <= Bias < 8%)
            if not (0.0 <= bias_ma20_pct < max_bias_pct):
                continue

            # 條件 3 (籌碼集中度): 近 5 日法人累計大於 0
            if meta['total_5d_lots'] <= 0:
                continue

            # -------------------------------------------------------------
            # 策略實戰操盤四大價位試算 (進場、加碼、停利、停損)
            # -------------------------------------------------------------
            entry_p = round(curr_close, 2)
            addon_raw = max(q['high'] * 1.005, curr_close * 1.03)
            addon_p = round(min(addon_raw, ma20_curr * 1.10), 2)
            tp1_p = round(ma20_curr * 1.12, 2)
            tp2_p = round(ma20_curr * 1.20, 2)
            sl_p = round(max(ma20_curr * 0.98, q['low'] * 0.99), 2)

            results.append({
                '證券代號': code,
                '證券名稱': q['name'],
                '收盤價': round(curr_close, 2),
                '進場參考': entry_p,
                '加碼價位': addon_p,
                '停利TP1': tp1_p,
                '停利TP2': tp2_p,
                '停損價位': sl_p,
                '月線(20MA)': round(ma20_curr, 2),
                '月線乖離%': round(bias_ma20_pct, 2),
                '當日成交量(張)': q['volume'] // 1000,
                '5日均量(張)': int(v5_avg_lots),
                '法人買超佔比%': meta['inst_ratio'],
                '外資3日買超(張)': meta['f_3d_lots'],
                '投信3日買超(張)': meta['t_3d_lots'],
                '當日法人買超(張)': meta['inst_today_lots'],
                '5日法人累計(張)': meta['total_5d_lots'],
                'K棒實體': meta['body'],
                '上影線': meta['upper_shadow']
            })
            
        except Exception as e:
            continue

    return results


def main():
    parser = argparse.ArgumentParser(description="台股法人籌碼集中起漲選股策略")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYYMMDD，預設為最新交易日)")
    parser.add_argument("--min-vol", type=int, default=1000, help="最低5日平均成交量(張)，預設 1000 張")
    parser.add_argument("--max-bias", type=float, default=8.0, help="20MA 正乖離上限百分比(%%)，預設 8.0%%")
    parser.add_argument("--inst-ratio", type=float, default=10.0, help="法人買超佔當日成交量低標(%%)，預設 10.0%%")
    parser.add_argument("--inst-3d", type=int, default=1000, help="外資或投信近3日累計買超門檻(張)，預設 1000 張")
    parser.add_argument("--export", type=str, default=None, help="匯出報表檔名 (.csv 或 .md)")
    parser.add_argument("--line", action="store_true", help="發送選股通知至 LINE Notify/Bot")

    args = parser.parse_args()

    results = screen_institutional_momentum(
        date_str=args.date,
        min_vol_lots=args.min_vol,
        max_bias_pct=args.max_bias,
        inst_ratio_pct=args.inst_ratio,
        inst_3d_threshold=args.inst_3d
    )

    if not results:
        print("\n[!] 經檢核，所選條件下無符合所有複合條件的個股。")
        return

    df_res = pd.DataFrame(results)
    # 依照「法人買超佔比%」與「當日法人買超(張)」排序
    df_res = df_res.sort_values(by=["法人買超佔比%", "當日法人買超(張)"], ascending=[False, False]).reset_index(drop=True)

    print("\n" + "="*95)
    print(f"🎯【台股法人籌碼集中起漲】選股結果清單（共 {len(df_res)} 檔）")
    print("條件符合：外資/投信3日買超>1000張 | 法人佔比>10% | 站上20MA且斜率向上 | 5日均量>1000張 | 正乖離<8% | 無長上影線")
    print("="*95)
    print(df_res.to_string(index=False))
    print("="*95)

    if args.export:
        export_path = args.export
        if not os.path.isabs(export_path) and os.path.dirname(export_path) == "":
            export_path = os.path.join(os.path.dirname(__file__), export_path)
        if export_path.endswith('.csv'):
            df_res.to_csv(export_path, index=False, encoding='utf-8-sig')
            print(f"[✓] 已成功匯出 CSV 報表至: {export_path}")
        elif export_path.endswith('.md'):
            with open(export_path, 'w', encoding='utf-8') as f:
                f.write(f"# 🎯 台股法人籌碼集中起漲選股日報\n\n")
                f.write(f"- 產生時間：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"- 篩選標準：外資/投信3日累計>1000張、法人佔比>10%、站上月線且斜率向上、5日均量>1000張、正乖離<8%、無長上影線\n\n")
                cols = list(df_res.columns)
                header_line = "| " + " | ".join(cols) + " |\n"
                separator_line = "| " + " | ".join(["---"] * len(cols)) + " |\n"
                f.write(header_line)
                f.write(separator_line)
                for _, row in df_res.iterrows():
                    row_line = "| " + " | ".join(str(row[c]) for c in cols) + " |\n"
                    f.write(row_line)
                f.write("\n")
            print(f"[✓] 已成功匯出 Markdown 報表至: {export_path}")

    if args.line:
        try:
            from line_sender import send_to_line
            target_date = args.date or (results[0]['證券代號'] and datetime.date.today().strftime('%Y%m%d')) or "今日"
            msg_lines = [
                "📊【台股盤後選股】法人籌碼集中起漲日報",
                f"📅 基準日期：{target_date}",
                f"🎯 核心濾網：外資投信3日>1000張｜法人佔比>10%｜站上月線且上揚｜乖離<8%｜無長上影線",
                f"🔥 今日共篩選出 {len(df_res)} 檔起漲焦點股（附進場/加碼/停利/停損價位）：",
                "─────────────────────"
            ]
            for i, r in enumerate(df_res.head(8).to_dict(orient='records'), 1):
                msg_lines.append(
                    f"{i}. 📍 {r['證券代號']} {r['證券名稱']} (現價 {r['收盤價']}元)\n"
                    f"   🟢 進場：{r['收盤價']} 元 (月線 {r['月線(20MA)']}，乖離 +{r['月線乖離%']}%)\n"
                    f"   🔵 加碼：{r['加碼價位']} 元 ｜ 🛑 停損：{r['停損價位']} 元\n"
                    f"   🔴 停利：TP1 {r['停利TP1']} 元 ｜ TP2 {r['停利TP2']} 元\n"
                    f"   🏦 籌碼：佔比 {r['法人買超佔比%']}% (外資+{r['外資3日買超(張)']:,}張, 投信+{r['投信3日買超(張)']:,}張)\n"
                    "─────────────────────"
                )
            if len(df_res) > 8:
                msg_lines.append(f"(其餘 {len(df_res)-8} 檔完整名單請查看電腦 CSV 報表)")
            msg_lines.append("💡 交易紀律：起漲安全區進場，跌破停損價無條件執行！")
            
            print("[*] 正在發送選股清單至 LINE (策略 02)...")
            send_to_line("\n".join(msg_lines), strategy="02")
        except Exception as e:
            print(f"[!] 發送 LINE 選股通知失敗: {e}")


if __name__ == '__main__':
    main()
