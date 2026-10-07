// =====================================================================
// XQ 全球贏家 (XS 選股腳本)
// 策略名稱：布林下軌逆勢法人買超選股
// 執行頻率：日線 (需有三大法人籌碼模組或日籌碼權限)
// 邏輯核心：
// 1. 日成交量大於 1000 張
// 2. 股價位於布林通道下軌 1% 之內或是跌破 5% 之內 (-5% <= 偏離率 <= +1%)
// 3. 籌碼面：三大法人逆勢合計買超，且投信未大量拋售
// =====================================================================

[Input]
input: Length(20, "布林通道天期(MA20)");
input: BandRange(2, "標準差倍數(預設2倍)");
input: MinVolLots(1000, "日成交量低標(張)");
input: MaxDistPct(1.0, "下軌上方容許偏離%(超跌反彈區)");
input: MinDistPct(-5.0, "跌破下軌容許偏離%(破底洗盤區)");
input: TrustSellLimit(500, "投信最大賣超上限(張，超過視為拋售)");

// ---------------------------------------------------------------------
// 1. 計算成交量 (日線頻率下 Volume 單位即為張)
// ---------------------------------------------------------------------
variable: VolLots(0);
VolLots = Volume;

// ---------------------------------------------------------------------
// 2. 計算布林通道 (下軌、中軌、偏離率)
// ---------------------------------------------------------------------
variable: DownBand(0), MidBand(0), UpBand(0), BiasLB(0);
MidBand = Average(Close, Length);
DownBand = BollingerBand(Close, Length, -BandRange);
UpBand = BollingerBand(Close, Length, BandRange);

if DownBand > 0 then
    BiasLB = (Close - DownBand) / DownBand * 100
else
    BiasLB = 0;

// ---------------------------------------------------------------------
// 3. 讀取三大法人籌碼數據 (日資料)
// ---------------------------------------------------------------------
variable: TotalInst(0), ForeignNet(0), TrustNet(0), DealerNet(0);
TotalInst  = GetField("三大法人買賣超", "D");
ForeignNet = GetField("外資買賣超", "D");
TrustNet   = GetField("投信買賣超", "D");
DealerNet  = GetField("自營商買賣超", "D");

// ---------------------------------------------------------------------
// 4. 條件判定
// ---------------------------------------------------------------------
// 條件一：流動性保護 (成交量 >= 1000 張)
Condition1 = (VolLots >= MinVolLots);

// 條件二：股價在布林下軌 1% 位置或跌破 5% 之內
Condition2 = (BiasLB >= MinDistPct) and (BiasLB <= MaxDistPct);

// 條件三：籌碼主力逆勢承接且投信無巨量倒貨
// (三大法人合計淨買超 > 0，且投信賣超未超過上限)
Condition3 = (TotalInst > 0) and (TrustNet >= -TrustSellLimit);

// ---------------------------------------------------------------------
// 5. 輸出符合標的與資訊欄位
// ---------------------------------------------------------------------
if Condition1 and Condition2 and Condition3 then
begin
    Ret = 1;
    OutputField(1, Close, "收盤價");
    OutputField(2, DownBand, "布林下軌");
    OutputField(3, BiasLB, "距下軌(%)");
    OutputField(4, VolLots, "成交量(張)");
    OutputField(5, TotalInst, "三大法人買超(張)");
    OutputField(6, ForeignNet, "外資買超(張)");
    OutputField(7, TrustNet, "投信買超(張)");
    OutputField(8, DealerNet, "自營商買超(張)");
end;
