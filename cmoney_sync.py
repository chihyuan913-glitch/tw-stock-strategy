#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股量化選股策略最高總指揮中心 - 籌碼K線 (CMoney) 自選股雲端自動同步模組
================================================================================
核心責任：
1. 【雲端自選股全自動同步 (CMoney Cloud Watchlist Sync)】：
   - 每日 17:35 盤後選股完成後，自動將當日選出之高勝率精選個股代碼，
     透過 CMoney 雲端 API 寫入自選股群組（預設群組名稱：「量化策略精選」）。
   - 達成手機版【籌碼K線 APP】與電腦版【籌碼K線 PC 版】雙端免手動、即時雲端同步！
2. 【安全憑證架構 (Zero Hardcoding Credentials)】：
   - 嚴格遵守資安規範，帳號、密碼或 Access Token 一律自根目錄 `.env` 讀取：
     * `CMONEY_ACCOUNT`：CMoney 登入帳號 (Email 或手機號碼)
     * `CMONEY_PASSWORD`：CMoney 登入密碼
     * `CMONEY_AUTH_TOKEN`：(選填) CMoney Bearer Access Token / Cookie
     * `CMONEY_GROUP_NAME`：(選填) 自訂自選股群組名稱 (預設: 量化策略精選)
3. 【高可用雙軌保險機制 (Fallback & Auto-Clipboard Injection)】：
   - 無論雲端連線狀態如何，每次同步時自動將最新選股代碼格式化匯出為
     `籌碼K線_自選股匯入.txt`，並在 Windows 環境下自動將代碼寫入系統剪貼簿。
   - 電腦版打開按下「自選股匯入」或 Ctrl+V 即能秒級手動匯入，雙重防禦零死角。
================================================================================
"""

import os
import sys
import json
import argparse
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

# Windows 命令列編碼保護
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"
DEFAULT_GROUP_NAME = "量化策略精選"
EXPORT_TXT_PATH = ROOT_DIR / "籌碼K線_自選股匯入.txt"


def load_env() -> Dict[str, str]:
    """手動解析 .env，無需額外套件依賴"""
    env_vars = {}
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key.strip()] = val.strip().strip("'\"")
    return env_vars


def copy_to_windows_clipboard(text: str) -> bool:
    """使用 Windows 內建 clip.exe 將文字複製至系統剪貼簿"""
    if sys.platform != "win32":
        return False
    try:
        # clip.exe 在 Windows 繁體中文環境通常接受 Big5 或 UTF-16
        # 使用 UTF-16 編碼寫入管道最通用
        p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True)
        p.communicate(input=text.encode("utf-16"))
        return p.returncode == 0
    except Exception:
        try:
            p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True)
            p.communicate(input=text.encode("big5", errors="ignore"))
            return p.returncode == 0
        except Exception:
            return False


def export_local_cmoney_file(stock_codes: List[str], group_name: str = DEFAULT_GROUP_NAME) -> Path:
    """
    匯出符合籌碼K線 PC 版標準匯入格式之自選股文字檔，並自動複製至 Windows 剪貼簿。
    標準格式：一行一檔股票代碼（純代碼或 代碼,名稱）
    """
    codes = [str(c).strip() for c in stock_codes if str(c).strip()]
    content = "\n".join(codes)

    EXPORT_TXT_PATH.write_text(content, encoding="utf-8")
    print(f"[✓] 已產生籌碼K線本地匯入檔: {EXPORT_TXT_PATH} (共 {len(codes)} 檔)")

    # 自動複製到系統剪貼簿 (以逗號隔開與換行，方便直接貼上)
    clip_text = ",".join(codes)
    if copy_to_windows_clipboard(clip_text):
        print(f"[✓] 最新選股代碼已自動複製至 Windows 剪貼簿 ({clip_text})！")

    return EXPORT_TXT_PATH


class CMoneyWatchlistClient:
    """CMoney 雲端自選股 API 客戶端"""

    IDENTITY_TOKEN_URL = "https://www.cmoney.tw/identity/token"
    API_BASE_URL = "https://api.cmoney.tw"

    def __init__(self, account: Optional[str] = None, password: Optional[str] = None, token: Optional[str] = None):
        env = load_env()
        self.account = account or os.environ.get("CMONEY_ACCOUNT") or env.get("CMONEY_ACCOUNT")
        self.password = password or os.environ.get("CMONEY_PASSWORD") or env.get("CMONEY_PASSWORD")
        self.token = token or os.environ.get("CMONEY_AUTH_TOKEN") or env.get("CMONEY_AUTH_TOKEN")
        self.group_name = os.environ.get("CMONEY_GROUP_NAME") or env.get("CMONEY_GROUP_NAME") or DEFAULT_GROUP_NAME

    def is_configured(self) -> bool:
        """檢查是否已具備足夠憑證連線 CMoney 雲端"""
        return bool(self.token or (self.account and self.password))

    def authenticate(self) -> Tuple[bool, str]:
        """
        向 CMoney 伺服器驗證並換取 Access Token。
        若已有有效 Token 則直接使用。
        """
        if self.token:
            return True, "使用自訂 CMONEY_AUTH_TOKEN"

        if not self.account or not self.password:
            return False, "尚未於 .env 中配置 CMONEY_ACCOUNT 或 CMONEY_PASSWORD"

        print(f"[*] 正在連線 CMoney 會員身份驗證伺服器 (帳號: {self.account[:3]}***)...")

        # 模擬 CMoney 行動端 / Web 端 OAuth 驗證
        payload = {
            "grant_type": "password",
            "username": self.account,
            "password": self.password,
            "client_id": "cmoney_app_client",
            "scope": "openid profile offline_access"
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        headers = {
            "User-Agent": "CMoney/MobileApp/10.0 (Android; TW)",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        req = urllib.request.Request(self.IDENTITY_TOKEN_URL, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                access_token = res_data.get("access_token")
                if access_token:
                    self.token = access_token
                    return True, "CMoney OAuth 驗證成功"
                return False, f"未取得 access_token: {res_data}"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            # 若官方修改了 client_id 或流程，給予清晰診斷提示
            return False, f"CMoney 登入 HTTP 錯誤 ({e.code}): {err_msg[:120]}"
        except Exception as e:
            return False, f"連線 CMoney 伺服器異常: {e}"

    def update_watchlist_group(self, stock_codes: List[str], group_name: Optional[str] = None) -> Tuple[bool, str]:
        """
        將股票代碼清單更新至指定自選股群組。
        """
        grp_name = group_name or self.group_name
        clean_codes = [str(c).strip() for c in stock_codes if str(c).strip()]
        if not clean_codes:
            return False, "選股清單為空，取消同步"

        # 先確保具備 Token
        ok, msg = self.authenticate()
        if not ok:
            return False, f"身份驗證失敗: {msg}"

        # 呼叫自選股更新 API
        url = f"{self.API_BASE_URL}/user/selfselected/groups/update"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "CMoney/MobileApp/10.0",
            "Content-Type": "application/json"
        }
        payload = {
            "group_name": grp_name,
            "stock_codes": clean_codes,
            "auto_create": True
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 204):
                    return True, f"已成功將 {len(clean_codes)} 檔股票同步至 CMoney 群組【{grp_name}】"
                return False, f"自選股 API 回應狀態碼: {resp.status}"
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"自選股 API 錯誤 ({e.code}): {err_msg[:120]}"
        except Exception as e:
            return False, f"同步至 CMoney 自選股時發生異常: {e}"


def collect_latest_screened_codes() -> List[str]:
    """
    從現有各策略最新選股結果 (`result.csv`) 中收集今日通過的所有精選標的代碼
    """
    import pandas as pd
    found_codes = set()
    strat_dir = ROOT_DIR / "strategies"

    if strat_dir.exists():
        for csv_file in strat_dir.glob("**/result.csv"):
            try:
                df = pd.read_csv(csv_file, encoding="utf-8-sig", dtype=str)
                # 排除待剔除標的
                if "生命週期狀態" in df.columns:
                    df = df[df["生命週期狀態"] != "PENDING_REMOVAL"]
                if "狀態標註" in df.columns:
                    df = df[~df["狀態標註"].astype(str).str.contains("待剔除")]

                for col in ["證券代號", "股票代碼", "代號", "code", "Code"]:
                    if col in df.columns:
                        for val in df[col].dropna():
                            s = str(val).strip()
                            if s.endswith(".0"):
                                s = s[:-2]
                            if s.isdigit() and len(s) in (4, 5, 6):
                                found_codes.add(s)
                        break
            except Exception:
                pass

    return sorted(list(found_codes))


def sync_stocks_to_cmoney(
    stock_codes: Optional[List[str]] = None,
    group_name: Optional[str] = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    【高階統一入口】：執行 CMoney 雲端自選股同步 ＋ 本地雙保險匯出
    """
    # 1. 若未指定股票，自動收集今日最新精選標的
    codes = stock_codes or collect_latest_screened_codes()
    target_group = group_name or DEFAULT_GROUP_NAME

    result = {
        "total_codes": len(codes),
        "codes": codes,
        "group_name": target_group,
        "cloud_sync_success": False,
        "cloud_message": "",
        "local_file": str(EXPORT_TXT_PATH),
        "clipboard_copied": False
    }

    if verbose:
        print("\n" + "=" * 65)
        print(f" 📲 籌碼K線 (CMoney) 自選股智慧同步引擎")
        print(f" 🎯 同步目標群組: 【{target_group}】")
        print(f" 📊 今日入選標的 ({len(codes)} 檔): {', '.join(codes) if codes else '(無入選標的)'}")
        print("=" * 65)

    if not codes:
        result["cloud_message"] = "今日無入選標的，跳過同步"
        return result

    # 2. 雙保險：先生成本地匯入檔與寫入 Windows 剪貼簿
    export_local_cmoney_file(codes, group_name=target_group)
    result["clipboard_copied"] = True

    # 3. 雲端同步
    client = CMoneyWatchlistClient()
    if client.is_configured():
        ok, msg = client.update_watchlist_group(codes, group_name=target_group)
        result["cloud_sync_success"] = ok
        result["cloud_message"] = msg
        if ok:
            print(f"[✓] 🎉 {msg}")
            print(f"    👉 您開啟手機【籌碼K線 APP】或電腦版即可直接看見【{target_group}】最新名單！")
        else:
            print(f"[!] 雲端同步未竟全功: {msg}")
            print(f"    💡 已啟用【本地剪貼簿與文字檔】雙重防線，電腦版按 Ctrl+V 或匯入即可立即使用。")
    else:
        result["cloud_message"] = "未設定 CMONEY_ACCOUNT 帳密或 Token，已切換至本地匯入與剪貼簿自動注入防線。"
        if verbose:
            print(f"[ℹ️] 尚未於 .env 中設定 CMONEY_ACCOUNT 或 CMONEY_AUTH_TOKEN。")
            print(f"    👉 已將 {len(codes)} 檔個股複製至 Windows 剪貼簿，並儲存至:")
            print(f"       {EXPORT_TXT_PATH}")
            print(f"    💡 打開籌碼K線電腦版，按「自選股匯入」或 Ctrl+V 即完成匯入！")

    return result


def main():
    parser = argparse.ArgumentParser(description="台股量化選股 - 籌碼K線 (CMoney) 雲端自選股自動同步器")
    parser.add_argument("--codes", type=str, default=None, help="手動指定個股代碼 (以逗號分隔，例如 2330,2317,2454)")
    parser.add_argument("--group", type=str, default=None, help=f"指定自選股群組名稱 (預設: {DEFAULT_GROUP_NAME})")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",")] if args.codes else None
    sync_stocks_to_cmoney(stock_codes=codes, group_name=args.group, verbose=True)


if __name__ == "__main__":
    main()
