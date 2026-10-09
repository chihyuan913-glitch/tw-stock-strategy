// =====================================================================
// XQ 全球贏家 (XS 選股腳本) V2.0 旗艦版
// 策略名稱：布林下軌逆勢法人買超 ＋ 止跌型態與反彈空間選股
// 執行頻率：日線
// 核心升級：
// 1. 日成交量大於 1000 張
// 2. 股價位於布林下軌 1% 之內或是跌破 5% 之內 (-5.0% <= BiasLB <= +1.0%)
// 3. 籌碼純度：三大法人買超佔成交量 >= 1.5%，且投信無巨額拋售
// 4. 型態止跌：留下影線(下影線佔振幅 >= 25%) 或 收紅K落底，拒絕長黑接刀
// 5. 盈虧比空間：距布林中軌 (20MA) 潛在反彈利潤空間 >= 2.0%
// =====================================================================

[Input]
input: Length(20, "布林通道天期(MA20)");
input: BandRange(2, "標準差倍數(預設2倍)");
input: MinVolLots(1000, "日成交量低標(張)");
input: MaxDistPct(1.0, "下軌上方容許偏離%(超跌區)");
input: MinDistPct(-5.0, "跌破下軌容許偏離%(洗盤區)");
input: MinInstRatio(1.5, "法人買超佔成交量最低%(純度門檻)");
input: MinUpsidePct(2.0, "距20MA中軌最低反彈空間%(盈虧比門檻)");
input: TrustSellLimit(500, "投信最大賣超上限(張)");

// ---------------------------------------------------------------------
// 1. 量能與布林通道計算
// ---------------------------------------------------------------------
variable: VolLots(0), DownBand(0), MidBand(0), UpBand(0);
variable: BiasLB(0), UpsidePotential(0);

VolLots = Volume;
MidBand = Average(Close, Length);
DownBand = BollingerBand(Close, Length, -BandRange);
UpBand = BollingerBand(Close, Length, BandRange);

if DownBand > 0 then
    BiasLB = (Close - DownBand) / DownBand * 100
else
    BiasLB = 0;

if Close > 0 then
    UpsidePotential = (MidBand - Close) / Close * 100
else
    UpsidePotential = 0;

// ---------------------------------------------------------------------
// 2. 讀取三大法人籌碼數據與佔比計算
// ---------------------------------------------------------------------
variable: TotalInst(0), ForeignNet(0), TrustNet(0), DealerNet(0);
variable: InstRatio(0), IsDualBuy(false);

TotalInst  = GetField("三大法人買賣超", "D");
ForeignNet = GetField("外資買賣超", "D");
TrustNet   = GetField("投信買賣超", "D");
DealerNet  = GetField("自營商買賣超", "D");

if VolLots > 0 then
    InstRatio = (TotalInst / VolLots) * 100
else
    InstRatio = 0;

IsDualBuy = (ForeignNet > 0) and (TrustNet > 0);

// ---------------------------------------------------------------------
// 3. K線止跌型態特徵判定 (防接長黑貫穿飛刀)
// ---------------------------------------------------------------------
variable: Amplitude(0), LowerShadow(0), LowerShadowRatio(0);
variable: IsCandleReversal(false);

Amplitude = High - Low;
if Amplitude <= 0 then Amplitude = 0.001;

LowerShadow = MinList(Open, Close) - Low;
LowerShadowRatio = LowerShadow / Amplitude;

// 止跌條件：留下影線(>=25%) 或 收紅K 或 未收在最低
IsCandleReversal = (LowerShadowRatio >= 0.25) or (Close >= Open) or (Close > (Low + Amplitude * 0.15));

// ---------------------------------------------------------------------
// 4. 複合條件判定
// ---------------------------------------------------------------------
Condition1 = (VolLots >= MinVolLots);
Condition2 = (BiasLB >= MinDistPct) and (BiasLB <= MaxDistPct);
Condition3 = (TotalInst > 0) and (InstRatio >= MinInstRatio) and (TrustNet >= -TrustSellLimit);
Condition4 = IsCandleReversal;
Condition5 = (UpsidePotential >= MinUpsidePct);

// ---------------------------------------------------------------------
// 5. 輸出符合標的與資訊欄位
// ---------------------------------------------------------------------
if Condition1 and Condition2 and Condition3 and Condition4 and Condition5 then
begin
    Ret = 1;
    OutputField(1, Close, "收盤價");
    OutputField(2, DownBand, "布林下軌");
    OutputField(3, BiasLB, "距下軌(%)");
    OutputField(4, UpsidePotential, "反彈空間(%)");
    OutputField(5, VolLots, "成交量(張)");
    OutputField(6, TotalInst, "三大法人買超(張)");
    OutputField(7, InstRatio, "法人佔比(%)");
    OutputField(8, ForeignNet, "外資買超(張)");
    OutputField(9, TrustNet, "投信買超(張)");
end;
