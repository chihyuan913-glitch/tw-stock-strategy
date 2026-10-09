// =====================================================================
// 【XQ 全球贏家 - 自訂選股腳本 V2.0 旗艦版】
// 策略名稱：布林下軌超跌 ＋ 法人逆勢買超 ＋ 止跌型態與反彈空間
// 腳本類型：選股腳本 (日線頻率)
// 適用範圍：台股上市、上櫃全部普通股
// =====================================================================
// 【核心選股邏輯】
// 1. 日成交量 >= 1000 張 (排除無量小型股與流動性陷阱)
// 2. 股價位於布林下軌 1% 之內或是跌破 5% 之內 (-5.0% <= 偏離率 <= +1.0%)
// 3. 籌碼純度：三大法人合計買超 > 0，且佔成交量比重 >= 1.5%，投信無大量拋售
// 4. K線止跌型態：留長下影線(>=25%) 或 收紅K落底，拒絕長黑接刀
// 5. 盈虧比空間：距布林中軌 (20MA) 潛在反彈利潤空間 >= 2.0%
// =====================================================================

[Input]
input: Length(20, "布林天期(MA20)");
input: BandRange(2, "標準差倍數(預設2倍)");
input: MinVolLots(1000, "日成交量門檻(張)");
input: MaxDistPct(1.0, "下軌上方容許偏離%(超跌區)");
input: MinDistPct(-5.0, "跌破下軌容許偏離%(洗盤區)");
input: MinInstRatio(1.5, "法人買超佔比低標%(籌碼純度)");
input: MinUpsidePct(2.0, "距20MA最低反彈空間%(盈虧比)");
input: TrustSellLimit(500, "投信最大賣超上限(張，超過視為拋售)");

// ---------------------------------------------------------------------
// 1. 量能與布林通道計算 (採用原生相容演算法)
// ---------------------------------------------------------------------
variable: VolLots(0), MidBand(0), StdDevVal(0), UpBand(0), DownBand(0);
variable: BiasLB(0), UpsidePotential(0);

VolLots = Volume; // 日線下 Volume 單位即為張
MidBand = Average(Close, Length);
StdDevVal = StandardDev(Close, Length, 1);
UpBand = MidBand + (BandRange * StdDevVal);
DownBand = MidBand - (BandRange * StdDevVal);

// 計算收盤價距下軌偏離率
if DownBand > 0 then
    BiasLB = (Close - DownBand) / DownBand * 100
else
    BiasLB = 0;

// 計算距 20MA 中軌潛在反彈空間%
if Close > 0 then
    UpsidePotential = (MidBand - Close) / Close * 100
else
    UpsidePotential = 0;

// ---------------------------------------------------------------------
// 2. 讀取三大法人籌碼數據與純度計算
// ---------------------------------------------------------------------
variable: TotalInst(0), ForeignNet(0), TrustNet(0), DealerNet(0);
variable: InstRatio(0), IsDualBuy(false);

TotalInst  = GetField("三大法人買賣超", "D");
ForeignNet = GetField("外資買賣超", "D");
TrustNet   = GetField("投信買賣超", "D");
DealerNet  = GetField("自營商買賣超", "D");

// 法人買超佔比 = 三大法人買超張數 / 當日成交量 * 100%
if VolLots > 0 then
    InstRatio = (TotalInst / VolLots) * 100
else
    InstRatio = 0;

// 土洋同步同買判定 (外資 > 0 且 投信 > 0)
IsDualBuy = (ForeignNet > 0) and (TrustNet > 0);

// ---------------------------------------------------------------------
// 3. K線止跌型態判定 (防接長黑貫穿飛刀)
// ---------------------------------------------------------------------
variable: Amplitude(0), LowerShadow(0), LowerShadowRatio(0);
variable: IsCandleReversal(false);

Amplitude = High - Low;
if Amplitude <= 0 then Amplitude = 0.001;

// 下影線長度 = Min(開盤, 收盤) - 最低價
LowerShadow = MinList(Open, Close) - Low;
LowerShadowRatio = LowerShadow / Amplitude;

// 止跌條件：下影線佔振幅 >= 25% 或 收紅K落底 或 未收在最低
IsCandleReversal = (LowerShadowRatio >= 0.25) or (Close >= Open) or (Close > (Low + Amplitude * 0.15));

// ---------------------------------------------------------------------
// 4. 智慧星級評分 (0~100 分)
// ---------------------------------------------------------------------
variable: StarScore(50);
StarScore = 50;

if InstRatio >= 5.0 then StarScore = StarScore + 15
else if InstRatio >= 2.5 then StarScore = StarScore + 10;

if IsDualBuy then StarScore = StarScore + 15
else if TrustNet > 0 then StarScore = StarScore + 10;

if (Close >= Open) and (LowerShadowRatio >= 0.25) then StarScore = StarScore + 10
else if IsCandleReversal then StarScore = StarScore + 5;

if UpsidePotential >= 4.0 then StarScore = StarScore + 10;

// ---------------------------------------------------------------------
// 5. 五大條件嚴格檢核
// ---------------------------------------------------------------------
Condition1 = (VolLots >= MinVolLots);
Condition2 = (BiasLB >= MinDistPct) and (BiasLB <= MaxDistPct);
Condition3 = (TotalInst > 0) and (InstRatio >= MinInstRatio) and (TrustNet >= -TrustSellLimit);
Condition4 = IsCandleReversal;
Condition5 = (UpsidePotential >= MinUpsidePct);

// ---------------------------------------------------------------------
// 6. 觸發選股與欄位資訊輸出
// ---------------------------------------------------------------------
if Condition1 and Condition2 and Condition3 and Condition4 and Condition5 then
begin
    Ret = 1;
    OutputField(1, Close, "收盤價");
    OutputField(2, DownBand, "布林下軌");
    OutputField(3, BiasLB, "距下軌(%)");
    OutputField(4, UpsidePotential, "反彈空間(%)");
    OutputField(5, VolLots, "成交量(張)");
    OutputField(6, TotalInst, "法人買超(張)");
    OutputField(7, InstRatio, "法人佔比(%)");
    OutputField(8, ForeignNet, "外資買超(張)");
    OutputField(9, TrustNet, "投信買超(張)");
    OutputField(10, StarScore, "綜合評分");
end;
