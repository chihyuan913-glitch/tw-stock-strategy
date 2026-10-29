import urllib.request
import json

try:
    with urllib.request.urlopen('http://127.0.0.1:8989/api/market_pulse/treemap') as resp:
        data = json.loads(resp.read().decode('utf-8'))
        print('Total stocks:', data.get('total_stocks'))
        for s in data.get('root', {}).get('children', [])[:3]:
            print('Sector:', s.get('name'), 'count:', s.get('stock_count'), 'has_value:', 'value' in s)
            for c in s.get('children', [])[:2]:
                print('  Stock:', c.get('name'), 'code:', c.get('code'), 'price:', c.get('price'))
except Exception as e:
    print('Error:', e)
