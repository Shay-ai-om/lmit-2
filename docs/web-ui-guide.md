# LMIT-2 Web UI Guide

這份文件對應目前的 Windows 本機版 Web UI。

## 第一次啟動

1. 開啟 `LMIT-2 Wiki Console`。
2. 如果 `%APPDATA%\LMIT-2\wiki-only.toml` 不存在，啟動器會先要求選擇 knowledge base 資料夾與 LMIT-1 raw Markdown 來源資料夾。
3. 進入 Web UI 後，仍可在 `Knowledge Base Path` 與 `Raw Source Paths` 修改路徑。
4. 按 `Save Paths` 只會儲存路徑並初始化 knowledge base；它不會執行 Ingest，也不會呼叫任何 LLM。
5. 路徑確認後，再執行 `Ingest`。

## 日常流程

1. `Ingest`：讀取 raw Markdown，複製成安全短檔名，產生 source notes、`manifest.json`、精簡首頁與 `Source Catalog`。
2. 如果目前沒有啟用任何 LLM profile，`Ingest` 會先跳出警示。你可以先去設定 LLM，或明確選擇 `fallback ingest`。
3. `fallback ingest` 仍會保存 raw/source note/manifest traceability，但首頁不會退化成整頁 source dump。
4. `Lint`：檢查 knowledge base 必要目錄與索引是否存在。
5. `Search`：查詢 source notes、raw copy、system pages 與 wiki pages。
6. 搜尋結果可用 `Open Result` 打開目前文件；若結果對應 source note，還會出現 `Open Raw` 直接打開 raw markdown。
7. `Ask The Wiki`：根據目前 wiki 回答問題；`Ask And Save` 會把結果存入 `wiki/queries`。
8. `Ask The Wiki` 會串流顯示答案，只要模型已開始輸出 token，畫面就會持續更新。
9. `Sync Now`：背景執行 LLM auto sync，把已 ingest 的素材提升成 topic/entity pages，並更新首頁訊號。
10. `Stop Sync`：要求目前背景 sync 在當前 source 完成後停止，不會回滾已完成的 page update。
11. `Resume Sync`：從下一筆未完成 source 繼續，不會重跑已成功完成的 source。

## 首頁與 Source Catalog

- `wiki/index.md` 現在是高信號首頁，不再列出全量 sources。
- 完整的 imported source inventory 會寫到 `wiki/system/sources.md`。
- fallback ingest 後，首頁會保留 `pre-curation` 提示；之後就算 query save 或其他 refresh 重寫首頁，也不會把這個狀態洗掉。
- 首頁也會顯示最近一次 sync 摘要，以及最新的 query pages。
- `wiki/hubs/` 會自動維護三個核心 hub pages：
  - `knowledge-map.md`
  - `recent-work.md`
  - `open-questions.md`
- 這些 hub pages 是系統自動更新的高信號導航頁；你也可以另外在 `wiki/hubs/` 新增自己的手工 hub pages。

## LLM Settings

- `Add Ollama`：建立本機 Ollama profile。
- `Add LM Studio REST`：建立 LM Studio 原生 REST profile，預設 `Base URL` 為 `http://localhost:1234/api/v1`。
- `Add LiteLLM`：建立本機 LiteLLM proxy profile，預設 `Base URL` 為 `http://localhost:4000`。
- `Add OpenAI` / `Add Gemini`：建立遠端 API profile。
- `Fetch Models` 目前支援 OpenAI-compatible、LiteLLM 與 LM Studio REST profile。
- `Model` 欄位要填 API 回傳的 model id 或 model key。
- API key 只保存環境變數名稱，不保存密鑰值。
- 本機 LLM profile 預設 `Timeout Seconds = 300`；慢模型或長上下文可再往上調。
- `Ask The Wiki` 串流最適合本機 Ollama、LiteLLM 與 LM Studio REST；若 provider 不支援串流，結果會在完成時一次顯示。
- 修改 profile 後必須按 `Save Settings`。
- `Restore Defaults` 會重建預設 profile 清單。

## LM Studio

LMIT-2 預設只保留 LM Studio 原生 REST profile。

- Base URL：`http://localhost:1234/api/v1`
- `Model` 欄位要填 API 回傳的 id 或 key，不一定等於下載畫面看到的名稱

如果要確認模型清單，可先在本機執行：

```powershell
Invoke-RestMethod http://localhost:1234/api/v1/models | ConvertTo-Json -Depth 5
```

## LiteLLM

LiteLLM Proxy Server 官方 quick start 預設會跑在：

```text
http://localhost:4000
```

如果你的 LiteLLM proxy 啟用了 master key，可在 profile 內填 `LITELLM_API_KEY` 這類環境變數名稱。`Fetch Models` 會透過 LiteLLM 的 OpenAI-style `/models` 端點讀取可用模型。

## API Keys 與 `.env`

API key 可以來自：

- Windows 使用者環境變數
- Windows 系統環境變數
- 安裝資料夾下的 `.env` 檔

例如：

```text
OPENAI_API_KEY=...
GEMINI_API_KEY=...
LM_STUDIO_API_TOKEN=...
LITELLM_API_KEY=...
```

Web UI 不會顯示 stored key，只顯示 `API Key Environment Variable` 欄位。

## Auto Sync 與排程

- `Sync Now` 會在背景執行，不會把瀏覽器卡在單一 HTTP 請求上。
- `Stop Sync` 會在目前 source 完成後乾淨停下，不會強制中斷正在跑的那次 LLM 呼叫。
- `Resume Sync` 會沿用既有 `processed_sources` state，從下一筆未完成 source 繼續。
- 第一次安裝通常不建議立刻建立 Windows scheduled ingest / sync / lint tasks，因為路徑、ingest 結果與 LLM profile 往往還沒驗證完。

## 疑難排解

- `Save Paths` 卡住：先看 `%APPDATA%\LMIT-2\logs`。
- `Ingest` 找不到資料：確認 `Raw Source Paths` 是否指向 LMIT-1 的 `output/raw`。
- `Ingest` 跳出 no-LLM 警示：這不是錯誤，代表系統要你確認是否走 fallback ingest。
- `Ask The Wiki` timeout：先提高目前 profile 的 `Timeout Seconds`。
- `Sync Now` 背景任務失敗：先看 Web UI 顯示的錯誤內容，再檢查目前 LLM profile、模型名稱與 timeout。
- 要手動關閉 Web UI server，可執行：

```powershell
lmit-wiki stop --config "%APPDATA%\LMIT-2\wiki-only.toml"
```
