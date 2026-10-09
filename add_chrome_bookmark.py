#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import datetime
import shutil

# 設定標準輸出編碼為 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

bookmarks_path = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Bookmarks")

if not os.path.exists(bookmarks_path):
    print(f"[X] 找不到 Chrome 書籤檔案: {bookmarks_path}")
    sys.exit(1)

# 1. 備份
backup_path = bookmarks_path + ".bak"
shutil.copyfile(bookmarks_path, backup_path)
print(f"[*] 已備份原始書籤至: {backup_path}")

# 2. 讀取
with open(bookmarks_path, "r", encoding="utf-8") as f:
    data = json.load(f)

# 尋找目前最大的 id
max_id = 0
def get_max_id(node):
    global max_id
    if isinstance(node, dict):
        if 'id' in node:
            try:
                max_id = max(max_id, int(node['id']))
            except Exception:
                pass
        for v in node.values():
            get_max_id(v)
    elif isinstance(node, list):
        for item in node:
            get_max_id(item)

get_max_id(data)

# 計算微秒時間戳 (Windows FILETIME epoch: 1601-01-01)
now_ts = str(int((datetime.datetime.now(datetime.timezone.utc) - datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)).total_seconds() * 1000000))

folder_name = "台股選股策略"
bar = data.get("roots", {}).get("bookmark_bar", {})
if "children" not in bar:
    bar["children"] = []

# 檢查是否已存在同名資料夾
existing_folder = None
for item in bar["children"]:
    if item.get("type") == "folder" and item.get("name") == folder_name:
        existing_folder = item
        break

bookmarks_to_add = [
    {
        "name": "tw-stock-strategy (GitHub倉庫)",
        "url": "https://github.com/chihyuan913-glitch/tw-stock-strategy"
    },
    {
        "name": "GitHub Actions 雲端排程監控",
        "url": "https://github.com/chihyuan913-glitch/tw-stock-strategy/actions"
    },
    {
        "name": "LINE Developers Console",
        "url": "https://developers.line.biz/console/"
    }
]

if existing_folder:
    print(f"[*] 找到既有的「{folder_name}」書籤資料夾，正在更新書籤內容...")
    target_folder = existing_folder
    if "children" not in target_folder:
        target_folder["children"] = []
    
    existing_urls = {c.get("url") for c in target_folder["children"] if c.get("type") == "url"}
    for b in bookmarks_to_add:
        if b["url"] not in existing_urls:
            max_id += 1
            target_folder["children"].append({
                "date_added": now_ts,
                "date_last_used": "0",
                "id": str(max_id),
                "name": b["name"],
                "type": "url",
                "url": b["url"]
            })
else:
    print(f"[*] 正在建立全新的「{folder_name}」書籤資料夾...")
    max_id += 1
    folder_id = str(max_id)
    
    children_nodes = []
    for b in bookmarks_to_add:
        max_id += 1
        children_nodes.append({
            "date_added": now_ts,
            "date_last_used": "0",
            "id": str(max_id),
            "name": b["name"],
            "type": "url",
            "url": b["url"]
        })
        
    new_folder = {
        "date_added": now_ts,
        "date_last_used": "0",
        "date_modified": now_ts,
        "id": folder_id,
        "name": folder_name,
        "type": "folder",
        "children": children_nodes
    }
    bar["children"].append(new_folder)

# 3. 寫回
with open(bookmarks_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=3)

print(f"[OK] 成功將「{folder_name}」書籤資料夾寫入 Chrome 書籤列！")
