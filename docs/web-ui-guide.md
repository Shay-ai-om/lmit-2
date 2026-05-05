# LMIT-2 Web UI 操作教學

這份文件對應目前 Web UI 布局。每次 Web UI 增減功能或改變操作流程時，請同步更新本文件。

## 第一次啟動

1. 開啟 `LMIT-2 Wiki Console`。
2. 在 `Knowledge Base Path` 填入 LMIT-2 要保存 wiki 的資料夾。
3. 在 `Raw Source Paths` 填入 LMIT-1 產出的 raw Markdown 資料夾；多個來源可一行一個。
4. 按 `Save Paths`。LMIT-2 會寫回目前使用者的 `%APPDATA%\LMIT-2\wiki-only.toml`，並初始化 knowledge base 目錄。

## 日常流程

1. `Ingest`：讀取 raw Markdown，複製成安全短檔名，產生 source notes、manifest 與 index。
2. `Lint`：檢查 knowledge base 必要目錄與索引是否存在。
3. `Search`：查詢已 ingest 的 source notes、raw copy 與 wiki 頁面。
4. `Ask The Wiki`：根據目前 wiki 回答問題；`Ask And Save` 會把結果存入 `wiki/queries`。

## LLM Settings

- `Add Ollama` 建立本機 Ollama profile；通常不需要 API key env。
- `Add LM Studio` 建立本機 OpenAI-compatible profile，預設使用 `http://localhost:1234/v1`，通常不需要 API key env。
- `Add OpenAI` 或 `Add Gemini` 只保存環境變數名稱，不保存密鑰值。
- API key 可放在 Windows 使用者/系統環境變數，也可放在安裝資料夾的 `.env` 檔，例如 `OPENAI_API_KEY=...`。
- `Active Profile` 是優先使用的 profile；`Fallback Order` 是失敗時的備援順序。
- 修改 profile 後必須按 `Save Settings`。
- `Restore Defaults` 會重建預設 profile 清單。

## Auto Sync 與排程

- `Sync Now` 會使用已啟用的 LLM profile 更新 topic/entity 頁面。
- 第一次安裝不建議立即建立排程，因為路徑、ingest 結果與 LLM profile 通常還沒確認。
- 需要自動化時，再重新安裝並勾選排程，或用 Windows 工作排程器手動建立。

## 疑難排解

- Web UI 打不開時，先確認 `127.0.0.1:8765` 沒被其他程式佔用。
- 啟動器錯誤記錄位於 `%APPDATA%\LMIT-2\logs`。
- Ingest 找不到資料時，檢查 `Raw Source Paths` 是否指向 LMIT-1 的 `output/raw`。
- `Save Paths` 只儲存路徑與初始化 KB，不會執行 Ingest，也不會呼叫任何 LLM。
- 如果按鈕顯示 timeout，通常是 server 未回應、路徑位於慢速/離線磁碟，或另一個長時間操作仍在執行。
