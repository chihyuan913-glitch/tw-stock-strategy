with open('line_bot_server.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = '@app.get("/api/overview")'
new_routes = '''@app.get("/api/market_pulse/score")
async def get_market_pulse_score(refresh: bool = False):
    """提供 FinLab 風格台股大盤 0~10 分多空計分板與走勢圖資料"""
    try:
        from market_pulse import GLOBAL_MARKET_PULSE
        return GLOBAL_MARKET_PULSE.get_market_pulse_data(force_refresh=refresh)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/market_pulse/treemap")
async def get_market_pulse_treemap(metric: str = "market_cap", color_mode: str = "tw"):
    """提供全市場產業板塊熱力樹狀圖 (Treemap) 與報酬率分佈長條圖資料"""
    try:
        from market_pulse import GLOBAL_MARKET_PULSE
        return GLOBAL_MARKET_PULSE.get_treemap_data(metric=metric, color_mode=color_mode)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/market_pulse/refresh")
async def refresh_market_pulse():
    """手動強制刷新大盤指標與產業板塊資料"""
    try:
        from market_pulse import GLOBAL_MARKET_PULSE
        data = GLOBAL_MARKET_PULSE.get_market_pulse_data(force_refresh=True)
        return {"status": "ok", "score": data.get("score"), "timestamp": data.get("timestamp")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


'''

if target in content and '@app.get("/api/market_pulse/score")' not in content:
    content = content.replace(target, new_routes + target, 1)
    with open('line_bot_server.py', 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    print("SUCCESS: Routes inserted.")
else:
    print("WARNING: Already present or target not found.")
