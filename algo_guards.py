#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 程式交易反制與風控守門引擎 (Algo Trading Guards)
================================================================================
本模組為全專案共用底層風控核心，專門反制台股程式交易與量化高頻單所造成的市場變數：
1. 【隔日沖分點集中度評估 (Overnight Broker Clustering Guard)】:
   - 識別著名量化隔日沖券商（凱基台北、富邦虎尾、美商高盛、美林等）。
   - 結合證交所 T86「自營商避險」爆量與量化做市代理指標。
   - 納入第二階段軟評分扣分 (扣 5~15 分) 與戰報警示，提醒「防開高倒貨、嚴禁追高」。
2. 【盤中防毛刺確認機制 (Intraday Anti-Noise Tick Filter)】:
   - 破解高頻演算法 1~2 張市價單瞬間觸價洗盤（毛刺假跌破甩轎、假突破誘多）。
   - 採「雙次確認 (Consecutive Confirmation)」或「有效實體偏離判定」。
   - 排除瞬間假到價，僅在確認站穩或有效跌破時發送 LINE 推播。
================================================================================
"""

import time
import datetime
from typing import Dict, Any, Tuple, Optional

# ------------------------------------------------------------------------------
# 1. 著名量化隔日沖券商特徵庫 (Famous Overnight Quant Brokers)
# ------------------------------------------------------------------------------
OVERNIGHT_BROKER_KEYWORDS = [
    "凱基-台北", "凱基-市政", "凱基-松山",
    "富邦-虎尾", "富邦-建國", "富邦-嘉義",
    "元大-土城永寧", "元大-竹科",
    "國票-敦北", "群益金鼎-大安",
    "美商高盛", "美林", "摩根士丹利", "統一-總公司"
]

def evaluate_overnight_broker_risk(
    code: str,
    meta: Optional[Dict[str, Any]] = None,
    broker_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    評估個股之「隔日沖分點集中度與倒貨風險」
    
    參數:
      - code: 股票代號 (如 '2330')
      - meta: 選股器元數據 (可含 'volume', 'dealer_hedge', 'foreign', 'close', 'inst_ratio' 等)
      - broker_data: 券商分點買賣明細 (選填，若有分點明細優先分析)

    回傳字典:
      {
        'is_overnight_warning': bool,  # 是否觸發隔日沖警報
        'risk_level': str,             # 'HIGH', 'MEDIUM', 'LOW'
        'penalty_score': int,          # 軟評分扣分值 (0, 5, 10, 15)
        'concentration_pct': float,    # 隔日沖/避險集中度百分比
        'tag': str,                    # 標籤顯示 (如 '⚠️隔日沖高集中(-15分)')
        'reason': str,                 # 詳細成因說明
        'advice': str                  # 次日操盤應對指引
      }
    """
    if not isinstance(meta, dict):
        meta = {}
    vol = float(meta.get('volume', 0))
    dealer_hedge = float(meta.get('dealer_hedge', meta.get('dealer', 0))) # 自營商避險
    foreign = float(meta.get('foreign', 0))
    inst_ratio = float(meta.get('inst_ratio', 0))

    # A. 若有精確券商分點數據 (Direct Broker Branch Detection)
    if broker_data and 'top_buyers' in broker_data:
        top_buyers = broker_data.get('top_buyers', [])
        overnight_buy_vol = 0
        total_top_buy = 0
        hit_brokers = []

        for b in top_buyers:
            b_name = b.get('name', '')
            b_net = b.get('net_buy', 0)
            total_top_buy += max(0, b_net)
            for kw in OVERNIGHT_BROKER_KEYWORDS:
                if kw in b_name and b_net > 0:
                    overnight_buy_vol += b_net
                    hit_brokers.append(b_name)
                    break

        if total_top_buy > 0:
            ratio = round((overnight_buy_vol / total_top_buy) * 100, 1)
            if ratio >= 40.0:
                return {
                    'is_overnight_warning': True,
                    'risk_level': 'HIGH',
                    'penalty_score': 15,
                    'concentration_pct': ratio,
                    'tag': f"⚠️隔日沖高度集中({ratio}%)",
                    'reason': f"買超前段班出現隔日沖主力 [{', '.join(hit_brokers[:2])}] 佔比高達 {ratio}%",
                    'advice': "次日開盤嚴禁盲目追高，防演算法開高倒貨；已有部位者可開盤分批獲利入袋。"
                }
            elif ratio >= 20.0:
                return {
                    'is_overnight_warning': True,
                    'risk_level': 'MEDIUM',
                    'penalty_score': 8,
                    'concentration_pct': ratio,
                    'tag': f"⚡含隔日沖分點({ratio}%)",
                    'reason': f"買超含有隔日沖分點 [{', '.join(hit_brokers[:2])}] 佔比 {ratio}%",
                    'advice': "開盤可能伴隨量化震盪，建議觀察 09:15 後量縮價穩再行進場。"
                }

    # B. 證交所 T86 自營商避險與量化做市代理模型 (TWSE Proxy Model)
    # 權證與程式量化套利避險單會在「自營商避險」留下極為顯著的足跡
    if vol > 0 and dealer_hedge > 0:
        hedge_ratio = round((dealer_hedge / vol) * 100, 1)
        # 當自營商避險買超佔成交量 >= 4.0%，通常代表權證量化隔日沖買盤湧入
        if hedge_ratio >= 4.0:
            return {
                'is_overnight_warning': True,
                'risk_level': 'HIGH',
                'penalty_score': 12,
                'concentration_pct': hedge_ratio,
                'tag': f"⚠️權證量化避險高({hedge_ratio}%)",
                'reason': f"自營商避險佔成交量高達 {hedge_ratio}%，短線權證量化隔日沖高度活躍",
                'advice': "隔日沖權證賣壓通常於開盤半小時傾瀉，切勿於 09:00~09:20 追價，防長上影線。"
            }
        elif hedge_ratio >= 2.5:
            return {
                'is_overnight_warning': True,
                'risk_level': 'MEDIUM',
                'penalty_score': 6,
                'concentration_pct': hedge_ratio,
                'tag': f"⚡量化短線避險({hedge_ratio}%)",
                'reason': f"自營商避險買超佔比 {hedge_ratio}%，具部分程式短線籌碼",
                'advice': "開盤維持分批低接，避免追高吃下量化調節單。"
            }

    # C. 常態健康籌碼 (真金白銀主力沉澱)
    return {
        'is_overnight_warning': False,
        'risk_level': 'LOW',
        'penalty_score': 0,
        'concentration_pct': 0.0,
        'tag': "🟢主力籌碼沉澱",
        'reason': "未見異常隔日沖或高頻避險扎堆，多為中長線法人波段吸籌",
        'advice': "籌碼穩定度高，可依原定四大價位紀律執行操盤計畫。"
    }


# ------------------------------------------------------------------------------
# 2. 盤中防毛刺確認機制 (Intraday Anti-Noise Tick Filter)
# ------------------------------------------------------------------------------
class IntradayTickGuard:
    """
    盤中防毛刺守門器 (Anti-Noise Tick Filter)
    
    破解高頻量化單「瞬間 1-tick 毛刺假跌破甩轎」與「瞬間假突破誘多」：
    - 狀態追蹤：紀錄每個個股事件的初次觸價狀態。
    - 雙次確認 (Consecutive Confirmation)：要求至少間隔 3 秒以上且連續確認，或實體穿透度足夠。
    - 停損安全急迫性 (Emergency Stop-Loss)：若跌破幅度 >= 1.0%，直接放行確保風控撤退。
    """
    def __init__(self, required_confirmations: int = 2, min_interval_sec: float = 3.0):
        self.required_confirmations = required_confirmations
        self.min_interval_sec = min_interval_sec
        # 結構: { "code_event": { "count": int, "first_ts": float, "last_price": float } }
        self._states: Dict[str, Dict[str, Any]] = {}

    def check_confirmation(
        self,
        code: str,
        event_code: str,
        current_price: float,
        target_price: float,
        force_pass: bool = False
    ) -> Tuple[bool, str]:
        """
        比對當前價格是否通過防毛刺確認
        
        回傳: (is_confirmed: bool, status_msg: str)
        """
        if force_pass:
            return True, "強制放行 (測試或覆蓋)"

        key = f"{code}_{event_code}"
        now_ts = time.time()

        # 1. 停損事件防護：若實體摜破幅度 >= 1.0% (非 1-tick 邊緣毛刺)，判定為真跌破，直接放行！
        if event_code == 'STOP_LOSS' and target_price > 0:
            penetration_pct = (target_price - current_price) / target_price * 100.0
            if penetration_pct >= 1.0:
                self._states.pop(key, None)
                return True, f"🛑 實體有效跌破停損線 (-{penetration_pct:.1f}%)，立即風控離場"

        # 2. 查詢既有碰價狀態
        state = self._states.get(key)
        if not state:
            # 第一次初碰價 (Pending，可能是高頻量化毛刺)
            self._states[key] = {
                'count': 1,
                'first_ts': now_ts,
                'last_price': current_price
            }
            return False, f"⏳ 初次觸碰價位 ({current_price:.2f})，進入防毛刺確認佇列..."

        # 3. 第二次以上確認
        state['count'] += 1
        elapsed = now_ts - state['first_ts']

        # 若已滿足連續確認次數 (>= 2) 或 時間間隔充足，判定為「真到價」
        if state['count'] >= self.required_confirmations or elapsed >= self.min_interval_sec:
            self._states.pop(key, None) # 成功出關，清除暫存
            return True, f"✓ 通過防毛刺雙重確認 (持續 {elapsed:.1f}s / {state['count']} 次)"
        else:
            return False, f"⏳ 正在進行第 {state['count']} 次防毛刺複檢中 (距初碰 {elapsed:.1f}s)..."

    def reset_target(self, code: str, event_code: Optional[str] = None):
        """當價格脫離區間時，重置防毛刺狀態"""
        if event_code:
            self._states.pop(f"{code}_{event_code}", None)
        else:
            to_remove = [k for k in self._states if k.startswith(f"{code}_")]
            for k in to_remove:
                self._states.pop(k, None)

# 全域單例
GLOBAL_TICK_GUARD = IntradayTickGuard()
