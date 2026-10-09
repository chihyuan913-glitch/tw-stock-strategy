#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股法人籌碼出貨破線做空選股器 (Institutional Dumping & Downtrend Breakdown Short Screener)

【核心做空策略邏輯】
一、籌碼面（主力資金出貨追蹤）：
  1. 外資或投信近 3 個交易日累計賣超大於 1,000 張 (<-1,000,000 股)。
  2. 法人賣超張數佔當日總成交量比例大於 10% (賣方資金主導砸盤)。
  3. 近 5 日法人籌碼呈現淨賣超 (籌碼渙散、主力提款)。

二、技術面（空頭趨勢與流動性）：
  4. 收盤價跌破 20 日均線（月線），且月線斜率維持向下 (MA20_curr < MA20_prev，均線蓋頭反壓助跌)。
  5. 近 5 日平均成交量大於 1,000 張，確保融券/借券賣出具備充足流動性。

三、風險與做空濾網（防軋空安全邊界）：
  6. 收盤價與 20 日均線負乖離率介於 0% 到 -8% 之間 (-8.0% <= Bias <= 0%)，
     確保於「起跌破線區或反彈遭遇月線反壓區」進場，避免在跌幅已大、負乖離過大處追空被軋反彈。
  7. 當日 K 棒無長下影線（下影線長度需小於實體 K 棒的一半），排除低檔有主力強撐或轉折紅K。

四、操盤實戰四大價位：
  - 空單進場：現價 (或反彈至月線反壓區分批佈空)
  - 加空價位：跌破今日低點續跌確認
  - 停利回補：TP1 月線負乖離 -12% ｜ TP2 月線負乖離 -20%
  - 停損回補：站回月線 +2% (或突破當日高點) 嚴格無條件停損！
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

# 匯入 LINE 發送模組
from line_sender import send_to_line

# 設定標準輸出編碼為 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def get_recent_trading_dates(n_days=5, max_lookback=20):
    """取得最近 n_days 個證交所實際開盤交易日 (YYYYMMDD)"""
    today = datetime.date.today()
    found_dates = []
    
    for i in range(max_lookback):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5:
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
    """批次抓取三大法人買賣超 (T86)"""
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
    """抓取每日收盤行情 (MI_INDEX)"""
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


def screen_short_stocks(date_str=None, min_vol_lots=1000, 
                        max_neg_bias_pct=-8.0, inst_sell_ratio_pct=10.0,
                        inst_3d_threshold=1000):
    """
    執行完整做空量化篩選
    """
    if date_str:
        all_dates = get_recent_trading_dates(n_days=10)
        if date_str in all_dates:
            idx = all_dates.index(date_str)
            trading_dates = all_dates[idx:idx+5]
        else:
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
            print("[X] 無法取得交易日資料，請檢查網路連線。")
            return []

    latest_date = trading_dates[0]
    dates_3d = trading_dates[:min(3, len(trading_dates))]
    dates_5d = trading_dates[:min(5, len(trading_dates))]
    
    print(f"[*] 做空篩選基準日：{latest_date}（近3日賣超檢測：{dates_3d}，近5日：{dates_5d}）")

    chips_history = fetch_institutional_data(dates_5d)
    quotes = fetch_daily_quotes(latest_date)
    
    if not quotes or not chips_history:
        print("[X] 資料獲取不完整，終止篩選。")
        return []

    print(f"[*] 今日取得 {len(quotes)} 檔行情，{len(chips_history)} 檔籌碼記錄。")
    print("[*] 執行第一階段籌碼出貨面與 K 棒形態快篩...")

    preliminary_candidates = []
    preliminary_meta = {}

    for code, q in quotes.items():
        if code not in chips_history or latest_date not in chips_history[code]:
            continue
            
        c_today = chips_history[code][latest_date]
        
        # 近 3 日外資累計與投信累計賣超 (股數)
        f_3d_shares = sum(chips_history[code][d]['foreign'] for d in dates_3d if d in chips_history[code])
        t_3d_shares = sum(chips_history[code][d]['trust'] for d in dates_3d if d in chips_history[code])
        
        # 做空條件 1: 外資或投信近 3 個交易日累計賣超大於 1,000 張 (<= -1,000,000 股)
        threshold_sell_shares = -inst_3d_threshold * 1000
        cond_inst_3d_sell = (f_3d_shares <= threshold_sell_shares) or (t_3d_shares <= threshold_sell_shares)
        if not cond_inst_3d_sell:
            continue
            
        # 做空條件 2: 當日法人賣超佔總成交量比例大於 10%
        inst_today_shares = c_today['total']
        if q['volume'] <= 0 or inst_today_shares >= 0:
            continue
            
        inst_sell_ratio = (abs(inst_today_shares) / q['volume']) * 100.0
        if inst_sell_ratio < inst_sell_ratio_pct:
            continue

        # 做空風險濾網: 當日 K 棒無長下影線（排除低檔強烈承接）
        # 實體 K 棒長度 = abs(Close - Open)
        # 下影線長度 = min(Open, Close) - Low
        body = abs(q['close'] - q['open'])
        lower_shadow = min(q['open'], q['close']) - q['low']
        
        if body > 0.05:
            cond_shadow = (lower_shadow < 0.5 * body)
        else:
            cond_shadow = (lower_shadow <= (q['close'] * 0.005))
            
        if not cond_shadow:
            continue

        # 近 5 日法人累計淨賣超 (< 0)
        total_5d_shares = sum(chips_history[code][d]['total'] for d in dates_5d if d in chips_history[code])
        if total_5d_shares >= 0:
            continue

        preliminary_candidates.append(code)
        preliminary_meta[code] = {
            'quote': q,
            'f_3d_lots': f_3d_shares // 1000,
            't_3d_lots': t_3d_shares // 1000,
            'inst_today_lots': inst_today_shares // 1000,
            'total_5d_lots': total_5d_shares // 1000,
            'inst_sell_ratio': round(inst_sell_ratio, 1),
            'body': round(body, 2),
            'lower_shadow': round(lower_shadow, 2)
        }

    print(f"[*] 通過籌碼大額出貨與無下影線初篩標的共 {len(preliminary_candidates)} 檔。")
    if not preliminary_candidates:
        print("[-] 今日無符合籌碼出貨與下影線門檻之標的。")
        return []

    # 第二階段技術面濾網：跌破 20MA、月線向下、5日均量>1000張、負乖離在 0%~-8% 甜蜜做空區
    print("[*] 執行第二階段空頭技術面濾網（下載日K計算月線下彎與負乖離率）...")
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
            
            # 做空條件 4: 收盤價跌破 20 日均線（月線），且月線斜率維持向下 (助跌蓋頭反壓)
            curr_close = float(q['close'])
            is_below_ma20 = (curr_close <= ma20_curr)
            is_slope_down = (ma20_curr < ma20_prev)
            
            if not (is_below_ma20 and is_slope_down):
                continue

            # 做空條件 5: 近 5 日平均成交量大於 1,000 張 (確保流動性)
            v5_avg_shares = float(vol_series.rolling(5).mean().iloc[-1])
            v5_avg_lots = v5_avg_shares / 1000.0
            if v5_avg_lots < min_vol_lots:
                continue

            # 做空條件 6 (防軋空安全進場濾網):
            # 收盤價與 20 日均線負乖離率介於 0% 到 -8% 之間 (起跌破線區，絕不追空過深標的)
            bias_ma20_pct = ((curr_close - ma20_curr) / ma20_curr) * 100.0
            if not (max_neg_bias_pct <= bias_ma20_pct <= 0.0):
                continue

            # -------------------------------------------------------------
            # 策略實戰做空四大價位試算
            # -------------------------------------------------------------
            # 1. 空單進場：現價或反彈至月線反壓區
            short_entry = round(curr_close, 2)
            # 2. 加空價位：破當日低點 1 檔續跌 (或現價 -3%)
            add_short = round(min(q['low'] * 0.995, curr_close * 0.97), 2)
            # 3. 停利回補：
            #    TP1: 月線負乖離 -12% 短波段滿足點 (補回 1/3 ~ 1/2)
            #    TP2: 月線負乖離 -20% 波段滿足點
            tp1_cover = round(ma20_curr * 0.88, 2)
            tp2_cover = round(ma20_curr * 0.80, 2)
            # 4. 停損回補：站回月線 +2% (或突破當日高點) 嚴格無條件停損回補！
            high_ref = q['high'] if q['high'] > 0 else curr_close * 1.02
            sl_cover = round(max(ma20_curr * 1.02, high_ref * 1.01), 2)

            results.append({
                '證券代號': code,
                '證券名稱': q['name'],
                '收盤價': round(curr_close, 2),
                '空單進場': short_entry,
                '加空價位': add_short,
                '停利TP1': tp1_cover,
                '停利TP2': tp2_cover,
                '停損回補': sl_cover,
                '月線(20MA)': round(ma20_curr, 2),
                '月線負乖離%': round(bias_ma20_pct, 2),
                '當日成交量(張)': q['volume'] // 1000,
                '5日均量(張)': int(v5_avg_lots),
                '法人賣超佔比%': meta['inst_sell_ratio'],
                '外資3日賣超(張)': meta['f_3d_lots'],
                '投信3日賣超(張)': meta['t_3d_lots'],
                '當日法人賣超(張)': meta['inst_today_lots'],
                '5日法人累計(張)': meta['total_5d_lots'],
                'K棒實體': meta['body'],
                '下影線': meta['lower_shadow']
            })
            
        except Exception as e:
            continue

    return results


def main():
    parser = argparse.ArgumentParser(description="台股法人籌碼出貨破線做空選股策略")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYYMMDD，預設為最新交易日)")
    parser.add_argument("--min-vol", type=int, default=1000, help="最低5日平均成交量(張)，預設 1000 張")
    parser.add_argument("--max-neg-bias", type=float, default=-8.0, help="20MA 負乖離率下限百分比(%%)，預設 -8.0%% (避免追空過深)")
    parser.add_argument("--sell-ratio", type=float, default=10.0, help="法人賣超佔成交量低標(%%)，預設 10.0%%")
    parser.add_argument("--sell-3d", type=int, default=1000, help="外資或投信近3日累計賣超門檻(張)，預設 1000 張")
    parser.add_argument("--export", type=str, default=None, help="匯出報表檔名 (.csv 或 .md)")
    parser.add_argument("--line", action="store_true", help="發送選股通知至 LINE Notify/Bot")

    args = parser.parse_args()

    results = screen_short_stocks(
        date_str=args.date,
        min_vol_lots=args.min_vol,
        max_neg_bias_pct=args.max_neg_bias,
        inst_sell_ratio_pct=args.sell_ratio,
        inst_3d_threshold=args.sell_3d
    )

    if not results:
        print("\n[!] 經檢核，所選條件下無符合所有做空複合條件的個股。")
        return

    df_res = pd.DataFrame(results)
    # 依照「法人賣超佔比%」與「當日法人賣超(張)」排序 (砸盤力道最強者排前)
    df_res = df_res.sort_values(by=["法人賣超佔比%", "當日法人賣超(張)"], ascending=[False, True]).reset_index(drop=True)

    print("\n" + "="*105)
    print(f"🎯【台股法人出貨破線做空】選股結果清單（共 {len(df_res)} 檔）")
    print("條件符合：外資/投信3日賣超>1000張 | 法人佔比>10% | 跌破20MA且斜率向下 | 5日均量>1000張 | 負乖離[-8%~0%] | 無長下影線")
    print("="*105)
    print(df_res.to_string(index=False))
    print("="*105)

    if args.export:
        if args.export.endswith('.csv'):
            df_res.to_csv(args.export, index=False, encoding='utf-8-sig')
            print(f"[✓] 已成功匯出 CSV 報表至: {args.export}")
        elif args.export.endswith('.md'):
            with open(args.export, 'w', encoding='utf-8') as f:
                f.write(f"# 🎯 台股法人出貨破線做空選股日報\n\n")
                f.write(f"- 產生時間：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"- 篩選標準：外資/投信3日累計賣超>1000張、法人賣超佔比>10%、跌破月線且斜率向下、5日均量>1000張、負乖離[-8%~0%]、無長下影線\n\n")
                cols = list(df_res.columns)
                header_line = "| " + " | ".join(cols) + " |\n"
                separator_line = "| " + " | ".join(["---"] * len(cols)) + " |\n"
                f.write(header_line)
                f.write(separator_line)
                for _, row in df_res.iterrows():
                    row_line = "| " + " | ".join(str(row[c]) for c in cols) + " |\n"
                    f.write(row_line)
                f.write("\n")
            print(f"[✓] 已成功匯出 Markdown 報表至: {args.export}")

    if args.line:
        try:
            from line_sender import send_to_line
            target_date = args.date or (results[0]['證券代號'] and datetime.date.today().strftime('%Y%m%d')) or "今日"
            msg_lines = [
                "📉【台股做空選股日報】法人出貨破線轉弱標的",
                f"📅 基準日期：{target_date}",
                f"🎯 空方濾網：外資投信3日大賣>1000張｜法人賣超佔比>10%｜跌破月線且下彎｜負乖離[-8%~0%]起跌區｜無長下影線",
                f"🔥 今日篩選出 {len(df_res)} 檔空方發動焦點標的（附操盤四價位）：",
                "─────────────────────"
            ]
            for i, r in enumerate(df_res.head(8).to_dict(orient='records'), 1):
                msg_lines.append(
                    f"{i}. 📍 {r['證券代號']} {r['證券名稱']} (現價 {r['收盤價']}元)\n"
                    f"   🟢 空單進場：{r['收盤價']} 元 (月線 {r['月線(20MA)']}，負乖離 {r['月線負乖離%']}%)\n"
                    f"   🔵 加空價位：{r['加空價位']} 元 ｜ 🛑 停損回補：{r['停損回補']} 元\n"
                    f"   🔴 停利補回：TP1 {r['停利TP1']} 元 ｜ TP2 {r['停利TP2']} 元\n"
                    f"   🏦 籌碼：賣超佔比 {r['法人賣超佔比%']}% (外資3日{r['外資3日賣超(張)']:,}張, 投信3日{r['投信3日賣超(張)']:,}張)\n"
                    "─────────────────────"
                )
            if len(df_res) > 8:
                msg_lines.append(f"(其餘 {len(df_res)-8} 檔請查看 short_result.csv 報表)")
            msg_lines.append("💡 做空紀律：站回月線反壓無條件嚴格停損回補，絕不死抗被軋！")
            
            print("[*] 正在發送做空選股清單至 LINE...")
            send_to_line("\n".join(msg_lines))
        except Exception as e:
            print(f"[!] 發送 LINE 選股通知失敗: {e}")


if __name__ == '__main__':
    main()
