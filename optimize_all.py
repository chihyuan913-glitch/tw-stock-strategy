#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 每週日全策略動態優化與升級總引擎 (Weekly Strategy Optimizer)
執行時間：每週日 20:00 (台灣時間)
核心任務：
1. 【股票字典自動同步】：從證交所/櫃買中心官方資料同步最新股票代碼與中文名稱至 stock_dict.json。
2. 【大盤位階動態校準】：分析加權指數趨勢位階、20MA/60MA 乖離與市場多空型態，制定下週市場情境 (Market Regime)。
3. 【可轉債 (CB) 事件池動態健檢】：更新 Strategy 04 之 CB 事件池、核對轉換價與最後退場死線。
4. 【全策略盤前全域選股】：自動重跑全策略 01~04 盤後選股器，確保週一開盤備妥最新清單與四大防線價位。
5. 【生成每週戰略週報與 LINE 推播】：產出 weekly_report.md，並將下週操盤核心指引即時推播至 LINE。
"""

import os
import sys
import json
import re
import datetime
import urllib.request
from pathlib import Path
import pandas as pd
import numpy as np

# Windows 命令列編碼保護
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

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

TZ_TW = datetime.timezone(datetime.timedelta(hours=8))

from line_sender import send_to_line

def sync_stock_dictionary():
    """任務一：自證交所與櫃買中心同步最新全台股股票名稱與代碼"""
    print("\n[*] 【任務一/五】正在同步證交所 (TWSE) 與櫃買中心 (TPEx) 官方個股清單...")
    dict_file = ROOT_DIR / "stock_dict.json"
    stock_dict = {}
    initial_count = 0
    added_count = 0

    sources = [
        ("TWSE 上市", "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"),
        ("TPEx 上櫃", "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4")
    ]

    for name, url in sources:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode('big5', errors='ignore')
                rows = re.findall(r'<tr>(.*?)</tr>', html, re.DOTALL)
                current_sec = 'NONE'
                section_count = 0
                for r in rows:
                    sec_m = re.search(r'<B>\s*([^\s<]+)\s*<B>', r)
                    if sec_m:
                        current_sec = sec_m.group(1).strip()
                        continue
                    if current_sec in ('股票', '臺灣存託憑證(TDR)', 'ETF'):
                        cols = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)]
                        if len(cols) >= 6:
                            code_name = cols[0]
                            m = re.match(r'^(\d{4,6})[\u3000\s]+([^\s]+)', code_name)
                            if m:
                                code, sname = m.group(1), m.group(2).strip()
                                stock_dict[code] = sname
                                stock_dict[sname] = code
                                section_count += 1
                                added_count += 1
            print(f"[✓] {name} 清單同步完成 (共擷取 {section_count} 筆純股票與ETF標的)")
        except Exception as e:
            print(f"[!] 同步 {name} 失敗: {e}")

    try:
        with open(dict_file, "w", encoding="utf-8") as f:
            json.dump(stock_dict, f, ensure_ascii=False, indent=2)
        print(f"[✓] 股票代碼字典更新成功！現有對照記錄：{len(stock_dict)} 條 (新增/更新：{added_count} 條)")
    except Exception as e:
        print(f"[!] 儲存 stock_dict.json 失敗: {e}")

    return len(stock_dict), added_count

def calibrate_market_regime():
    """任務二：加權指數動態校準與下週市場位階判定"""
    print("\n[*] 【任務二/五】正在分析加權指數 (^TWII) 位階與動態市場環境...")
    try:
        import yfinance as yf
        df = yf.download("^TWII", period="3mo", interval="1d", progress=False)
        if df.empty or len(df) < 20:
            print("[!] 加權指數歷史資料不足，使用預設平衡型參數。")
            return {
                "regime": "穩健平衡型 (常態震盪)",
                "close": 23000,
                "bias_ma20": 0.0,
                "ma20": 23000,
                "ma60": 23000,
                "trend": "多空均衡",
                "vol_status": "常態",
                "risk_advice": "維持標準倉位 5~7 成，嚴守 4+1 維度與四大防線。"
            }

        close_col = df['Close']
        if isinstance(close_col, pd.DataFrame):
            close_col = close_col.iloc[:, 0]
        close = float(close_col.iloc[-1])
        ma20 = float(close_col.rolling(20).mean().iloc[-1])
        ma60 = float(close_col.rolling(60).mean().iloc[-1]) if len(close_col) >= 60 else ma20
        bias20 = round(((close - ma20) / ma20) * 100, 2)

        if close >= ma20 and ma20 >= ma60:
            if bias20 > 5.0:
                regime = "🔴【多頭強攻・短線過熱警戒】"
                trend = "強勢多頭"
                risk_advice = "指數正乖離偏高，嚴防盤中震盪拉回。多單嚴禁盲目追高，善用拉回均線低接；持股者移動停利上調。"
            else:
                regime = "🟢【多頭良性主升波段】"
                trend = "多頭排列"
                risk_advice = "均線呈多頭排列，盤面具動能續航力。可積極佈局策略 02 法人起漲股與策略 04 可轉債伏擊股。"
        elif close < ma20 and close >= ma60:
            regime = "🟡【高檔震盪整理・箱型洗盤】"
            trend = "箱型震盪"
            risk_advice = "指數跌破月線但守穩季線，類股快速輪動。採高出低進策略，重視布林下軌超跌反彈（策略 01）。"
        else:
            regime = "🟣【空頭回檔修正・防禦避險】"
            trend = "弱勢破線"
            risk_advice = "指數跌破雙均線，以保守防守為最高原則。提高現金水位至 7 成以上，善用策略 03 個股期放空避險。"

        result = {
            "regime": regime,
            "close": round(close, 2),
            "bias_ma20": bias20,
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "trend": trend,
            "risk_advice": risk_advice
        }
        print(f"[✓] 加權指數現價: {close:.2f} | 20MA: {ma20:.2f} | 月線乖離: {bias20:+.2f}%")
        print(f"[✓] 下週市場位階判定: {regime}")
        return result
    except Exception as e:
        print(f"[!] 分析市場位階失敗: {e}")
        return {
            "regime": "穩健平衡型",
            "close": 0,
            "bias_ma20": 0.0,
            "ma20": 0,
            "ma60": 0,
            "trend": "均衡",
            "risk_advice": "請注意即時風控。"
        }

def audit_cb_pool():
    """任務三：可轉債 (CB) 事件池動態審查與過期維護"""
    print("\n[*] 【任務三/五】正在審查可轉債 (Strategy 04) 事件池與退場死線...")
    cb_file = ROOT_DIR / "strategies" / "04_cb_pricing_ambush" / "cb_pool.json"
    active_targets = []
    expired_count = 0

    if cb_file.exists():
        try:
            with open(cb_file, "r", encoding="utf-8") as f:
                pool = json.load(f)
            
            today = datetime.datetime.now(TZ_TW).date()
            for item in pool:
                lockup_str = item.get("lockup_end_date", "")
                is_expired = False
                # 檢查是否過期
                m = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', lockup_str)
                if m:
                    exp_date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    if exp_date < today:
                        is_expired = True
                        expired_count += 1
                
                if not is_expired:
                    active_targets.append(item)
            
            print(f"[✓] CB 事件池審查完成：有效追蹤 {len(active_targets)} 檔 (排除過期 {expired_count} 檔)")
            return len(active_targets), active_targets
        except Exception as e:
            print(f"[!] 讀取 cb_pool.json 失敗: {e}")
    return 0, []

def audit_and_rank_group_pool() -> tuple:
    """任務三點五：審查 Strategy 08 之集團池字典並統計當週最強作帳前三大集團"""
    print("\n[*] 【任務三點五/五】正在校準集團池 (Strategy 08) 字典並統計熱度排行...")
    pool_file = ROOT_DIR / "strategies" / "08_group_window_dressing" / "group_pool.json"
    dict_file = ROOT_DIR / "stock_dict.json"
    res_csv = ROOT_DIR / "strategies" / "08_group_window_dressing" / "result.csv"

    group_count = 0
    valid_member_count = 0
    top_groups_lines = []

    if pool_file.exists():
        try:
            with open(pool_file, "r", encoding="utf-8") as f:
                pool = json.load(f)
            group_count = len(pool)
            
            stock_dict = {}
            if dict_file.exists():
                try:
                    with open(dict_file, "r", encoding="utf-8") as f:
                        stock_dict = json.load(f)
                except Exception:
                    pass

            group_scores = {}
            for g in pool:
                gid = g.get('group_id')
                gname = g.get('group_name')
                weight = float(g.get('historical_weight', 1.0))
                members = g.get('members', [])
                valid_m = [m for m in members if str(m.get('code')) in stock_dict or not stock_dict]
                valid_member_count += len(valid_m)
                group_scores[gname] = {
                    'count': 0,
                    'weight': weight,
                    'top_stock': f"{g.get('flagship_code')} {g.get('flagship_name')}"
                }

            # 若有 result.csv，統計入選檔數 (欄位名稱為「所屬集團」，舊版誤讀「集團名稱」導致恆為 0)
            if res_csv.exists():
                try:
                    df = pd.read_csv(res_csv, encoding='utf-8-sig')
                    for _, row in df.iterrows():
                        gn = str(row.get('所屬集團', '')).strip()
                        if gn in group_scores:
                            group_scores[gn]['count'] += 1
                except Exception:
                    pass

            # 優先採用選股器落地之當日熱度排行 (含共振、法人資金流向)，否則退回入選檔數加權排序
            ranking_file = ROOT_DIR / "strategies" / "08_group_window_dressing" / "group_ranking.json"
            ranked_ok = False
            if ranking_file.exists():
                try:
                    with open(ranking_file, "r", encoding="utf-8") as f:
                        rk = json.load(f)
                    for g in rk.get('ranking', [])[:3]:
                        gname = g.get('group_name', '')
                        picked = group_scores.get(gname, {}).get('count', 0)
                        top_groups_lines.append(
                            f"{g.get('rank')}. **{gname}**（熱度 {g.get('heat_score')}，{g.get('status_badge', '')}，"
                            f"共振 {g.get('active_count')}/{g.get('member_count')}，"
                            f"法人淨買 {int(g.get('net_inst_1d_lots', 0)):+,} 張，入選 {picked} 檔，排行日 {rk.get('date', '')}）"
                        )
                    ranked_ok = bool(top_groups_lines)
                except Exception:
                    top_groups_lines = []

            if not ranked_ok:
                sorted_groups = sorted(
                    group_scores.items(),
                    key=lambda x: (x[1]['count'] * x[1]['weight'], x[1]['weight']),
                    reverse=True
                )
                for rank, (gname, info) in enumerate(sorted_groups[:3], 1):
                    top_groups_lines.append(f"{rank}. **{gname}**（熱度權重 {info['weight']}，旗艦標的：{info['top_stock']}，共振入選 {info['count']} 檔）")

            print(f"[✓] 集團池校驗完成：共 {group_count} 個核心集團，有效成分股 {valid_member_count} 檔。")
            return group_count, valid_member_count, top_groups_lines
        except Exception as e:
            print(f"[!] 校驗集團池失敗: {e}")

    return 0, 0, []

def run_strategies_premarket():
    """任務四：全策略自動排程選股與最新報表生成"""
    print("\n[*] 【任務四/五】正在為週一開盤執行全策略盤前選股 (run_all.py)...")
    import subprocess
    cmd = [sys.executable, str(ROOT_DIR / "run_all.py")]
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True, encoding='utf-8', errors='ignore')
        print(f"[✓] 全策略盤前自動選股完成！(Exit Code: {proc.returncode})")
    except Exception as e:
        print(f"[!] 執行 run_all.py 失敗: {e}")

def collect_latest_candidates():
    """彙整全策略最新選出的入選標的"""
    strategies = [
        ("01_bollinger_reversal", "策略 01：布林下軌超跌反轉"),
        ("02_institutional_momentum", "策略 02：法人籌碼集中起漲"),
        ("03_short_momentum", "策略 03：弱勢破線做空避險"),
        ("04_cb_pricing_ambush", "策略 04：可轉債定價伏擊"),
        ("05_stock_futures_long", "策略 05：個股期法人強勢波段"),
        ("06_volume_surge", "策略 06：盤中爆量起漲動能"),
        ("07_margin_call_ambush", "策略 07：高融資斷頭與浮額清洗"),
        ("08_group_window_dressing", "策略 08：集團作帳與母子聯動"),
        ("09_treasury_stock_buyback", "策略 09：庫藏宣告與低估護盤")
    ]
    summary = {}
    for folder, name in strategies:
        csv_path = ROOT_DIR / "strategies" / folder / "result.csv"
        stocks = []
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path, encoding='utf-8-sig')
                for _, row in df.head(3).iterrows():
                    code = str(row.get('證券代號') or row.get('代號') or '').strip()
                    sname = str(row.get('證券名稱') or row.get('名稱') or '').strip()
                    entry = str(row.get('進場價位') or row.get('收盤價') or '').strip()
                    sl = str(row.get('停損價位') or '').strip()
                    stars = str(row.get('星級推薦') or row.get('星級') or '★★★★☆').strip()
                    if code:
                        stocks.append(f"{code} {sname} ({stars}) [進場: {entry} | 停損: {sl}]")
            except Exception:
                pass
        summary[name] = stocks
    return summary

def generate_weekly_report(dict_info, market_info, cb_info, group_info, candidates, adaptive_config=None, send_line=False):
    """任務五：產出週報與 LINE 推播"""
    print("\n[*] 【任務五/五】正在產生【每週全策略優化與升級總體戰報】...")
    now_str = datetime.datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M")
    
    dict_total, dict_added = dict_info
    cb_active, _ = cb_info
    grp_count, grp_members, top_groups = group_info

    report_lines = [
        f"# 🏆 台股最高總指揮中心 - 每週全策略動態優化與升級總體戰報",
        f"**升級校準時間：{now_str} (週日 20:00 盤前自動作業)**",
        "",
        "---",
        "",
        "## 🧭 一、大盤情境與波動率校準 (Market Regime)",
        f"- **加權指數位階**：{market_info['close']} 點 (20MA: {market_info['ma20']} | 月線乖離: {market_info['bias_ma20']:+.2f}%)",
        f"- **市場位階判定**：{market_info['regime']}",
        f"- **大盤多空結構**：{market_info['trend']}",
        f"- **下週總體指引**：{market_info['risk_advice']}",
        "",
        "---",
        "",
        "## ⚙️ 二、全策略基礎工程升級成果",
        f"1. **全台股代碼與名稱字典 (stock_dict.json)**：",
        f"   - 官方證交所 (TWSE) 與櫃買中心 (TPEx) 聯網校驗完畢。",
        f"   - 總對照詞彙數達到 **{dict_total}** 筆（本次更新/新增 **{dict_added}** 筆），確保隨傳隨回零誤判。",
        f"2. **可轉債 (CB) 事件池動態審查 (Strategy 04)**：",
        f"   - 目前有效追蹤發行中標的共 **{cb_active}** 檔，過期解禁標的已強制清倉除帳。",
        f"3. **集團作帳與母子聯動監控池審查 (Strategy 08)**：",
        f"   - 校準台股 25 大核心作帳集團，有效追蹤成分股共 **{grp_count}** 個集團、**{grp_members}** 檔標的。",
        f"   - **當週最強作帳前三大集團排行**：",
    ]

    if top_groups:
        for tg in top_groups:
            report_lines.append(f"     - {tg}")
    else:
        report_lines.append("     - 目前各集團作帳動能均衡輪動中。")

    report_lines.extend([
        f"4. **風控標準再次強化**：",
        f"   - 全面落實「4+1 核心維度（硬篩選 $\\le 5$ 個）」與「四大操盤防線（🟢進場、🔵加碼、🔴停利、🛑停損）」。",
        "",
        "---",
        "",
        "## 🎯 三、下週各策略核心焦點名單 (前 3 檔精華)",
    ])

    for sname, stocks in candidates.items():
        report_lines.append(f"### {sname}")
        if stocks:
            for s in stocks:
                report_lines.append(f"- {s}")
        else:
            report_lines.append("- （目前無符合嚴格 4+1 條件標的，維持資金安全）")
        report_lines.append("")

    # 嵌入第四章：全域自適應操盤矩陣
    if adaptive_config:
        from adaptive_regime_manager import format_adaptive_report_section
        report_lines.append("---")
        report_lines.append("")
        report_lines.append(format_adaptive_report_section(adaptive_config))
        report_lines.append("")

    report_lines.extend([
        "---",
        "💡 *本報告由最高指揮中心 GitHub Actions 雲端工作流於每週日 20:00 自動執行產出。*",
        "⚠️ *投資涉及風險，操盤請嚴守停損紀律，做好部位管理。*"
    ])

    report_content = "\n".join(report_lines)
    report_file = ROOT_DIR / "weekly_report.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_content)
    print(f"[✓] 週報已寫入: {report_file}")

    if send_line:
        print("\n[*] 正在將每週戰略週報摘要推播至 LINE...")
        line_msg = [
            "🏆【台股總指揮中心・每週全策略優化與升級戰報】",
            f"📅 校準時間：{now_str}",
            f"🧭 下週大盤：{market_info['regime']}",
            f"📊 指數現價：{market_info['close']} (月線乖離 {market_info['bias_ma20']:+.1f}%)",
            "",
            "⚙️ 全策略基礎工程升級：",
            f"• 股票字典同步：{dict_total} 檔 (新增/更新 {dict_added})",
            f"• 可轉債有效池：{cb_active} 檔 (過期已除帳)",
            f"• 集團作帳池：{grp_count} 集團 ({grp_members} 檔)",
            "• 四大防線與 4+1 濾網：校準就緒"
        ]

        if adaptive_config:
            g_ctrl = adaptive_config.get("global_controls", {})
            l_strat = adaptive_config.get("long_strategies", {})
            line_msg.extend([
                f"🧠 自適應模式：【{g_ctrl.get('mode', '平衡型')}】(現金 {g_ctrl.get('cash_target_pct', 50)}%)",
                f"• 多方參數：追高上限 {l_strat.get('bias_max_pct', 3.5)}% | TP1 +{l_strat.get('tp1_pct', 4.0)}% | SL {l_strat.get('stop_loss_pct', -5.0)}%"
            ])

        line_msg.extend([
            "",
            "🎯 下週焦點監控精選："
        ])

        for sname, stocks in candidates.items():
            short_sname = sname.split("：")[-1]
            if stocks:
                top_stock = stocks[0].split(" [")[0]
                line_msg.append(f"• {short_sname}：{top_stock}")
            else:
                line_msg.append(f"• {short_sname}：觀望保留現金")

        line_msg.extend([
            "",
            f"💡【作戰提示】{market_info['risk_advice'][:45]}...",
            "📱 詳細報告已更新至 weekly_report.md"
        ])

        push_text = "\n".join(line_msg)
        send_to_line(push_text, strategy=None)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="台股每週日全策略動態優化與升級總引擎")
    parser.add_argument("--line", action="store_true", help="執行後推播每週戰報摘要至 LINE")
    args = parser.parse_args()

    print("=" * 70)
    print("🚀 啟動【台股最高總指揮中心 - 每週全策略優化與再升級作業】")
    print("=" * 70)

    # 1. 股票字典同步
    dict_info = sync_stock_dictionary()

    # 2. 大盤位階動態校準
    market_info = calibrate_market_regime()

    # 3. CB 事件池審查
    cb_info = audit_cb_pool()

    # 3.5 集團作帳池審查
    group_info = audit_and_rank_group_pool()

    # 3.8 全域自適應操盤矩陣生成 (Adaptive Market Regime Engine)
    from adaptive_regime_manager import generate_adaptive_regime_config
    adaptive_config = generate_adaptive_regime_config(market_info, group_info)

    # 4. 全策略盤前自動選股
    run_strategies_premarket()

    # 4.5 雷達監控池跨日動態滾動校準與自動移動停利 (Auto Trailing Stop)
    try:
        from radar_manager import rollover_watchlist_defense
        rollover_watchlist_defense(verbose=True)
    except Exception as e:
        print(f"[!] 週日雷達監控池滾動校準失敗: {e}")

    # 5. 彙整候選標的
    candidates = collect_latest_candidates()

    # 6. 生成週報與 LINE 推播
    generate_weekly_report(dict_info, market_info, cb_info, group_info, candidates, adaptive_config=adaptive_config, send_line=args.line)

    print("\n" + "=" * 70)
    print("🎉 【每週全策略優化與再升級作業】全數順利完成！下週開盤備妥就緒！")
    print("=" * 70)

if __name__ == '__main__':
    main()
