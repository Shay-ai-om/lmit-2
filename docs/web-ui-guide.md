# LMIT-2 Web UI 操作教學

這份文件對應 Windows 本機安裝版的 Web UI。每次 Web UI 流程改動時，這份文件也要同步更新。

## 第一次啟動

1. 開啟 `LMIT-2 Wiki Console`。
2. 第一次啟動若還沒有 `%APPDATA%\LMIT-2\wiki-only.toml`，啟動器會先請你選擇 knowledge base 資料夾與 LMIT-1 raw Markdown 來源資料夾。
3. 進入 Web UI 後，仍可在 `Knowledge Base Path` 與 `Raw Source Paths` 修改路徑。
4. 按 `Save Paths` 只會儲存路徑並初始化 knowledge base，不會執行 Ingest，也不會呼叫任何 LLM。
5. 路徑確認後，再按 `Ingest`。

## 日常流程

1. `Ingest`：讀取 raw Markdown，複製成安全短檔名，產生 source notes、manifest 與 index。
2. `Lint`：檢查 knowledge base 必要目錄與索引是否存在。
3. `Search`：查詢已 ingest 的 source notes、raw copy 與 wiki 頁面。
4. `Ask The Wiki`：根據目前 wiki 回答問題；`Ask And Save` 會把結果存入 `wiki/queries`。
5. `Sync Now`：使用已啟用的 LLM profile 更新 topic/entity 頁面。

## LLM Settings

- `Add Ollama` 建立本機 Ollama profile；通常不需要 API key env。
- `Add LM Studio` 建立本機 OpenAI-compatible profile，預設 `Base URL` 是 `http://localhost:1234/v1`，通常不需要 API key env。
- `Add OpenAI` 或 `Add Gemini` 只保存環境變數名稱，不保存密鑰值。
- API key 可放在 Windows 使用者/系統環境變數，也可放在安裝資料夾的 `.env` 檔，例如 `OPENAI_API_KEY=...`。
- `Active Profile` 是優先使用的 profile；`Fallback Order` 是失敗時的備援順序。
- 修改 profile 後必須按 `Save Settings`。
- `Restore Defaults` 會重建預設 profile 清單。

## LM Studio Model 名稱

LM Studio 的 `Base URL` 用 `http://localhost:1234/v1` 是正確的前提是：LM Studio 的 Local Server 已在 Developer 頁面啟動，且 port 是 1234。

`Model` 欄位要填 API 回傳的 model id，不一定等於下載頁上看到的 HuggingFace 名稱。建議做法：

1. 在 LM Studio 載入模型並啟動 Local Server。
2. 在 LM Studio profile 按 `Fetch Models`。
3. Web UI 會呼叫 `/v1/models`，把第一個回傳 id 填入 `Model`，並在提示列列出可用 id。

也可以在 PowerShell 手動檢查：

```powershell
Invoke-RestMethod http://localhost:1234/v1/models | ConvertTo-Json -Depth 5
```

如果這個指令顯示「目標電腦拒絕連線」，代表 LM Studio server 沒有啟動、port 不同，或被防火牆/權限擋住；這不是 model 名稱錯誤。

## LM Studio 原生 REST API

LM Studio 0.4.0 之後官方推薦原生 REST API `/api/v1/*` 給模型管理、載入/卸載、stateful chat、MCP 等功能。LMIT-2 目前只需要一般 chat completion 與 citation workflow，所以先使用 OpenAI-compatible `/v1/chat/completions`，好處是設定簡單，也能與其他 OpenAI-compatible server 共用同一套 profile。

之後若要做「在 Web UI 內載入/卸載 LM Studio 模型」或「管理下載模型」，再接 LM Studio 原生 REST API 會比較合適。

## Auto Sync 與排程

- 第一次安裝不建議立即建立 Windows 排程，因為路徑、ingest 結果與 LLM profile 通常還沒確認。
- 安裝程式的 `Create Windows scheduled ingest, sync and lint tasks` 預設不勾選。
- 需要自動化時，再重新安裝並勾選排程，或用 Windows 工作排程器手動建立。

## 疑難排解

- Web UI 打不開時，先確認 `127.0.0.1:8765` 沒被其他程式佔用。
- 啟動器錯誤記錄位於 `%APPDATA%\LMIT-2\logs`。
- Ingest 找不到資料時，檢查 `Raw Source Paths` 是否指向 LMIT-1 的 `output/raw`。
- `Save Paths` 卡在 saving 時，通常是 server 未回應、路徑初始化被磁碟權限卡住，或另一個長時間操作仍在執行；它不會連 LLM。
- `Save Settings` 卡住時，先確認 Web UI server 還活著，並查看 `%APPDATA%\LMIT-2\logs`。
