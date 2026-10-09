// =====================================================================
// 【XQ 全球贏家 - 策略雷達即時警示腳本 V2.0 旗艦版】
// 策略名稱：布林下軌觸底 ＋ 法人買超盤中即時雷達
// 腳本類型：警示腳本 (用於 XQ「策略雷達」盤中即時盯盤)
// 執行頻率：分鐘線 (建議 1 分鐘或 5 分鐘)
// =====================================================================

[Input]
input: Length(20, "布林天期(MA20)");
input: BandRange(2, "標準差倍數");
input: MinVolLots(1000, "日成交量低標(張)");
input: MaxDistPct(1.0, "下軌上方容許偏離%");
input: MinDistPct(-5.0, "跌破下軌容許偏離%");
input: MinUpsidePct(2.0, "距20MA最低反彈空間%");

// ---------------------------------------------------------------------
// 1. 讀取日線布林通道與前一日法人籌碼
// ---------------------------------------------------------------------
variable: DayMid(0), DayStd(0), DayLB(0), DayBias(0), DayUpside(0);
variable: LastInstTotal(0), LastForeign(0), LastTrust(0);

// 以日頻率計算布林下軌
DayMid = Average(GetField("收盤價", "D"), Length);
DayStd = StandardDev(GetField("收盤價", "D"), Length, 1);
DayLB  = DayMid - (BandRange * DayStd);

// 讀取前一交易日法人籌碼背景 (作為底池防護)
LastInstTotal = GetField("三大法人買賣超", "D")[1];
LastForeign   = GetField("外資買賣超", "D")[1];
LastTrust     = GetField("投信買賣超", "D")[1];

// ---------------------------------------------------------------------
// 2. 盤中即時現價與下軌關係判定
// ---------------------------------------------------------------------
if DayLB > 0 then
    DayBias = (Close - DayLB) / DayLB * 100
else
    DayBias = 0;

if Close > 0 then
    DayUpside = (DayMid - Close) / Close * 100
else
    DayUpside = 0;

// ---------------------------------------------------------------------
// 3. 盤中即時條件判定
// ---------------------------------------------------------------------
// 條件 A: 當日累積成交量突破 1000 張
Condition1 = (GetField("成交量", "D") >= MinVolLots);

// 條件 B: 現價切入布林下軌超跌支撐區 (-5% ~ +1%)
Condition2 = (DayBias >= MinDistPct) and (DayBias <= MaxDistPct);

// 條件 C: 前一日三大法人逆勢站在買方 (有主力撐腰)
Condition3 = (LastInstTotal > 0) and (LastTrust >= -300);

// 條件 D: 潛在反彈利潤空間足夠
Condition4 = (DayUpside >= MinUpsidePct);

// ---------------------------------------------------------------------
// 4. 觸發即時警示 (XQ 策略雷達即時叮咚彈窗/手機App推播)
// ---------------------------------------------------------------------
if Condition1 and Condition2 and Condition3 and Condition4 then
begin
    Ret = 1;
    // 輸出雷達即時訊息
    Print(File("C:\SKISXQ\User\radar_log.txt"), Date, Time, Symbol, SymbolName, "現價=", Close, "距下軌%=", DayBias, "反彈空間%=", DayUpside);
end;
