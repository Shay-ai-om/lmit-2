# LMIT-2 Web UI 操作教學

這份文件對應 Windows 本機安裝版的 Web UI。

## 第一次啟動

1. 開啟 `LMIT-2 Wiki Console`。
2. 如果 `%APPDATA%\LMIT-2\wiki-only.toml` 還不存在，啟動器會先請你選 knowledge base 資料夾與 LMIT-1 raw Markdown 來源資料夾。
3. 進入 Web UI 後，仍可在 `Knowledge Base Path` 與 `Raw Source Paths` 修改路徑。
4. `Save Paths` 只會儲存路徑並初始化 knowledge base，不會執行 Ingest，也不會呼叫任何 LLM。
5. 路徑確認後，再執行 `Ingest`。

## 日常流程

1. `Ingest`：讀取 raw Markdown，複製成安全短檔名，產生 source notes、manifest 與 index。
2. `Lint`：檢查 knowledge base 必要目錄與索引是否存在。
3. `Search`：查詢已 ingest 的 source notes、raw copy 與 wiki 頁面。
搜尋結果可直接點 `Open Result`，若該結果對應 source note，還會額外出現 `Open Raw` 連到 raw markdown。
4. `Ask The Wiki`：根據目前 wiki 回答問題；`Ask And Save` 會把結果存入 `wiki/queries`。
5. `Sync Now`：使用已啟用的 LLM profile 更新 topic/entity 頁面。

## LLM Settings

- `Add Ollama`：建立本機 Ollama profile。
- `Add LM Studio`：建立 LM Studio 的 OpenAI-compatible profile，預設 `Base URL` 是 `http://localhost:1234/v1`。
- `Add LM Studio REST`：建立 LM Studio 原生 REST profile，預設 `Base URL` 是 `http://localhost:1234/api/v1`。
- `Add OpenAI`、`Add Gemini`：建立外部 API profile。
- 本機 LLM profile 預設 `Timeout Seconds` 是 `300`；慢模型可再往上調。
- `Active Profile` 是優先使用的 profile；`Fallback Order` 是失敗時的備援順序。
- `Save Settings` 只會儲存設定，不會直接呼叫 LLM。
- Web UI 不再顯示 stored key 狀態；密鑰仍可從 Windows 環境變數或安裝資料夾 `.env` 讀取。

## LM Studio

LM Studio 目前可用兩種接法：

- OpenAI-compatible：`http://localhost:1234/v1`
- Native REST：`http://localhost:1234/api/v1`

`Fetch Models` 會依目前 profile 的 provider 去抓模型：

- OpenAI-compatible profile：抓 `/v1/models`
- LM Studio REST profile：抓 `/api/v1/models`

`Model` 欄位必須填 API 回傳的 model id 或 model key，不一定等於下載頁顯示名稱。

如果 OpenAI-compatible profile 可以列出模型，但 `Ask The Wiki` 仍回傳 `400 Bad Request`，優先改用 `LM Studio REST` profile。

如果 `Ask The Wiki` 顯示 `timed out`，先把目前 profile 的 `Timeout Seconds` 提高，再重試。
`Sync Now` 若顯示 `timed out`，處理方式相同，因為它使用同一組 LLM profile 與 timeout 設定。

## API Keys

- 外部 OpenAI-compatible profile 若未明填 `API Key Environment Variable`，非本機 URL 會預設讀 `OPENAI_API_KEY`。
- Gemini profile 若未明填，會預設讀 `GEMINI_API_KEY`。
- LM Studio REST 若你在 LM Studio 啟用了 API token，可在 profile 填入例如 `LM_STUDIO_API_TOKEN`。
- `.env` 可放在安裝資料夾，例如：

```text
OPENAI_API_KEY=...
GEMINI_API_KEY=...
LM_STUDIO_API_TOKEN=...
```

## 疑難排解

- Web UI 打不開時，先確認 `127.0.0.1:8765` 沒被其他程式佔用。
- 啟動器錯誤記錄位於 `%APPDATA%\LMIT-2\logs`。
- Ingest 找不到資料時，檢查 `Raw Source Paths` 是否指向 LMIT-1 的 `output/raw`。
- `Save Paths` 卡住時，問題通常在 server 或磁碟/權限，不是 LLM。
- `Save Settings` 卡住時，先查看 `%APPDATA%\LMIT-2\logs`。
