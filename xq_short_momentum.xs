// =====================================================================
// XQ 全球贏家 (XS 選股腳本)
// 策略名稱：台股法人出貨破線做空選股策略 (Institutional Dumping & Downtrend Breakdown)
// 執行頻率：日線
// 適用對象：選股中心 / 融券與借券賣出策略雷達
// 核心邏輯：
// 1. 【籌碼面】外資或投信近 3 個交易日累計賣超大於 1,000 張
// 2. 【籌碼面】法人賣超張數佔當日總成交量比例大於 10%
// 3. 【籌碼面】近 5 日主力券商分點呈現淨賣超，且買入分點大於賣出分點 (散戶接刀、籌碼渙散)
// 4. 【技術面】收盤價跌破 20 日均線（月線），且月線斜率維持向下 (均線反壓蓋頭助跌)
// 5. 【技術面】近 5 日平均成交量大於 1,000 張，確保融券流動性充足
// 6. 【風險濾網】收盤價與 20 日均線負乖離率介於 0% 到 -8% 之間 (起跌破線區，避免追空被軋)
// 7. 【風險濾網】當日 K 棒無長下影線（下影線長度需小於實體 K 棒的一半）
// =====================================================================

[Input]
input: MA_Period(20, "月線均線天期(MA)");
input: MaxNegBias(-8.0, "月線負乖離率下限(%，避免追空過深)");
input: MinVol5D(1000, "近5日平均成交量低標(張)");
input: SellRatioMin(10.0, "法人賣超佔當日成交量最低比例(%)");
input: Sell3DMin(1000, "外資或投信近3日累計賣超門檻(張)");
input: LowerShadowLimit(0.5, "下影線佔實體K棒比例上限(預設0.5)");

// ---------------------------------------------------------------------
// 1. 技術面指標計算 (MA20、斜率、負乖離率、5日均量)
// ---------------------------------------------------------------------
variable: MA20(0), SlopeDown(false), NegBiasMA20(0), VolAvg5(0);
MA20 = Average(Close, MA_Period);

// 月線斜率向下 (今日 MA20 小於昨日 MA20，助跌空頭結構)
SlopeDown = (MA20 < MA20[1]);

// 20MA 乖離率 (%)
if MA20 > 0 then
    NegBiasMA20 = (Close - MA20) / MA20 * 100
else
    NegBiasMA20 = 0;

// 近 5 日平均成交量 (張)
VolAvg5 = Average(Volume, 5);

// ---------------------------------------------------------------------
// 2. K 棒形態濾網 (排除長下影線、主力低檔急拉承接)
// ---------------------------------------------------------------------
variable: K_Body(0), Lower_Shadow(0), ValidCandle(false);
K_Body = AbsValue(Close - Open);
Lower_Shadow = MinList(Open, Close) - Low;

// 下影線長度需小於實體 K 棒的一半
if K_Body > 0.05 then
    ValidCandle = (Lower_Shadow < LowerShadowLimit * K_Body)
else
    ValidCandle = (Lower_Shadow <= (Close * 0.005));

// ---------------------------------------------------------------------
// 3. 籌碼面資料讀取 (三大法人賣賣超)
// ---------------------------------------------------------------------
variable: ForeignNet(0), TrustNet(0), InstTotal(0);
variable: Foreign3DSell(0), Trust3DSell(0), InstSellRatio(0);

ForeignNet = GetField("外資買賣超", "D");
TrustNet   = GetField("投信買賣超", "D");
InstTotal  = GetField("三大法人買賣超", "D");

// 外資或投信近 3 個交易日累計賣超 (取負數累加)
Foreign3DSell = Summation(ForeignNet, 3);
Trust3DSell   = Summation(TrustNet, 3);

// 法人賣超張數佔當日成交量比例 (%)
if Volume > 0 and InstTotal < 0 then
    InstSellRatio = (AbsValue(InstTotal) / Volume) * 100
else
    InstSellRatio = 0;

// ---------------------------------------------------------------------
// 4. 籌碼面進階：前 15 大主力券商分點淨賣超與籌碼渙散度
// ---------------------------------------------------------------------
variable: Top15MajorNet(0), Top15Net5D(0), BranchDiff(0), BranchDiff5D(0);

// XQ 內建主力籌碼大數據欄位
Top15MajorNet = GetField("主力買賣超張數", "D");
Top15Net5D    = Summation(Top15MajorNet, 5);

// 「買賣家數差」 = 買進分點家數 - 賣出分點家數
// 當家數差為正數，代表買入分點家數 > 賣出分點家數 (籌碼散到眾多散戶手上，主力出貨渙散)
BranchDiff    = GetField("買賣家數差", "D");
BranchDiff5D  = Summation(BranchDiff, 5);

// ---------------------------------------------------------------------
// 5. 綜合做空策略條件判定
// ---------------------------------------------------------------------
// 條件一：外資或投信近 3 日累計賣超大於門檻 (張)
Condition1 = (Foreign3DSell <= -Sell3DMin) or (Trust3DSell <= -Sell3DMin);

// 條件二：法人賣超張數佔當日總成交量比例大於 10%
Condition2 = (InstSellRatio >= SellRatioMin);

// 條件三：近 5 日主力分點呈現淨賣超，籌碼渙散 (或單日主力大賣超)
Condition3 = (Top15Net5D < 0) or (Top15MajorNet < 0);

// 條件四：收盤價跌破 20 日均線且月線斜率向下
Condition4 = (Close <= MA20) and SlopeDown;

// 條件五：近 5 日平均成交量大於 1,000 張
Condition5 = (VolAvg5 >= MinVol5D);

// 條件六：收盤價與 20 日均線負乖離率介於 0% 到 -8% 之間 (起跌破線區，不追空過深)
Condition6 = (NegBiasMA20 <= 0) and (NegBiasMA20 >= MaxNegBias);

// 條件七：當日 K 棒無長下影線 (下影線長度 < 實體的一半)
Condition7 = ValidCandle;

// ---------------------------------------------------------------------
// 6. 操盤四大價位試算
// ---------------------------------------------------------------------
variable: ShortEntry(0), AddShort(0), TP1(0), TP2(0), StopLossCover(0);
ShortEntry     = Close;
AddShort       = MinList(Low * 0.995, Close * 0.97);
TP1            = MA20 * 0.88; // 負乖離 -12%
TP2            = MA20 * 0.80; // 負乖離 -20%
StopLossCover  = MaxList(MA20 * 1.02, High * 1.01); // 站回月線+2%無條件回補

// ---------------------------------------------------------------------
// 7. 觸發與選股結果輸出
// ---------------------------------------------------------------------
if Condition1 and Condition2 and Condition3 and Condition4 and Condition5 and Condition6 and Condition7 then
begin
    Ret = 1;
    OutputField(1, Close, "收盤價");
    OutputField(2, ShortEntry, "空單進場");
    OutputField(3, AddShort, "加空價位");
    OutputField(4, TP1, "停利TP1(-12%)");
    OutputField(5, TP2, "停利TP2(-20%)");
    OutputField(6, StopLossCover, "停損回補");
    OutputField(7, MA20, "月線(20MA)");
    OutputField(8, NegBiasMA20, "月線負乖離(%)");
    OutputField(9, VolAvg5, "5日均量(張)");
    OutputField(10, InstSellRatio, "法人賣超佔比(%)");
    OutputField(11, Foreign3DSell, "外資3日賣超(張)");
    OutputField(12, Trust3DSell, "投信3日賣超(張)");
end;
