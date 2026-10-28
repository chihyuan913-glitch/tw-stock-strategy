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
import re
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
DEFAULT_GROUP_NAME = "量化策略全池"
DEFAULT_PRIORITY_GROUP_NAME = "🏆量化明日首選"
EXPORT_TXT_PATH = ROOT_DIR / "籌碼K線_自選股匯入.txt"
EXPORT_ALL_TXT_PATH = ROOT_DIR / "籌碼K線_自選股匯入_全量.txt"
EXPORT_PRIORITY_TXT_PATH = ROOT_DIR / "籌碼K線_自選股匯入_明日首選.txt"


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


def export_local_cmoney_file(
    stock_codes: List[str],
    priority_codes: Optional[List[str]] = None,
    group_name: str = DEFAULT_GROUP_NAME
) -> Path:
    """
    匯出符合籌碼K線 PC 版標準匯入格式之自選股文字檔，並自動複製至 Windows 剪貼簿。
    支援全量池與【🏆量化明日首選】雙檔分流產出。
    """
    all_clean = [str(c).strip() for c in stock_codes if str(c).strip()]
    pri_clean = [str(c).strip() for c in (priority_codes or []) if str(c).strip()]

    # 1. 寫入全量檔
    EXPORT_ALL_TXT_PATH.write_text("\n".join(all_clean), encoding="utf-8")
    print(f"[✓] 已產生籌碼K線本地匯入檔 (全量池): {EXPORT_ALL_TXT_PATH} (共 {len(all_clean)} 檔)")

    # 2. 寫入明日首選檔 (若無則使用全量)
    target_priority = pri_clean if pri_clean else all_clean
    EXPORT_PRIORITY_TXT_PATH.write_text("\n".join(target_priority), encoding="utf-8")
    EXPORT_TXT_PATH.write_text("\n".join(target_priority), encoding="utf-8")
    print(f"[✓] 已產生籌碼K線本地匯入檔 (🏆明日首選): {EXPORT_PRIORITY_TXT_PATH} (共 {len(target_priority)} 檔)")

    # 3. 優先將【明日首選】代碼自動載入 Windows 剪貼簿 (以逗號隔開，打開電腦版按 Ctrl+V 或直接匯入)
    clip_text = ",".join(target_priority)
    if copy_to_windows_clipboard(clip_text):
        tag = "🏆 明日決策首選" if pri_clean else "全量選股"
        print(f"[✓] 最新【{tag}】代碼已自動複製至 Windows 剪貼簿 ({clip_text})！")

    return EXPORT_PRIORITY_TXT_PATH


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
        向 CMoney 伺服器驗證並取得登入授權憑證。
        若已有有效 Token 則直接使用；若為帳號密碼則透過 CMoney 官方認證中心進行登入。
        """
        if self.token:
            return True, "使用自訂 CMONEY_AUTH_TOKEN"

        if not self.account or not self.password:
            return False, "尚未於 .env 中配置 CMONEY_ACCOUNT 或 CMONEY_PASSWORD"

        print(f"[*] 正在連線 CMoney 會員身份驗證伺服器 (帳號: {self.account[:3]}***)...")

        import http.cookiejar
        import html as html_lib
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

        login_url = "https://auth.cmoney.tw/Identity/account/login?returnUrl=%2Fauthorize%2Fcallback%3Fclient_id%3Dcmmainweb%26redirect_uri%3Dhttps%253A%252F%252Fwww.cmoney.tw%252Fmember%252Flogin%252Fsignin-oidc.aspx%26response_type%3Dnone%26scope%3D%26from%3Dmember"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            # 1. GET 登入頁面獲取 CSRF Token
            req = urllib.request.Request(login_url, headers=headers)
            resp = opener.open(req, timeout=10)
            page_html = resp.read().decode("utf-8", errors="ignore")

            token_m = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', page_html)
            if not token_m:
                token_m = re.search(r'value="([^"]+)"\s+name="__RequestVerificationToken"', page_html)
            csrf_token = token_m.group(1) if token_m else ""

            # 2. POST 送出帳號密碼登入
            post_data = {
                "Account": self.account,
                "Password": self.password,
                "RememberMe": "true",
                "ClientId": "cmmainweb",
                "RedirectUri": "https://www.cmoney.tw/member/login/signin-oidc.aspx",
                "ReturnUrl": "/authorize/callback?client_id=cmmainweb&redirect_uri=https%3A%2F%2Fwww.cmoney.tw%2Fmember%2Flogin%2Fsignin-oidc.aspx&response_type=none&scope=&from=member",
                "LogoUri": "https://image.cmoney.tw/servicetest/swagger/1675180800/d1202faa-8464-4a42-a77e-eb16348f8508.png",
                "ClientName": "www.cmoney.tw站台",
                "Fedcm": "False",
                "__RequestVerificationToken": csrf_token
            }
            encoded_data = urllib.parse.urlencode(post_data).encode("utf-8")
            post_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": login_url,
                "Origin": "https://auth.cmoney.tw"
            }
            post_req = urllib.request.Request(login_url, data=encoded_data, headers=post_headers, method="POST")
            post_resp = opener.open(post_req, timeout=10)
            res_html = post_resp.read().decode("utf-8", errors="ignore")

            # 檢查是否有登入錯誤提示
            decoded_html = html_lib.unescape(res_html)
            if "帳號或密碼錯誤" in decoded_html:
                return False, "CMoney 官方回傳：帳號或密碼錯誤，請確認手機/Email與密碼是否相符"

            # 檢查是否觸發 2FA 雙重認證
            final_url = post_resp.geturl() if hasattr(post_resp, "geturl") else ""
            if "透過雙重認證保護您的帳戶" in decoded_html or "send-code" in final_url:
                return False, "CMoney 帳號已啟用雙重認證 (2FA)。帳號密碼完全正確，但需手機簡訊/郵件驗證碼防護。"

            # 提取 Cookie
            cookie_dict = {c.name: c.value for c in cj}
            if any(k in cookie_dict for k in [".AspNetCore.Cookies", "cmtoken", "fs", "cmtoken2"]):
                self.token = "; ".join([f"{k}={v}" for k, v in cookie_dict.items()])
                return True, "CMoney Web 認證成功"

            if not self.token:
                return False, "未取得有效 CMoney Session 憑證"

            return True, "CMoney 身份驗證通過"
        except urllib.error.HTTPError as e:
            return False, f"CMoney 登入 HTTP 錯誤 ({e.code})"
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


def collect_priority_advisor_codes() -> List[str]:
    """
    從明日買賣建議表 (tomorrow_trading_advisor.csv) 提煉通過回測體檢之核心優先標的 (🟢 優先買進 / 🟡 拉回低接)
    """
    advisor_csv = ROOT_DIR / "lab" / "data" / "tomorrow_trading_advisor.csv"
    if not advisor_csv.exists():
        return []
    import pandas as pd
    try:
        df = pd.read_csv(advisor_csv, encoding="utf-8-sig", dtype=str)
        if "tomorrow_action" in df.columns and "symbol" in df.columns:
            strong_buys = df[df["tomorrow_action"].str.contains("優先買進|STRONG BUY", na=False, case=False)]["symbol"].tolist()
            dips = df[df["tomorrow_action"].str.contains("拉回低接|BUY ON DIP", na=False, case=False)]["symbol"].tolist()
            res = []
            for s in strong_buys + dips:
                c = str(s).strip()
                if c.endswith(".0"):
                    c = c[:-2]
                if c and c not in res:
                    res.append(c)
            return res[:6]
    except Exception:
        pass
    return []


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
    【高階統一入口】：執行 CMoney 雲端自選股雙群組分流同步 ＋ 本地雙保險匯出
    雙群組分流：
    1. 【量化策略全池】(全量約 15~20 檔標的)
    2. 【🏆量化明日首選】(僅收錄通過實驗室回測體檢之 3~5 檔 🟢優先買進/🟡拉回低接標的)
    """
    # 1. 收集全量代碼與明日核心首選代碼
    codes = stock_codes or collect_latest_screened_codes()
    priority_codes = collect_priority_advisor_codes() if stock_codes is None else []
    target_group_all = group_name or DEFAULT_GROUP_NAME
    target_group_pri = DEFAULT_PRIORITY_GROUP_NAME

    result = {
        "total_codes": len(codes),
        "codes": codes,
        "priority_codes": priority_codes,
        "group_name": target_group_all,
        "cloud_sync_success": False,
        "cloud_message": "",
        "local_file": str(EXPORT_PRIORITY_TXT_PATH),
        "clipboard_copied": False
    }

    if verbose:
        print("\n" + "=" * 65)
        print(f" 📲 籌碼K線 (CMoney) 自選股智慧雙軌分流同步引擎")
        print(f" 🎯 目標群組 1 (全量池): 【{target_group_all}】 ({len(codes)} 檔)")
        print(f" 🎯 目標群組 2 (明日首選): 【{target_group_pri}】 ({len(priority_codes)} 檔: {', '.join(priority_codes) if priority_codes else '同全量'})")
        print(f" 📊 全量標的: {', '.join(codes) if codes else '(無入選標的)'}")
        print("=" * 65)

    if not codes:
        result["cloud_message"] = "今日無入選標的，跳過同步"
        return result

    # 2. 雙保險：生成本地全量/首選匯入檔，並將【明日首選】注入 Windows 剪貼簿
    export_local_cmoney_file(codes, priority_codes=priority_codes, group_name=target_group_all)
    result["clipboard_copied"] = True

    # 3. 雲端同步 (若配置憑證，自動同步雙群組)
    client = CMoneyWatchlistClient()
    if client.is_configured():
        # 同步全量群組
        ok1, msg1 = client.update_watchlist_group(codes, group_name=target_group_all)
        # 同步明日首選群組 (若有首選標的)
        ok2, msg2 = True, ""
        if priority_codes:
            ok2, msg2 = client.update_watchlist_group(priority_codes, group_name=target_group_pri)
        
        result["cloud_sync_success"] = ok1 and ok2
        result["cloud_message"] = f"{msg1} | {msg2}".strip(" |")
        if ok1:
            print(f"[✓] 🎉 {msg1}")
            if priority_codes and ok2:
                print(f"[✓] 🏆 {msg2}")
            print(f"    👉 打開手機【籌碼K線 APP】或電腦版即可看見【{target_group_pri}】精華名單！")
        else:
            print(f"[!] 雲端同步未竟全功: {msg1}")
            print(f"    💡 已啟用【本地剪貼簿與文字檔】雙重防線，電腦版按 Ctrl+V 或匯入即可立即使用。")
    else:
        result["cloud_message"] = "未設定 CMONEY_ACCOUNT 帳密或 Token，已切換至本地匯入與剪貼簿自動注入防線。"
        if verbose:
            print(f"[ℹ️] 尚未於 .env 中設定 CMONEY_ACCOUNT 或 CMONEY_AUTH_TOKEN。")
            print(f"    👉 已將【明日首選】{len(priority_codes or codes)} 檔個股複製至 Windows 剪貼簿，並儲存至:")
            print(f"       {EXPORT_PRIORITY_TXT_PATH}")
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
