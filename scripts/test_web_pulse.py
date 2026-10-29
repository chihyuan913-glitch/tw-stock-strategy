import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from fastapi.testclient import TestClient
import line_bot_server

client = TestClient(line_bot_server.app)
res_html = client.get('/')
print('GET / status:', res_html.status_code)
html = res_html.text
print('Contains view-pulse:', 'id="view-pulse"' in html)
print('Contains d3.v7:', 'd3.v7.min.js' in html)
print('Contains Chart.js:', 'chart.js' in html)
print('Contains loadMarketPulseData:', 'loadMarketPulseData' in html)
print('Contains btn-tab-pulse:', 'id="btn-tab-pulse"' in html)
