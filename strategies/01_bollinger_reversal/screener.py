#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股選股策略 V2.0 旗艦版：布林通道下軌 + 三大法人逆勢買超 + 止跌型態與盈虧比濾網
核心條件：
1. 流動性防護：日成交量 >= 1000 張
2. 統計超跌區：股價位於布林通道下軌 1% 或是跌破 5% 之內 (-5.0% <= (Close - LB) / LB <= +1.0%)
3. 籌碼真主力：三大法人合計買超 > 0，且買超佔比 >= 門檻 (預設 2.0%)，投信無恐慌拋售
4. 型態止跌濾網：留下影線、收紅K或未收在最低點，過濾一棒灌破的長黑K飛刀
5. 盈虧比與空間：距布林中軌 (20MA) 潛在反彈空間 >= 2.0%
6. 智慧評分系統：0~100 分轉折潛力評分與 ★★★★★ 星級標籤
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
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

def get_latest_trading_date(max_days_back=15):
    """取得最近有證交所交易資料的日期 (YYYYMMDD)"""
    today = datetime.date.today()
    for i in range(max_days_back):
        d = today - datetime.timedelta(days=i)
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
    """抓取證交所三大法人買賣超 (T86) 與每日收盤行情 (MI_INDEX) 包含完整 OHLC"""
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

    # 2. 抓取每日收盤行情 (MI_INDEX, 包含 Open, High, Low, Close)
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
                                open_str = row[5].replace(',', '').strip()
                                high_str = row[6].replace(',', '').strip()
                                low_str = row[7].replace(',', '').strip()
                                close_str = row[8].replace(',', '').strip()
                                if close_str != '--' and open_str != '--':
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
        print(f"[!] 抓取每日行情資料失敗: {e}")
        return {}, {}

    return quotes, chips

def calculate_candlestick_features(q):
    """計算單日K線型態特徵 (實體、下影線、紅黑K)"""
    open_p = q['open']
    high_p = q['high']
    low_p = q['low']
    close_p = q['close']

    amplitude = max(high_p - low_p, 0.001)
    lower_shadow = max(min(open_p, close_p) - low_p, 0.0)
    lower_shadow_ratio = lower_shadow / amplitude
    is_bullish = close_p >= open_p
    # 是否未收在最低點 (距最低點至少有振幅的 10%)
    not_closed_at_low = close_p > (low_p + amplitude * 0.10)
    
    # 止跌反轉型態評定
    is_reversal = (lower_shadow_ratio >= 0.25) or is_bullish or not_closed_at_low

    pattern_desc = []
    if lower_shadow_ratio >= 0.35:
        pattern_desc.append("長下影線")
    elif lower_shadow_ratio >= 0.20:
        pattern_desc.append("帶下影線")
        
    if is_bullish:
        pattern_desc.append("收紅K")
    else:
        pattern_desc.append("收黑K")

    return {
        'lower_shadow_ratio': lower_shadow_ratio,
        'is_bullish': is_bullish,
        'is_reversal': is_reversal,
        'pattern_desc': "＋".join(pattern_desc)
    }

def calculate_score(inst_ratio, foreign_lots, trust_lots, candle_feat, upside_pct):
    """
    量化評分系統 (0~100 分)：
    1. 籌碼影響力 (25分): 法人買超佔成交量比重
    2. 主力純度 (25分): 外資與投信合力 (土洋同買加分)
    3. 止跌型態 (25分): 長下影線與落底紅K
    4. 反彈空間 (25分): 距 20MA (中軌) 空間
    """
    # 1. 籌碼佔比 (0~25)
    if inst_ratio >= 10.0:
        s_chip = 25
    elif inst_ratio >= 5.0:
        s_chip = 20
    elif inst_ratio >= 2.5:
        s_chip = 15
    else:
        s_chip = 10

    # 2. 主力純度 (0~25)
    is_dual = (foreign_lots > 0) and (trust_lots > 0)
    if is_dual:
        s_purity = 25  # 土洋同步同買，最強背書
    elif trust_lots > 0:
        s_purity = 20  # 投信認養
    elif foreign_lots > 0 and trust_lots == 0:
        s_purity = 15  # 外資單方買超
    else:
        s_purity = 10

    # 3. K線止跌型態 (0~25)
    ls_ratio = candle_feat['lower_shadow_ratio']
    is_bull = candle_feat['is_bullish']
    if is_bull and ls_ratio >= 0.30:
        s_candle = 25  # 錘子線/長下影紅K，完美反轉
    elif ls_ratio >= 0.30 or (is_bull and ls_ratio >= 0.15):
        s_candle = 20
    elif is_bull or ls_ratio >= 0.20:
        s_candle = 15
    elif candle_feat['is_reversal']:
        s_candle = 10
    else:
        s_candle = 0   # 灌破收在最低，0分

    # 4. 反彈空間 (0~25)
    if upside_pct >= 6.0:
        s_upside = 25
    elif upside_pct >= 4.0:
        s_upside = 20
    elif upside_pct >= 2.5:
        s_upside = 15
    elif upside_pct >= 1.0:
        s_upside = 10
    else:
        s_upside = 5

    total_score = s_chip + s_purity + s_candle + s_upside

    # 星級評等
    if total_score >= 80:
        stars = "★★★★★"
    elif total_score >= 65:
        stars = "★★★★☆"
    elif total_score >= 50:
        stars = "★★★☆☆"
    else:
        stars = "★★☆☆☆"

    return total_score, stars, is_dual

def screen_stocks(date_str=None, min_volume_lots=1000, 
                  lower_dist_max=1.0, lower_dist_min=-5.0, 
                  min_inst_ratio=1.5, min_upside=2.0,
                  trust_max_sell_lots=500, require_reversal_candle=True):
    """
    V2.0 旗艦選股流程
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
    print(f"[*] 第一階段過濾：成交量 >= {min_volume_lots} 張，三大法人買超佔比 >= {min_inst_ratio}%...")

    candidate_codes = []
    candidate_meta = {}
    min_vol_shares = min_volume_lots * 1000

    for code, q in quotes.items():
        if q['volume'] >= min_vol_shares and code in chips:
            c = chips[code]
            vol_lots = q['volume'] // 1000
            total_lots = c['total'] // 1000
            inst_ratio = (total_lots / vol_lots * 100.0) if vol_lots > 0 else 0.0

            # 條件 1: 日成交量 >= 1000 張
            # 條件 3: 三大主力法人合計買超 > 0 且佔比達標
            # 條件 3 (副條件): 投信未大量拋售
            if total_lots > 0 and inst_ratio >= min_inst_ratio and c['trust'] >= -(trust_max_sell_lots * 1000):
                # 型態止跌濾網
                candle_feat = calculate_candlestick_features(q)
                if require_reversal_candle and not candle_feat['is_reversal']:
                    continue

                candidate_codes.append(code)
                candidate_meta[code] = {**q, **c, 'inst_ratio': inst_ratio, 'candle_feat': candle_feat}

    print(f"[*] 通過量能、籌碼佔比與K線止跌初篩個股共 {len(candidate_codes)} 檔。")
    if not candidate_codes:
        print("[-] 今日無個股符合量能、籌碼佔比與型態門檻。")
        return []

    print(f"[*] 第二階段計算：批次計算 20 日布林通道與反彈空間...")
    tickers = [f"{c}.TW" for c in candidate_codes]
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

            ma20 = close_series.rolling(20).mean().iloc[-1]
            std20 = close_series.rolling(20).std(ddof=0).iloc[-1]
            lower_band = ma20 - 2.0 * std20
            upper_band = ma20 + 2.0 * std20
            
            curr_close = meta['close']
            dist_pct = (curr_close - lower_band) / lower_band * 100.0

            # 條件 2: 位於布林下軌 1% 之內或是跌破 5% 之內
            if lower_dist_min <= dist_pct <= lower_dist_max:
                # 反彈空間 (距 20MA 中軌潛在利潤)
                upside_pct = (ma20 - curr_close) / curr_close * 100.0
                if upside_pct < min_upside:
                    continue

                vol_lots = meta['volume'] // 1000
                f_lots = meta['foreign'] // 1000
                t_lots = meta['trust'] // 1000
                d_lots = meta['dealer'] // 1000
                tot_lots = meta['total'] // 1000
                c_feat = meta['candle_feat']

                # 智慧評分
                score, stars, is_dual = calculate_score(
                    inst_ratio=meta['inst_ratio'],
                    foreign_lots=f_lots,
                    trust_lots=t_lots,
                    candle_feat=c_feat,
                    upside_pct=upside_pct
                )

                results.append({
                    '證券代號': code,
                    '證券名稱': meta['name'],
                    '星級推薦': stars,
                    '評分': score,
                    '收盤價': round(curr_close, 2),
                    '布林下軌': round(float(lower_band), 2),
                    '反彈空間%': round(float(upside_pct), 2),
                    '距下軌%': round(float(dist_pct), 2),
                    '日成交量(張)': vol_lots,
                    '法人買超(張)': tot_lots,
                    '法人佔比%': round(meta['inst_ratio'], 1),
                    '外資買超': f_lots,
                    '投信買超': t_lots,
                    '土洋同買': "是" if is_dual else "否",
                    'K線型態': c_feat['pattern_desc']
                })
        except Exception:
            continue

    return results

def format_line_message(results, date_str, args):
    """將 V2.0 選股清單格式化為手機 LINE 排版"""
    sorted_res = sorted(results, key=lambda x: x['評分'], reverse=True)
    msg_lines = [
        "📊【台股選股日報 V2.0 旗艦版】",
        f"📅 日期：{date_str} 盤後",
        f"🎯 策略：布林下軌超跌 ＋ 法人逆勢買超 ＋ 止跌型態",
        f"🔥 今日精選轉折標的（共 {len(sorted_res)} 檔，依評分排序）：",
        "─────────────────"
    ]
    
    for i, r in enumerate(sorted_res[:10], 1):
        name = r['證券名稱']
        code = r['證券代號']
        stars = r['星級推薦']
        score = r['評分']
        close = r['收盤價']
        dist = r['距下軌%']
        dist_sign = "+" if dist > 0 else ""
        upside = r['反彈空間%']
        vol = r['日成交量(張)']
        tot_inst = r['法人買超(張)']
        ratio = r['法人佔比%']
        f_inst = r['外資買超']
        t_inst = r['投信買超']
        pattern = r['K線型態']
        dual_str = " 🔥土洋同買" if r['土洋同買'] == "是" else ""

        detail_items = []
        if f_inst != 0:
            detail_items.append(f"外資{'+' if f_inst>0 else ''}{f_inst}")
        if t_inst != 0:
            detail_items.append(f"投信{'+' if t_inst>0 else ''}{t_inst}")
        chip_detail = f" ({', '.join(detail_items)})" if detail_items else ""

        msg_lines.append(
            f"{i}. {code} {name} {stars} (評分:{score})\n"
            f"   • 收盤: {close} 元 (距下軌 {dist_sign}{dist}%)\n"
            f"   • 潛在反彈空間: +{upside}% (看中軌)\n"
            f"   • K線: {pattern}{dual_str}\n"
            f"   • 籌碼: 買超 +{tot_inst:,} 張 (佔比 {ratio}%){chip_detail}"
        )
        
    if len(sorted_res) > 10:
        msg_lines.append(f"...\n(其餘 {len(sorted_res)-10} 檔請查看電腦 result.csv 報表)")
        
    msg_lines.append("─────────────────")
    msg_lines.append("💡 交易紀律：優先鎖定 ★★★★☆ 以上標的；跌破下軌逾 5% 嚴格停損！")
    return "\n".join(msg_lines)

def main():
    parser = argparse.ArgumentParser(description="台股布林通道下軌 + 法人逆勢買超選股策略 V2.0 旗艦版")
    parser.add_argument("--date", type=str, default=None, help="指定日期 (格式: YYYYMMDD，預設為最新交易日)")
    parser.add_argument("--min-vol", type=int, default=1000, help="最低日成交量(張)，預設 1000 張")
    parser.add_argument("--max-dist", type=float, default=1.0, help="位於布林下軌上方容許百分比(%%)，預設 1.0%%")
    parser.add_argument("--min-dist", type=float, default=-5.0, help="跌破布林下軌容許百分比(%%)，預設 -5.0%%")
    parser.add_argument("--min-inst-ratio", type=float, default=1.5, help="法人買超佔成交量最低比重(%%)，預設 1.5%%")
    parser.add_argument("--min-upside", type=float, default=2.0, help="距布林中軌最低反彈空間(%%)，預設 2.0%%")
    parser.add_argument("--trust-dump-limit", type=int, default=500, help="投信最大容許賣超張數，預設 500 張")
    parser.add_argument("--no-candle-filter", action="store_true", help="關閉 K 線止跌型態濾網")
    parser.add_argument("--export", type=str, default=None, help="匯出檔名 (如 result.csv 或 result.md)")
    parser.add_argument("--line", action="store_true", help="自動發送選股結果至 LINE 推播")

    args = parser.parse_args()

    results = screen_stocks(
        date_str=args.date,
        min_volume_lots=args.min_vol,
        lower_dist_max=args.max_dist,
        lower_dist_min=args.min_dist,
        min_inst_ratio=args.min_inst_ratio,
        min_upside=args.min_upside,
        trust_max_sell_lots=args.trust_dump_limit,
        require_reversal_candle=not args.no_candle_filter
    )

    if not results:
        print("\n[!] 經 V2.0 旗艦濾網檢核，所選日期無符合所有複合條件的個股。")
        if args.line:
            try:
                from line_sender import send_to_line
                empty_msg = f"📊【台股選股日報 V2.0】\n📅 日期：{args.date or '今日'} 盤後\n\n今日全市場無符合 V2.0 旗艦濾網（止跌型態+法人純度）之個股，建議耐心空手等待！"
                send_to_line(empty_msg)
            except Exception as e:
                print(f"[!] LINE 推播失敗: {e}")
        return

    df_res = pd.DataFrame(results)
    # 依「評分」降序排列
    df_res = df_res.sort_values(by="評分", ascending=False).reset_index(drop=True)

    print("\n" + "="*95)
    print(f"🎯 選股結果清單 V2.0（共 {len(df_res)} 檔）- 依智慧評分排序（星級 ★★★★★ / 反彈空間 / 法人純度）")
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
                f.write(f"# 台股選股清單 V2.0 旗艦版\n\n")
                f.write(f"- 產生時間：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"- 核心條件：成交量 > {args.min_vol}張｜下軌區間 [{args.min_dist}% ~ +{args.max_dist}%]｜法人買超佔比 > {args.min_inst_ratio}%｜反彈空間 > {args.min_upside}%\n\n")
                f.write(df_res.to_markdown(index=False))
                f.write("\n")
            print(f"[✓] 已成功匯出 Markdown 報表至: {export_path}")

    if args.line:
        try:
            from line_sender import send_to_line
            target_date = args.date or get_latest_trading_date() or "今日"
            line_msg = format_line_message(results, target_date, args)
            print("[*] 正在發送 V2.0 選股清單至 LINE...")
            send_to_line(line_msg)
        except Exception as e:
            print(f"[!] 發送 LINE 訊息過程發生異常: {e}")

if __name__ == '__main__':
    main()
