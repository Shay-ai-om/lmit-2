# LMIT-2 Web UI Guide

這份文件對應目前的 Windows 本地安裝版 Web UI。

## 第一次啟動

1. 開啟 `LMIT-2 Wiki Console`。
2. 如果目前使用者還沒有 `%APPDATA%\LMIT-2\wiki-only.toml`，啟動器會先要求選擇 knowledge base 資料夾與 LMIT-1 raw Markdown 來源資料夾。
3. 進入 Web UI 後，仍可在 `Knowledge Base Path` 與 `Raw Source Paths` 修改路徑。
4. 按 `Save Paths` 只會儲存路徑並初始化 knowledge base；它不會執行 Ingest，也不會呼叫任何 LLM。
5. 路徑確認後，再執行 `Ingest`。

## 日常流程

1. `Ingest`：讀取 raw Markdown，複製成安全短檔名，並產生 source notes、manifest 與 index。
2. `Lint`：檢查 knowledge base 必要目錄與索引是否存在。
3. `Search`：搜尋 source notes、raw copy 與 wiki 頁面。
4. 搜尋結果可用 `Open Result` 打開目前文件；若結果對應 source note，還會出現 `Open Raw` 直接打開 raw markdown。
5. `Ask The Wiki`：根據目前 wiki 回答問題；`Ask And Save` 會把結果存入 `wiki/queries`。
6. `Ask The Wiki` 現在會串流顯示答案，只要模型開始輸出 token，畫面就會持續更新。
7. `Sync Now`：背景執行 LLM auto sync，更新 topic/entity 頁面，並在 Web UI 顯示進度。
8. `Stop Sync`：要求目前背景 sync 在當前 source 完成後停止，不會回滾已經完成的 page update。
9. `Resume Sync`：從下一筆未完成 source 繼續，不會重跑已成功完成的 source。

## LLM Settings

- `Add Ollama`：建立本機 Ollama profile。
- `Add LM Studio REST`：建立 LM Studio 原生 REST profile，預設 `Base URL` 為 `http://localhost:1234/api/v1`。
- `Add LiteLLM`：建立本機 LiteLLM proxy profile，預設 `Base URL` 為 `http://localhost:4000`。
- `Add OpenAI` / `Add Gemini`：建立雲端 API profile。
- `Fetch Models` 目前支援 OpenAI-compatible、LiteLLM 與 LM Studio REST profile。
- `Model` 欄位要填 API 真正回傳的 model id 或 model key。
- API key 只保存「環境變數名稱」，不保存密鑰值本身。
- 本機 LLM profile 預設 `Timeout Seconds` 為 `300`；慢模型或長上下文可再往上調。
- `Ask The Wiki` 串流最適合本機 Ollama、LiteLLM 與 LM Studio REST；若 provider 不支援串流，結果會在完成時一次顯示。
- 修改 profile 後必須按 `Save Settings`。
- `Restore Defaults` 會重建預設 profile 清單。

## LM Studio

LM Studio 在 LMIT-2 內預設只保留原生 REST 模式：

- Native REST：`http://localhost:1234/api/v1`

建議流程：

1. 在 LM Studio 內啟動 Local Server。
2. 在 LMIT-2 Web UI 內選擇對應 profile。
3. 按 `Fetch Models`。
4. 把回傳的 model id 寫入 `Model` 欄位。

## LiteLLM

LiteLLM Proxy Server 官方 quick start 預設會跑在：

- `http://localhost:4000`

如果你的 LiteLLM proxy 啟用了 master key，可在 profile 內填 `LITELLM_API_KEY` 這類環境變數名稱。
`Fetch Models` 會透過 LiteLLM 的 OpenAI-style `/models` 端點讀取可用模型。

## API Keys 與 `.env`

可用的方式：

- Windows 使用者環境變數
- Windows 系統環境變數
- 安裝資料夾下的 `.env` 檔

例如：

```text
OPENAI_API_KEY=...
GEMINI_API_KEY=...
LM_STUDIO_API_TOKEN=...
```

Web UI 不會顯示 stored key 值，只顯示 `API Key Environment Variable` 欄位。

## Auto Sync 與排程

- `Sync Now` 會在背景工作執行，不會把瀏覽器卡在單一長請求上。
- `Stop Sync` 會在目前 source 完成後乾淨停下，不會強制中斷正在跑的那次 LLM 呼叫。
- `Resume Sync` 會依照已儲存的 `processed_sources` state，從下一筆未完成 source 接著跑。
- Web UI 會顯示：
  - 目前任務狀態
  - 已處理 source 數量
  - 目前處理中的 source
  - created / updated page 數量
  - 完成後的 page 清單
- 如果有一個 sync job 已在執行，再按一次 `Sync Now` 不會重複開第二個任務，而是回到既有進度畫面。
- 如果模型先回傳完整 JSON，後面又多補說明文字，最新版會盡量只擷取第一個完整 JSON 文件，降低 `Extra data` 類型失敗。
- 第一次安裝通常不建議立刻建立 Windows scheduled ingest / sync / lint tasks，因為路徑、ingest 結果與 LLM profile 往往還沒驗證完。

## 疑難排解

- `Save Paths` 卡住：先看 Web UI 下方狀態列與 `%APPDATA%\LMIT-2\logs`。
- 如果 `%APPDATA%\LMIT-2\wiki-only.toml` 已存在但壞掉，啟動器會先備份成 `wiki-only.toml.broken-*.bak`，再要求重新選路徑。
- `Ask The Wiki` 沒有任何串流輸出：先確認模型已載入、provider 支援串流，或提高目前 profile 的 `Timeout Seconds`。
- `Sync Now` 很久：這不一定是壞掉，因為它現在會在背景跑。先看進度訊息是否持續更新。
- `Sync Now` 背景任務失敗：先看 Web UI 顯示的錯誤內容，再檢查目前 LLM profile、模型名稱與 timeout。
- `Sync Now` 若中途失敗，可用 `Resume Sync` 從下一筆未完成 source 繼續，不必整批重來。
- Web UI 打不開：先確認 `127.0.0.1:8765` 沒被其他程式占用。
- 若需要從命令列關閉目前的 Web UI server，可執行 `lmit-wiki stop --config "%APPDATA%\LMIT-2\wiki-only.toml"`。
- Ingest 找不到資料：確認 `Raw Source Paths` 是否指向 LMIT-1 的 `output/raw`。
