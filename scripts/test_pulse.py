import yfinance as yf
import pandas as pd
import numpy as np

twii = yf.download('^TWII', period='3y', interval='1d', progress=False)
close = twii['Close']
if isinstance(close, pd.DataFrame):
    close = close.iloc[:, 0]

ma20 = close.rolling(20).mean()
ma60 = close.rolling(60).mean()
slope20 = ma20.diff(5)
delta = close.diff()
gain = (delta.where(delta > 0, 0)).rolling(14).mean()
loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
rs = gain / (loss + 1e-9)
rsi14 = 100 - (100 / (1 + rs))

ema12 = close.ewm(span=12).mean()
ema26 = close.ewm(span=26).mean()
macd = ema12 - ema26
signal = macd.ewm(span=9).mean()
macd_hist = macd - signal

# Weekly MACD
weekly_close = close.resample('W').last()
w_ema12 = weekly_close.ewm(span=12).mean()
w_ema26 = weekly_close.ewm(span=26).mean()
w_macd = (w_ema12 - w_ema26).reindex(close.index, method='ffill')
w_signal = w_macd.ewm(span=9).mean()
w_hist = w_macd - w_signal

s1 = (close > ma20).astype(int)
s2 = (close > ma60).astype(int)
s3 = (slope20 > 0).astype(int)
s4 = (rsi14 > 50).astype(int)
s5 = (w_hist > 0).astype(int)
s6 = (macd_hist > 0).astype(int)
s7 = (close > close.shift(20)).astype(int)
s8 = (delta > 0).astype(int)
s9 = pd.Series(1, index=close.index) # Macro
s10 = pd.Series(1, index=close.index) # Rev YoY

total_score = s1 + s2 + s3 + s4 + s5 + s6 + s7 + s8 + s9 + s10
smooth_score = total_score.rolling(3, min_periods=1).mean()
latest_date = str(close.index[-1])[:10]
print(f"Latest Date: {latest_date}, Close: {close.iloc[-1]:.2f}")
print(f"Latest Raw Score: {total_score.iloc[-1]}, Smooth Score: {smooth_score.iloc[-1]:.1f}")
print("Recent 5 scores:", [round(x, 1) for x in smooth_score.tail(5).tolist()])
