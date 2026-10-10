import sys
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
df = pd.read_csv('strategies/04_cb_pricing_ambush/backtest/detailed_trades_all.csv')
valid = df[df['20日均量(張)'] >= 500]

print('=== TOP 10 WINNERS ===')
top10 = valid.sort_values('報酬率%', ascending=False).head(10)
for idx, r in top10.iterrows():
    print(f"{r['股票代號']} {r['股票名稱']} ({r['CB簡稱']}): 進場 {r['進場日期']} (成本 {r['進場價格']}, 轉換價 {r['轉換價格']}) -> 出場 {r['出場日期']} ({r['出場價格']}) | 報酬 +{r['報酬率%']}% | 最高潛在漲幅 +{r['最高漲幅MFE%']}% | 募資 {r['發行規模(億)']}億 | 日均量 {r['20日均量(張)']}張")

print('\n=== BOTTOM 5 LOSERS ===')
bot5 = valid.sort_values('報酬率%', ascending=True).head(5)
for idx, r in bot5.iterrows():
    print(f"{r['股票代號']} {r['股票名稱']} ({r['CB簡稱']}): 進場 {r['進場日期']} (成本 {r['進場價格']}, 轉換價 {r['轉換價格']}) -> 出場 {r['出場日期']} ({r['出場價格']}) | 報酬 {r['報酬率%']}% | 最大回檔 {r['最大回檔MAE%']}% | 募資 {r['發行規模(億)']}億 | 日均量 {r['20日均量(張)']}張")
