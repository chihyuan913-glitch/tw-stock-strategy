import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from market_pulse import GLOBAL_MARKET_PULSE

data = GLOBAL_MARKET_PULSE.get_treemap_data()
root = data["root"]
print("Total sectors:", len(root["children"]))
for s in root["children"][:3]:
    children_sum = sum(c["value"] for c in s["children"])
    print(f"Sector {s['name']}: sector['value']={s.get('value')}, children_sum={children_sum}")
