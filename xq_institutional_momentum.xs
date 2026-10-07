// =====================================================================
// XQ 全球贏家 (XS 選股腳本)
// 策略名稱：台股法人籌碼集中起漲選股策略 (Institutional Momentum & Chip Concentration Breakout)
// 執行頻率：日線
// 適用對象：選股中心 / 盤後策略雷達 (支援三大法人籌碼與主力券商分點)
// 核心邏輯：
// 1. 【籌碼面】外資或投信近 3 個交易日累計買超大於 1,000 張
// 2. 【籌碼面】法人買超張數佔當日總成交量比例大於 10%
// 3. 【籌碼面】近 5 日前 15 大主力券商分點呈現淨買超，且買入分點家數小於賣出分點家數（籌碼集中）
// 4. 【技術面】收盤價站上 20 日均線（月線），且月線斜率維持向上
// 5. 【技術面】近 5 日平均成交量大於 1,000 張，確保流動性
// 6. 【風險濾網】收盤價與 20 日均線正乖離率小於 8%，確保起漲支撐區進場
// 7. 【風險濾網】當日 K 棒無爆量長上影線（上影線長度需小於實體 K 棒的一半）
// =====================================================================

[Input]
input: MA_Period(20, "月線均線天期(MA)");
input: Bias_Max(8.0, "月線正乖離率上限(%)");
input: MinVol5D(1000, "近5日平均成交量低標(張)");
input: InstRatioMin(10.0, "法人買超佔當日成交量最低比例(%)");
input: Inst3DMin(1000, "外資或投信近3日累計買超門檻(張)");
input: ShadowRatioLimit(0.5, "上影線佔實體K棒比例上限(預設0.5)");

// ---------------------------------------------------------------------
// 1. 技術面指標計算 (MA20、斜率、乖離率、5日均量)
// ---------------------------------------------------------------------
variable: MA20(0), SlopeUp(false), BiasMA20(0), VolAvg5(0);
MA20 = Average(Close, MA_Period);

// 月線斜率向上 (今日 MA20 大於昨日 MA20，助漲結構)
SlopeUp = (MA20 > MA20[1]);

// 20MA 乖離率 (%)
if MA20 > 0 then
    BiasMA20 = (Close - MA20) / MA20 * 100
else
    BiasMA20 = 0;

// 近 5 日平均成交量 (張)
VolAvg5 = Average(Volume, 5);

// ---------------------------------------------------------------------
// 2. K 棒形態濾網 (排除長上影線、避雷針出貨)
// ---------------------------------------------------------------------
variable: K_Body(0), Upper_Shadow(0), ValidCandle(false);
K_Body = AbsValue(Close - Open);
Upper_Shadow = High - MaxList(Open, Close);

// 上影線長度需小於實體 K 棒的一半
if K_Body > 0.05 then
    ValidCandle = (Upper_Shadow < ShadowRatioLimit * K_Body)
else
    ValidCandle = (Upper_Shadow <= (Close * 0.005));

// ---------------------------------------------------------------------
// 3. 籌碼面資料讀取 (三大法人買賣超)
// ---------------------------------------------------------------------
variable: ForeignNet(0), TrustNet(0), InstTotal(0);
variable: Foreign3D(0), Trust3D(0), InstRatio(0);

ForeignNet = GetField("外資買賣超", "D");
TrustNet   = GetField("投信買賣超", "D");
InstTotal  = GetField("三大法人買賣超", "D");

// 外資或投信近 3 個交易日累計買超 (張)
Foreign3D = Summation(ForeignNet, 3);
Trust3D   = Summation(TrustNet, 3);

// 法人買超張數佔當日成交量比例 (%)
if Volume > 0 then
    InstRatio = (InstTotal / Volume) * 100
else
    InstRatio = 0;

// ---------------------------------------------------------------------
// 4. 籌碼面進階：前 15 大主力券商分點與買賣家數集中度
// ---------------------------------------------------------------------
variable: Top15MajorNet(0), Top15Net5D(0), BranchDiff(0), BranchDiff5D(0);

// XQ 內建主力籌碼大數據欄位
// 「主力買賣超張數」 = 前 15 大買超券商合計張數 - 前 15 大賣超券商合計張數
Top15MajorNet = GetField("主力買賣超張數", "D");
Top15Net5D    = Summation(Top15MajorNet, 5);

// 「買賣家數差」 = 買進分點家數 - 賣出分點家數
// 當買入分點家數 < 賣出分點家數時，家數差為負數 (代表少數主力集中吃貨，籌碼集中)
BranchDiff    = GetField("買賣家數差", "D");
BranchDiff5D  = Summation(BranchDiff, 5);

// ---------------------------------------------------------------------
// 5. 綜合策略條件判定
// ---------------------------------------------------------------------
// 條件一：外資或投信近 3 日累計買超大於 1,000 張
Condition1 = (Foreign3D >= Inst3DMin) or (Trust3D >= Inst3DMin);

// 條件二：法人買超張數佔當日總成交量比例大於 10%
Condition2 = (InstRatio >= InstRatioMin);

// 條件三：近 5 日前 15 大主力分點呈現淨買超，且買入分點家數小於賣出分點家數 (籌碼集中)
Condition3 = (Top15Net5D > 0) and (BranchDiff5D < 0 or BranchDiff < 0);

// 條件四：收盤價站上 20 日均線且月線斜率向上
Condition4 = (Close >= MA20) and SlopeUp;

// 條件五：近 5 日平均成交量大於 1,000 張
Condition5 = (VolAvg5 >= MinVol5D);

// 條件六：收盤價與 20 日均線正乖離率小於 8% (0% <= Bias < 8%)
Condition6 = (BiasMA20 >= 0) and (BiasMA20 < Bias_Max);

// 條件七：當日 K 棒無爆量長上影線 (上影線長度 < 實體的一半)
Condition7 = ValidCandle;

// ---------------------------------------------------------------------
// 6. 觸發與選股結果輸出
// ---------------------------------------------------------------------
if Condition1 and Condition2 and Condition3 and Condition4 and Condition5 and Condition6 and Condition7 then
begin
    Ret = 1;
    OutputField(1, Close, "收盤價");
    OutputField(2, MA20, "月線(20MA)");
    OutputField(3, BiasMA20, "月線乖離率(%)");
    OutputField(4, VolAvg5, "5日均量(張)");
    OutputField(5, InstRatio, "法人佔成交量(%)");
    OutputField(6, Foreign3D, "外資3日累計(張)");
    OutputField(7, Trust3D, "投信3日累計(張)");
    OutputField(8, Top15Net5D, "前15大主力5日淨買超(張)");
    OutputField(9, BranchDiff5D, "近5日買賣家數差");
end;
