#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股選股策略：布林通道下軌 + 三大法人逆勢買超選股器
條件：
1. 日成交量 >= 1000 張 (1,000,000 股)
2. 股價位於布林通道下軌 1% 位置或是跌破 5% 之內 (-5.0% <= (Close - LB) / LB <= +1.0%)
3. 籌碼面：三大主力法人逆勢買超 (合計買賣超 > 0)，且主力 (投信/外資) 未大量拋售
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

# 設定標準輸出編碼為 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def get_latest_trading_date(max_days_back=15):
    """取得最近有證交所交易資料的日期 (YYYYMMDD)"""
    today = datetime.date.today()
    for i in range(max_days_back):
        d = today - datetime.timedelta(days=i)
        # 排除週末
        if d.weekday() >= 5:
            continue
        d_str = d.strftime('%Y%m%d')
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('stat') == 'OK' and len(data.get('data', [])) > 0:
                    return d_str
        except Exception:
            pass
    return None

def fetch_twse_data(date_str):
    """抓取證交所三大法人買賣超 (T86) 與每日收盤行情 (MI_INDEX)"""
    print(f"[*] 正在連線台灣證券交易所抓取 {date_str} 交易數據...")
    
    # 1. 抓取三大法人買賣超 (T86)
    url_t86 = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json'
    req_t86 = urllib.request.Request(url_t86, headers={'User-Agent': 'Mozilla/5.0'})
    chips = {}
    try:
        with urllib.request.urlopen(req_t86, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get('stat') == 'OK':
                for row in data.get('data', []):
                    code = row[0].strip()
                    # 篩選四位數普通股
                    if len(code) == 4 and code.isdigit():
                        try:
                            foreign = int(row[4].replace(',', ''))
                            trust = int(row[10].replace(',', ''))
                            dealer = int(row[11].replace(',', ''))
                            total = int(row[18].replace(',', ''))
                            chips[code] = {
                                'foreign': foreign,
                                'trust': trust,
                                'dealer': dealer,
                                'total': total
                            }
                        except ValueError:
                            pass
    except Exception as e:
        print(f"[!] 抓取三大法人資料失敗: {e}")
        return {}, {}

    # 2. 抓取每日收盤行情 (MI_INDEX)
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
                                if close_str != '--':
                                    close = float(close_str)
                                    quotes[code] = {
                                        'name': name,
                                        'volume': vol,
                                        'close': close
                                    }
                            except (ValueError, IndexError):
                                pass
    except Exception as e:
        print(f"[!] 抓取每日行情資料失敗: {e}")
        return {}, {}

    return quotes, chips

def screen_stocks(date_str=None, min_volume_lots=1000, 
                  lower_dist_max=1.0, lower_dist_min=-5.0, 
                  trust_max_sell_lots=500):
    """
    執行策略選股核心流程
    """
    if not date_str:
        date_str = get_latest_trading_date()
        if not date_str:
            print("[X] 無法取得最新交易日，請檢查網路連線或手動指定 --date YYYYMMDD")
            return []

    quotes, chips = fetch_twse_data(date_str)
    if not quotes or not chips:
        print("[X] 數據獲取不完整，終止篩選。")
        return []

    print(f"[*] 成功讀取 {len(quotes)} 檔個股行情與 {len(chips)} 檔個股籌碼資料。")
    print(f"[*] 第一階段過濾：成交量 >= {min_volume_lots} 張，且三大法人買賣超 > 0...")

    candidate_codes = []
    candidate_meta = {}
    min_vol_shares = min_volume_lots * 1000

    for code, q in quotes.items():
        if q['volume'] >= min_vol_shares and code in chips:
            c = chips[code]
            # 條件 1: 日成交量 >= 1000 張
            # 條件 3: 三大主力法人合計買超 > 0
            # 條件 3 (副條件): 投信未大量拋售 (投信賣超不超過指定張數，避免土洋對作中投信斷頭逃命)
            if c['total'] > 0 and c['trust'] >= -(trust_max_sell_lots * 1000):
                candidate_codes.append(code)
                candidate_meta[code] = {**q, **c}

    print(f"[*] 通過量能與籌碼初篩個股共 {len(candidate_codes)} 檔。")
    if not candidate_codes:
        print("[-] 今日無個股符合成交量與法人買超門檻。")
        return []

    # 批次下載歷史日K線計算布林通道
    print(f"[*] 第二階段計算：使用 yfinance 批次下載歷史行情計算 20 日布林通道...")
    tickers = [f"{c}.TW" for c in candidate_codes]
    
    # 下載近 2 個月日K數據
    try:
        hist_data = yf.download(tickers, period='2mo', progress=False, group_by='ticker')
    except Exception as e:
        print(f"[!] 下載歷史K線失敗: {e}")
        return []

    results = []

    for code in candidate_codes:
        t_key = f"{code}.TW"
        meta = candidate_meta[code]
        try:
            # 處理 yfinance 單檔或多檔回傳結構
            if len(candidate_codes) == 1:
                df = hist_data
            else:
                if t_key in hist_data.columns.levels[0]:
                    df = hist_data[t_key]
                else:
                    continue

            if 'Close' not in df or df['Close'].dropna().empty:
                continue

            close_series = df['Close'].dropna()
            if len(close_series) < 20:
                continue

            # 計算 20MA 與標準差
            ma20 = close_series.rolling(20).mean().iloc[-1]
            std20 = close_series.rolling(20).std(ddof=0).iloc[-1]
            lower_band = ma20 - 2.0 * std20
            upper_band = ma20 + 2.0 * std20
            
            curr_close = meta['close']
            
            # 計算距離布林下軌百分比
            # dist_pct = (收盤價 - 下軌) / 下軌 * 100
            dist_pct = (curr_close - lower_band) / lower_band * 100.0

            # 條件 2: 位於布林下軌 1% 之內或是跌破 5% 之內
            if lower_dist_min <= dist_pct <= lower_dist_max:
                results.append({
                    '證券代號': code,
                    '證券名稱': meta['name'],
                    '收盤價': round(curr_close, 2),
                    '布林下軌': round(float(lower_band), 2),
                    '布林中軌(20MA)': round(float(ma20), 2),
                    '布林上軌': round(float(upper_band), 2),
                    '距下軌%': round(float(dist_pct), 2),
                    '日成交量(張)': meta['volume'] // 1000,
                    '外資買超(張)': meta['foreign'] // 1000,
                    '投信買超(張)': meta['trust'] // 1000,
                    '自營商買超(張)': meta['dealer'] // 1000,
                    '三大法人買超(張)': meta['total'] // 1000
                })
        except Exception as e:
            continue

    return results

def main():
    parser = argparse.ArgumentParser(description="台股布林通道下軌 + 法人逆勢買超選股策略")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYYMMDD，預設為最新交易日)")
    parser.add_argument("--min-vol", type=int, default=1000, help="最低日成交量(張)，預設 1000 張")
    parser.add_argument("--max-dist", type=float, default=1.0, help="位於布林下軌上方容許百分比(%%)，預設 1.0%%")
    parser.add_argument("--min-dist", type=float, default=-5.0, help="跌破布林下軌容許百分比(%%)，預設 -5.0%%")
    parser.add_argument("--trust-dump-limit", type=int, default=500, help="投信最大容許賣超張數，預設 500 張 (超過視為大量拋售)")
    parser.add_argument("--export", type=str, default=None, help="匯出檔名 (如 result.csv 或 result.md)")
    parser.add_argument("--line", action="store_true", help="自動發送選股結果至 LINE 推播")

    args = parser.parse_args()

    results = screen_stocks(
        date_str=args.date,
        min_volume_lots=args.min_vol,
        lower_dist_max=args.max_dist,
        lower_dist_min=args.min_dist,
        trust_max_sell_lots=args.trust_dump_limit
    )

    if not results:
        print("\n[!] 經檢核，所選日期無符合所有複合條件的個股。")
        if args.line:
            try:
                from line_sender import send_to_line
                empty_msg = f"📊【台股選股日報】布林下軌逆勢法人買超\n📅 日期：{args.date or '今日'} 盤後\n\n今日無符合量能與下軌法人承接之標的，建議空手觀望耐心等待！"
                send_to_line(empty_msg)
            except Exception as e:
                print(f"[!] LINE 推播失敗: {e}")
        return

    df_res = pd.DataFrame(results)
    # 依「三大法人買超張數」降序排列
    df_res = df_res.sort_values(by="三大法人買超(張)", ascending=False).reset_index(drop=True)

    print("\n" + "="*85)
    print(f"🎯 選股結果清單（共 {len(df_res)} 檔）- 符合成交量 > {args.min_vol}張、布林下軌區間 [{args.min_dist}% ~ +{args.max_dist}%]、三大法人買超")
    print("="*85)
    print(df_res.to_string(index=False))
    print("="*85)

    if args.export:
        if args.export.endswith('.csv'):
            df_res.to_csv(args.export, index=False, encoding='utf-8-sig')
            print(f"[✓] 已成功匯出 CSV 報表至: {args.export}")
        elif args.export.endswith('.md'):
            with open(args.export, 'w', encoding='utf-8') as f:
                f.write(f"# 台股選股清單：布林下軌 + 法人逆勢買超策略\n\n")
                f.write(f"- 產生時間：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"- 篩選條件：日成交量 > {args.min_vol} 張、下軌區間 [{args.min_dist}% ~ +{args.max_dist}%]、三大法人合計買超且未大量拋售\n\n")
                f.write(df_res.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已成功匯出 Markdown 報表至: {args.export}")

    # 若指定 --line，發送格式化通知至使用者 LINE
    if args.line:
        try:
            from line_sender import send_to_line
            target_date = args.date or get_latest_trading_date() or "今日"
            line_msg = format_line_message(results, target_date, args)
            print("[*] 正在發送選股清單至 LINE...")
            send_to_line(line_msg)
        except Exception as e:
            print(f"[!] 發送 LINE 訊息過程發生異常: {e}")

def format_line_message(results, date_str, args):
    """將選股清單格式化為手機 LINE 好讀版排版"""
    # 排序
    sorted_res = sorted(results, key=lambda x: x['三大法人買超(張)'], reverse=True)
    msg_lines = [
        "📊【台股選股日報】布林下軌逆勢法人買超",
        f"📅 日期：{date_str} 盤後",
        f"🎯 條件：成交量 > {args.min_vol}張｜下軌區間 [{args.min_dist}%~+{args.max_dist}%]｜三大法人淨買超",
        f"🔥 今日共篩選出 {len(sorted_res)} 檔轉折潛力標的：",
        "─────────────────"
    ]
    
    for i, r in enumerate(sorted_res[:12], 1):
        name = r['證券名稱']
        code = r['證券代號']
        close = r['收盤價']
        dist = r['距下軌%']
        dist_sign = "+" if dist > 0 else ""
        vol = r['日成交量(張)']
        tot_inst = r['三大法人買超(張)']
        f_inst = r['外資買超(張)']
        t_inst = r['投信買超(張)']
        
        detail_items = []
        if f_inst != 0:
            detail_items.append(f"外資{'+' if f_inst>0 else ''}{f_inst}")
        if t_inst != 0:
            detail_items.append(f"投信{'+' if t_inst>0 else ''}{t_inst}")
        detail_str = f" ({', '.join(detail_items)})" if detail_items else ""
        
        msg_lines.append(
            f"{i}. {code} {name}\n"
            f"   • 收盤: {close} 元 (距下軌 {dist_sign}{dist}%)\n"
            f"   • 成交量: {vol:,} 張\n"
            f"   • 法人買超: +{tot_inst:,} 張{detail_str}"
        )
        
    if len(sorted_res) > 12:
        msg_lines.append(f"...\n(其餘 {len(sorted_res)-12} 檔請查看電腦 result.csv 報表)")
        
    msg_lines.append("─────────────────")
    msg_lines.append("💡 交易紀律：跌破下軌逾 5% 嚴格停損；反彈第一目標看 20MA 布林中軌！")
    return "\n".join(msg_lines)

if __name__ == '__main__':
    main()
