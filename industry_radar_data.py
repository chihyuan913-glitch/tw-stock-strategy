# -*- coding: utf-8 -*-
"""
台股產業鏈 (Chain)、概念股 (Concept) 與企業標誌 (Logos) 結構化數據中心
"""
from typing import Dict, Any, List

# 企業精準 Logo 縮寫與代表色（完美還原看盤畫面）
STOCK_LOGOS: Dict[str, Dict[str, str]] = {
    "2330": {"bg": "#cc0000", "fg": "#ffffff", "text": "TSMC", "symbol": "●"},
    "2454": {"bg": "#ff6600", "fg": "#ffffff", "text": "M", "symbol": "M"},
    "2308": {"bg": "#0066cc", "fg": "#ffffff", "text": "▲", "symbol": "▲"},
    "2317": {"bg": "#e60012", "fg": "#ffffff", "text": "H", "symbol": "H"},
    "2382": {"bg": "#004080", "fg": "#ffffff", "text": "Q", "symbol": "Q"},
    "2357": {"bg": "#002060", "fg": "#ffffff", "text": "ASUS", "symbol": "A"},
    "2376": {"bg": "#ff0033", "fg": "#ffffff", "text": "G", "symbol": "G"},
    "2301": {"bg": "#ff3300", "fg": "#ffffff", "text": "L", "symbol": "L"},
    "2881": {"bg": "#008855", "fg": "#ffffff", "text": "F", "symbol": "F"},
    "2882": {"bg": "#009944", "fg": "#ffffff", "text": "🌲", "symbol": "🌲"},
    "2891": {"bg": "#007799", "fg": "#ffffff", "text": "CTBC", "symbol": "C"},
    "2886": {"bg": "#990000", "fg": "#ffffff", "text": "M", "symbol": "M"},
    "2884": {"bg": "#008833", "fg": "#ffffff", "text": "ESUN", "symbol": "E"},
    "2885": {"bg": "#003366", "fg": "#ffffff", "text": "Y", "symbol": "Y"},
    "2603": {"bg": "#006633", "fg": "#ffffff", "text": "EMC", "symbol": "E"},
    "2609": {"bg": "#003399", "fg": "#ffffff", "text": "YML", "symbol": "Y"},
    "2615": {"bg": "#006699", "fg": "#ffffff", "text": "WHL", "symbol": "W"},
    "3008": {"bg": "#003366", "fg": "#ffffff", "text": "LARGAN", "symbol": "L"},
    "1519": {"bg": "#ff9900", "fg": "#ffffff", "text": "FE", "symbol": "⚡"},
    "1513": {"bg": "#0066cc", "fg": "#ffffff", "text": "SEC", "symbol": "⚡"},
    "1503": {"bg": "#cc0000", "fg": "#ffffff", "text": "SE", "symbol": "⚡"},
    "1514": {"bg": "#336699", "fg": "#ffffff", "text": "AL", "symbol": "⚡"},
    "3017": {"bg": "#009999", "fg": "#ffffff", "text": "AVC", "symbol": "❄️"},
    "3324": {"bg": "#006666", "fg": "#ffffff", "text": "CCI", "symbol": "❄️"},
    "3653": {"bg": "#008888", "fg": "#ffffff", "text": "J", "symbol": "❄️"},
    "2059": {"bg": "#cc6600", "fg": "#ffffff", "text": "KS", "symbol": "K"},
    "2383": {"bg": "#3366cc", "fg": "#ffffff", "text": "EMC", "symbol": "E"},
    "2368": {"bg": "#0055aa", "fg": "#ffffff", "text": "GCE", "symbol": "G"},
    "3037": {"bg": "#004488", "fg": "#ffffff", "text": "U", "symbol": "U"},
    "2327": {"bg": "#cc5500", "fg": "#ffffff", "text": "Y", "symbol": "Y"},
    "2409": {"bg": "#004499", "fg": "#ffffff", "text": "AUO", "symbol": "A"},
    "3481": {"bg": "#0088cc", "fg": "#ffffff", "text": "INX", "symbol": "I"},
    "1301": {"bg": "#cc0000", "fg": "#ffffff", "text": "FPC", "symbol": "F"},
    "1303": {"bg": "#003399", "fg": "#ffffff", "text": "NPC", "symbol": "N"},
    "1326": {"bg": "#990000", "fg": "#ffffff", "text": "FCFC", "symbol": "F"},
    "6505": {"bg": "#006633", "fg": "#ffffff", "text": "FPCC", "symbol": "F"},
    "2002": {"bg": "#cc5500", "fg": "#ffffff", "text": "CSC", "symbol": "C"},
    "2207": {"bg": "#cc0000", "fg": "#ffffff", "text": "HOTAI", "symbol": "H"},
    "1216": {"bg": "#ff3300", "fg": "#ffffff", "text": "UNI", "symbol": "U"},
    "2412": {"bg": "#003399", "fg": "#ffffff", "text": "CHT", "symbol": "C"}
}

# ==========================================
# 1. 垂直縱深產業鏈 (Supply Chain Ecosystem)
# ==========================================
CHAIN_DATA: Dict[str, Dict[str, Any]] = {
    "AI 伺服器與高速運算鏈": {
        "desc": "從核心晶片、先進封裝、散熱水冷、滑軌機殼到系統代工之完整供應鏈",
        "stocks": [
            {"code": "2330", "name": "台積電", "tier": "核心晶片製造", "price": 2415.0, "change_pct": -0.41, "market_cap": 272000, "volume": 36500},
            {"code": "2454", "name": "聯發科", "tier": "ASIC/核心晶片", "price": 4045.0, "change_pct": -4.49, "market_cap": 23500, "volume": 12800},
            {"code": "3661", "name": "世芯-KY", "tier": "客製化 ASIC 設計", "price": 2145.0, "change_pct": -3.16, "market_cap": 1710, "volume": 2200},
            {"code": "3443", "name": "創意", "tier": "ASIC 設計與 IP", "price": 1225.0, "change_pct": -6.49, "market_cap": 1640, "volume": 4200},
            {"code": "3711", "name": "日月光投控", "tier": "CoWoS 先進封裝", "price": 162.5, "change_pct": -1.73, "market_cap": 7100, "volume": 18200},
            {"code": "3017", "name": "奇鋐", "tier": "水冷板與 3D VC 散熱", "price": 542.0, "change_pct": -3.42, "market_cap": 2180, "volume": 14200},
            {"code": "3324", "name": "雙鴻", "tier": "液冷水冷散熱模組", "price": 685.0, "change_pct": -2.14, "market_cap": 610, "volume": 6800},
            {"code": "3653", "name": "健策", "tier": "均熱片與連接扣件", "price": 1395.0, "change_pct": 0.47, "market_cap": 1980, "volume": 2400},
            {"code": "2059", "name": "川湖", "tier": "伺服器高階滑軌", "price": 1095.0, "change_pct": -2.66, "market_cap": 1040, "volume": 1800},
            {"code": "8210", "name": "勤誠", "tier": "AI 伺服器高密度機殼", "price": 286.0, "change_pct": -1.38, "market_cap": 350, "volume": 4100},
            {"code": "2383", "name": "台光電", "tier": "高頻高速銅箔基板", "price": 483.5, "change_pct": -0.30, "market_cap": 1620, "volume": 6200},
            {"code": "2368", "name": "金像電", "tier": "高多層伺服器 PCB", "price": 223.0, "change_pct": -5.91, "market_cap": 1090, "volume": 14200},
            {"code": "6274", "name": "台燿", "tier": "高速傳輸 CCL", "price": 176.0, "change_pct": 1.09, "market_cap": 480, "volume": 11500},
            {"code": "2308", "name": "台達電", "tier": "大功率電源與水冷冷卻", "price": 412.0, "change_pct": -4.52, "market_cap": 10700, "volume": 15400},
            {"code": "2301", "name": "光寶科", "tier": "伺服器高效電源模組", "price": 106.0, "change_pct": -3.63, "market_cap": 2480, "volume": 16800},
            {"code": "2382", "name": "廣達", "tier": "AI 伺服器系統代工 (ODM)", "price": 286.5, "change_pct": -3.64, "market_cap": 11060, "volume": 42000},
            {"code": "6669", "name": "緯穎", "tier": "CSP 雲端白牌伺服器", "price": 1985.0, "change_pct": -3.33, "market_cap": 3470, "volume": 2900},
            {"code": "2317", "name": "鴻海", "tier": "GB200 機櫃整機組裝", "price": 213.5, "change_pct": -1.39, "market_cap": 29600, "volume": 78000},
            {"code": "3231", "name": "緯創", "tier": "GPU 運算基板與代工", "price": 113.0, "change_pct": -3.08, "market_cap": 3280, "volume": 38000}
        ]
    },
    "半導體製造與設備材料鏈": {
        "desc": "晶圓代工、設備耗材、無塵室工程與化學材料",
        "stocks": [
            {"code": "2330", "name": "台積電", "tier": "晶圓代工先進製程", "price": 2415.0, "change_pct": -0.41, "market_cap": 272000, "volume": 36500},
            {"code": "2303", "name": "聯電", "tier": "成熟製程與特殊技術", "price": 46.2, "change_pct": -9.77, "market_cap": 5800, "volume": 89000},
            {"code": "5347", "name": "世界", "tier": "8吋與12吋功率晶圓", "price": 88.2, "change_pct": -6.57, "market_cap": 1450, "volume": 14200},
            {"code": "3711", "name": "日月光投控", "tier": "全球龍頭封裝測試", "price": 162.5, "change_pct": -1.73, "market_cap": 7100, "volume": 18200},
            {"code": "2449", "name": "京元電子", "tier": "高階晶片測試服務", "price": 128.5, "change_pct": -2.83, "market_cap": 1570, "volume": 16500},
            {"code": "3131", "name": "弘塑", "tier": "濕製程與蝕刻設備", "price": 1620.0, "change_pct": -3.57, "market_cap": 470, "volume": 1400},
            {"code": "3583", "name": "辛耘", "tier": "自製濕製程與再生晶圓", "price": 412.0, "change_pct": -2.83, "market_cap": 330, "volume": 3200},
            {"code": "3680", "name": "家登", "tier": "EUV 極紫外光光罩盒", "price": 465.0, "change_pct": -1.27, "market_cap": 390, "volume": 1800},
            {"code": "6187", "name": "萬潤", "tier": "CoWoS 先進點膠與貼合", "price": 435.0, "change_pct": -3.97, "market_cap": 370, "volume": 4100},
            {"code": "1560", "name": "中砂", "tier": "金剛石鑽石碟與砂輪", "price": 315.0, "change_pct": -2.17, "market_cap": 450, "volume": 5200},
            {"code": "2404", "name": "漢唐", "tier": "高科技廠房與無塵室", "price": 375.0, "change_pct": -0.80, "market_cap": 720, "volume": 4800},
            {"code": "5536", "name": "聖暉*", "tier": "無塵室機電整合工程", "price": 348.0, "change_pct": 0.58, "market_cap": 420, "volume": 1800}
        ]
    },
    "重電電網與綠能儲能鏈": {
        "desc": "台電強韌電網計畫、美國變壓器外銷、太陽能風電與大型儲能",
        "stocks": [
            {"code": "1519", "name": "華城", "tier": "超高壓變壓器出口", "price": 635.0, "change_pct": -3.85, "market_cap": 1660, "volume": 8200},
            {"code": "1503", "name": "士電", "tier": "配電盤與重電變壓器", "price": 218.0, "change_pct": -1.80, "market_cap": 1140, "volume": 6900},
            {"code": "1513", "name": "中興電", "tier": "GIS 高壓氣體絕緣開關", "price": 168.0, "change_pct": -1.75, "market_cap": 840, "volume": 11200},
            {"code": "1514", "name": "亞力", "tier": "高鐵與半導體配電設備", "price": 114.0, "change_pct": -2.24, "market_cap": 290, "volume": 7600},
            {"code": "1609", "name": "大亞", "tier": "超高壓特高壓電纜", "price": 49.5, "change_pct": -1.59, "market_cap": 360, "volume": 18000},
            {"code": "1605", "name": "華新", "tier": "特鋼與高階電纜線路", "price": 32.8, "change_pct": -1.20, "market_cap": 1330, "volume": 21000},
            {"code": "6806", "name": "森崴能源", "tier": "離岸風電與統包工程", "price": 132.0, "change_pct": -0.75, "market_cap": 280, "volume": 5800},
            {"code": "6869", "name": "雲豹能源", "tier": "再生能源發電與儲能", "price": 215.0, "change_pct": 2.38, "market_cap": 250, "volume": 6200}
        ]
    },
    "矽光子 (CPO) 與光通訊鏈": {
        "desc": "共同封裝光學 (CPO)、光收發模組、雷射晶粒與光纖跳接元件",
        "stocks": [
            {"code": "3450", "name": "聯鈞", "tier": "矽光子雷射封裝測試", "price": 228.0, "change_pct": 1.79, "market_cap": 330, "volume": 42000},
            {"code": "6451", "name": "訊芯-KY", "tier": "CPO 模組 SiP 系統封裝", "price": 218.0, "change_pct": -1.36, "market_cap": 230, "volume": 8900},
            {"code": "3363", "name": "上詮", "tier": "光纖陣列通道與保偏跳線", "price": 186.0, "change_pct": -2.62, "market_cap": 190, "volume": 12500},
            {"code": "6442", "name": "光聖", "tier": "高頻光收發模組與連接器", "price": 412.0, "change_pct": 3.26, "market_cap": 270, "volume": 9800},
            {"code": "4977", "name": "眾達-KY", "tier": "800G 光收發模組出貨", "price": 96.5, "change_pct": 0.84, "market_cap": 75, "volume": 6200},
            {"code": "4979", "name": "華星光", "tier": "光收發晶粒與高速雲端應用", "price": 142.0, "change_pct": -2.07, "market_cap": 180, "volume": 15400}
        ]
    },
    "車用電子與智慧座艙鏈": {
        "desc": "車載鏡頭、車用功率元件、智慧座艙與機構線束",
        "stocks": [
            {"code": "3008", "name": "大立光", "tier": "高階車載鏡頭模組", "price": 2315.0, "change_pct": -8.21, "market_cap": 3090, "volume": 1400},
            {"code": "3406", "name": "玉晶光", "tier": "潛望式與車用鏡頭", "price": 458.0, "change_pct": -3.28, "market_cap": 520, "volume": 3200},
            {"code": "3665", "name": "貿聯-KY", "tier": "超高壓線束與連接解決方案", "price": 472.0, "change_pct": 0.85, "market_cap": 810, "volume": 4600},
            {"code": "6279", "name": "胡連", "tier": "車用端子與連接器", "price": 164.0, "change_pct": -1.20, "market_cap": 160, "volume": 2100},
            {"code": "8255", "name": "朋程", "tier": "車用二極體與碳化矽 SiC", "price": 178.5, "change_pct": -1.92, "market_cap": 180, "volume": 1600}
        ]
    },
    "金控金融與財富管理鏈": {
        "desc": "大型公股與民營金控、壽險核心與投資銀行",
        "stocks": [
            {"code": "2881", "name": "富邦金", "tier": "壽險/銀行/證券三冠王", "price": 90.8, "change_pct": 0.62, "market_cap": 12400, "volume": 26000},
            {"code": "2882", "name": "國泰金", "tier": "大型旗艦壽險金控", "price": 68.4, "change_pct": 0.87, "market_cap": 10100, "volume": 28000},
            {"code": "2891", "name": "中信金", "tier": "消金龍頭與海外獲利王", "price": 36.2, "change_pct": 1.48, "market_cap": 7100, "volume": 42000},
            {"code": "2886", "name": "兆豐金", "tier": "外匯旗艦公股龍頭", "price": 39.5, "change_pct": 1.67, "market_cap": 5800, "volume": 18000},
            {"code": "2884", "name": "玉山金", "tier": "綠色金融與科技銀行", "price": 28.5, "change_pct": 1.34, "market_cap": 4500, "volume": 21000},
            {"code": "2885", "name": "元大金", "tier": "證券/期貨/ETF 市佔王", "price": 32.8, "change_pct": 0.55, "market_cap": 4200, "volume": 16000},
            {"code": "2887", "name": "台新新光金", "tier": "新金控整合大架構", "price": 18.2, "change_pct": 0.95, "market_cap": 3100, "volume": 36000}
        ]
    }
}

# ==========================================
# 2. 熱門概念股題材 (Concept Themes)
# ==========================================
CONCEPT_DATA: Dict[str, Dict[str, Any]] = {
    "台積電大聯盟 (CoWoS設備耗材)": {
        "desc": "台積電擴建 CoWoS 與先進製程資本支出最直接受惠供應鏈",
        "stocks": [
            {"code": "3131", "name": "弘塑", "theme": "CoWoS 濕製程蝕刻機", "price": 1620.0, "change_pct": -3.57, "market_cap": 470, "volume": 1400},
            {"code": "3583", "name": "辛耘", "theme": "晶圓剝離與再生晶圓", "price": 412.0, "change_pct": -2.83, "market_cap": 330, "volume": 3200},
            {"code": "3680", "name": "家登", "theme": "光罩盒獨家合格供應商", "price": 465.0, "change_pct": -1.27, "market_cap": 390, "volume": 1800},
            {"code": "6187", "name": "萬潤", "theme": "點膠機/散熱片貼合機", "price": 435.0, "change_pct": -3.97, "market_cap": 370, "volume": 4100},
            {"code": "1560", "name": "中砂", "theme": "鑽石碟獨家專利配合", "price": 315.0, "change_pct": -2.17, "market_cap": 450, "volume": 5200},
            {"code": "2404", "name": "漢唐", "theme": "美國與台灣廠區無塵室", "price": 375.0, "change_pct": -0.80, "market_cap": 720, "volume": 4800},
            {"code": "6196", "name": "帆宣", "theme": "極紫外光 EUV 次系統組裝", "price": 148.0, "change_pct": -1.98, "market_cap": 290, "volume": 3600}
        ]
    },
    "GB200 / 輝達 (NVIDIA) 生態系": {
        "desc": "獲輝達 Blackwell 架構認證之關鍵伺服器、散熱、電源與機構件",
        "stocks": [
            {"code": "2382", "name": "廣達", "theme": "首波 GB200 NVL72 代工", "price": 286.5, "change_pct": -3.64, "market_cap": 11060, "volume": 42000},
            {"code": "2317", "name": "鴻海", "theme": "垂直整合機櫃與組裝", "price": 213.5, "change_pct": -1.39, "market_cap": 29600, "volume": 78000},
            {"code": "3017", "name": "奇鋐", "theme": "獲認證之水冷散熱板", "price": 542.0, "change_pct": -3.42, "market_cap": 2180, "volume": 14200},
            {"code": "2059", "name": "川湖", "theme": "GB200 指定專用滑軌", "price": 1095.0, "change_pct": -2.66, "market_cap": 1040, "volume": 1800},
            {"code": "2383", "name": "台光電", "theme": "NVL72 專屬高頻高速 CCL", "price": 483.5, "change_pct": -0.30, "market_cap": 1620, "volume": 6200},
            {"code": "6669", "name": "緯穎", "theme": "微軟/AWS 主要白牌伺服器", "price": 1985.0, "change_pct": -3.33, "market_cap": 3470, "volume": 2900},
            {"code": "2308", "name": "台達電", "tier": "GB200 大功率電源供應", "price": 412.0, "change_pct": -4.52, "market_cap": 10700, "volume": 15400}
        ]
    },
    "CPO 矽光子狂飆題材": {
        "desc": "解決 AI 運算銅線傳輸極限與高能耗之革命性光電整合技術",
        "stocks": [
            {"code": "3450", "name": "聯鈞", "theme": "CPO 雷射雷射封裝大單", "price": 228.0, "change_pct": 1.79, "market_cap": 330, "volume": 42000},
            {"code": "6451", "name": "訊芯-KY", "theme": "母公司鴻海支持 SiP 封裝", "price": 218.0, "change_pct": -1.36, "market_cap": 230, "volume": 8900},
            {"code": "3363", "name": "上詮", "theme": "與晶圓龍頭共同開發光纖陣列", "price": 186.0, "change_pct": -2.62, "market_cap": 190, "volume": 12500},
            {"code": "6442", "name": "光聖", "theme": "北美 CSP 大客戶直供", "price": 412.0, "change_pct": 3.26, "market_cap": 270, "volume": 9800},
            {"code": "3163", "name": "波若威", "theme": "高密度多通道光纖被動元件", "price": 142.5, "change_pct": -0.70, "market_cap": 115, "volume": 7600}
        ]
    },
    "液冷水冷散熱革命": {
        "desc": "TDP 突破 1000W 必備之水冷板、分流管 (Manifold) 與冷卻液分配器 (CDU)",
        "stocks": [
            {"code": "3017", "name": "奇鋐", "theme": "水冷板良率業界領先", "price": 542.0, "change_pct": -3.42, "market_cap": 2180, "volume": 14200},
            {"code": "3324", "name": "雙鴻", "theme": "完整 CDU 與液冷散熱模組", "price": 685.0, "change_pct": -2.14, "market_cap": 610, "volume": 6800},
            {"code": "3653", "name": "健策", "theme": "扣件與均熱片市佔過半", "price": 1395.0, "change_pct": 0.47, "market_cap": 1980, "volume": 2400},
            {"code": "8996", "name": "高力", "theme": "硬焊板式熱交換器", "price": 315.0, "change_pct": -1.87, "market_cap": 280, "volume": 3800},
            {"code": "6125", "name": "廣運", "theme": "散熱水冷監控與物流自動化", "price": 89.5, "change_pct": -2.40, "market_cap": 220, "volume": 8400}
        ]
    },
    "機器人與智慧自動化概念": {
        "desc": "輝達人形機器人 Project GR00T、工業視覺與協作機器人",
        "stocks": [
            {"code": "2359", "name": "所羅門", "theme": "3D 視覺 AI 辨識定位", "price": 148.5, "change_pct": 1.71, "market_cap": 255, "volume": 21000},
            {"code": "4566", "name": "時碩工業", "theme": "高階關節齒輪加工", "price": 88.0, "change_pct": -1.12, "market_cap": 90, "volume": 4200},
            {"code": "8374", "name": "羅昇", "theme": "友通集團自動化控制代理", "price": 118.0, "change_pct": -3.28, "market_cap": 130, "volume": 6900},
            {"code": "6188", "name": "廣明", "theme": "旗下達明機器人協作手臂", "price": 102.5, "change_pct": -2.38, "market_cap": 285, "volume": 8400},
            {"code": "4562", "name": "穎漢", "theme": "智慧型彎管機與機械手", "price": 63.5, "change_pct": -4.22, "market_cap": 65, "volume": 9800}
        ]
    },
    "政策重電四雄與強韌電網": {
        "desc": "台電十年 5645 億強韌電網計畫與海外輸配電大缺口",
        "stocks": [
            {"code": "1519", "name": "華城", "theme": "訂單能見度直達 2027", "price": 635.0, "change_pct": -3.85, "market_cap": 1660, "volume": 8200},
            {"code": "1503", "name": "士電", "theme": "重電設備訂單滿載", "price": 218.0, "change_pct": -1.80, "market_cap": 1140, "volume": 6900},
            {"code": "1513", "name": "中興電", "theme": "GIS 獨家統包標案主力", "price": 168.0, "change_pct": -1.75, "market_cap": 840, "volume": 11200},
            {"code": "1514", "name": "亞力", "theme": "晶圓代工建廠變電設備", "price": 114.0, "change_pct": -2.24, "market_cap": 290, "volume": 7600}
        ]
    },
    "高股息 ETF 萬億資金護體": {
        "desc": "0056 / 00878 / 00919 / 00929 等千億級巨型 ETF 重倉成份股",
        "stocks": [
            {"code": "3034", "name": "聯詠", "theme": "穩定高現金股利殖利率", "price": 498.0, "change_pct": -0.33, "market_cap": 3030, "volume": 3900},
            {"code": "2603", "name": "長榮", "theme": "超高未分配盈餘與配息", "price": 194.0, "change_pct": -0.76, "market_cap": 4180, "volume": 24000},
            {"code": "2891", "name": "中信金", "theme": "獲利創高穩定殖利率", "price": 36.2, "change_pct": 1.48, "market_cap": 7100, "volume": 42000},
            {"code": "2404", "name": "漢唐", "theme": "現金流充沛超高配息率", "price": 375.0, "change_pct": -0.80, "market_cap": 720, "volume": 4800},
            {"code": "3702", "name": "大聯大", "theme": "半導體通路龍頭穩定收息", "price": 76.5, "change_pct": -0.65, "market_cap": 1280, "volume": 7800}
        ]
    }
}
