# 套用與還原

這份目錄的本機來源是 commit `081b97d85a96a7f9b4564039cfbc600b692d77a2`，再加上工作樹版的 `cloud_sync.py`。這個組合不是任何 commit 物件。

執行時只檢查一般檔是否與 `isolate_pin.py` 裡的鎖定雜湊一致。這個雜湊不能防止鎖定檔和信任根一起被換掉，也不會在執行時重新證明 git 祖先。祖先比對寫在建立 `isolate_provenance.json` 的時候。

## 套用

1. 從已驗收候選複製一份新目錄，不要覆蓋候選或正式專案。
2. 不要把 `.env`、`.env.example`、`regime_config.json`、`watchlist.json`、`trade_history.csv`、`trade_history.json`、以及 `.db`、`.sqlite` 檔拷進去。出現這些檔時，隔離入口會拒絕且不開內容。
3. 從正式 HEAD `081b97d` 的複本建立時，套用 `081b97d-to-v3.patch`（不含被省略的 runtime/db 檔）。不要混用其他版本的 patch。
4. 重算 `isolate_lock.json` 的 sha256，必須等於 `isolate_pin.py` 的 `EXPECTED_LOCK_SHA256`。
5. 只在三個隔離變數都正確時執行 `python isolate_entry.py`。不要用 `uvicorn line_bot_server:app` 或 `uvicorn isolate_entry:app`。

必要變數：

- `TW_STOCK_ISOLATE=1`
- `TW_STOCK_ISOLATE_HOST=127.0.0.1`
- `TW_STOCK_ISOLATE_PORT` 為 1024–65535 且不是 8989

缺項、錯值、8989、非 `127.0.0.1`，結束碼都是 2。目錄驗證失敗的結束碼是 3。兩種都不會改啟動正式服務。

## 平台限制

已在 Windows 以真實 junction（`mklink /J`）實測：隔離入口會拒絕，代碼為 `symlink`。
目錄與檔案 symlink 因這台電腦沒有建立權限（WinError 1314）未實測。
其他 reparse point 類型未實測。

## 還原

刪除這份隔離複本即可。不要把它拷回正式目錄，也不要還原資料庫或交易檔。

## 能力邊界

通過驗證時只有 `/health` 與 `/isolate/manifest`。這不是儀表板或完整網站測試。
