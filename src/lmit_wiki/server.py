from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from socketserver import ThreadingMixIn
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen
from wsgiref.simple_server import WSGIServer, make_server
import json

from lmit_wiki.builder import ingest_wiki, init_wiki, lint_wiki
from lmit_wiki.config import AppConfig, load_config, write_local_config
from lmit_wiki.auto import auto_sync_wiki
from lmit_wiki.query import answer_wiki_query
from lmit_wiki.runtime import (
    default_runtime_settings_payload,
    load_runtime_settings,
    merge_runtime_settings_payload,
    runtime_settings_public_payload,
    save_runtime_settings,
)
from lmit_wiki.search import search_result_payload, search_wiki


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def serve_wiki_ui(
    cfg: AppConfig,
    *,
    config_path: Path | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    host = host or cfg.wiki_runtime.serve_host
    port = port or cfg.wiki_runtime.serve_port
    app = WikiWebApp(cfg, config_path=config_path)
    with make_server(host, port, app, server_class=ThreadingWSGIServer) as server:
        print(f"Wiki UI: http://{host}:{port}")
        server.serve_forever()


class WikiWebApp:
    def __init__(self, cfg: AppConfig, *, config_path: Path | None = None) -> None:
        self.cfg = cfg
        self.config_path = config_path.resolve() if config_path is not None else None
        self._cfg_lock = RLock()

    def __call__(self, environ, start_response):
        method = environ["REQUEST_METHOD"].upper()
        path = environ.get("PATH_INFO", "/")
        try:
            if method == "GET" and path == "/":
                return self._html(start_response, INDEX_HTML)
            if method == "GET" and path == "/manual":
                return self._html(start_response, MANUAL_HTML)
            if method == "GET" and path == "/api/status":
                return self._json(start_response, self._status_payload())
            if method == "POST" and path == "/api/config":
                payload = self._read_json(environ)
                if self.config_path is None:
                    raise ValueError("This server was started without --config, so paths cannot be saved.")
                root_text = str(payload.get("root_dir", "")).strip()
                source_dirs = _payload_source_dirs(payload.get("source_dirs"))
                if not root_text:
                    raise ValueError("Knowledge base path is required.")
                if not source_dirs:
                    raise ValueError("At least one raw source path is required.")
                old_cfg = self._current_cfg()
                new_cfg = write_local_config(
                    self.config_path,
                    root_dir=Path(root_text),
                    source_dirs=source_dirs,
                    serve_host=old_cfg.wiki_runtime.serve_host,
                    serve_port=old_cfg.wiki_runtime.serve_port,
                    auto_sync_on_ingest=old_cfg.wiki_runtime.auto_sync_on_ingest,
                    search_limit=old_cfg.wiki_runtime.search_limit,
                    task_schedule=old_cfg.windows.task_schedule,
                )
                init_wiki(new_cfg)
                with self._cfg_lock:
                    self.cfg = load_config(self.config_path)
                return self._json(start_response, self._status_payload())
            if method == "POST" and path == "/api/ingest":
                result = ingest_wiki(self._current_cfg())
                return self._json(
                    start_response,
                    {
                        "source_count": result.source_count,
                        "copied_raw_count": result.copied_raw_count,
                        "source_note_count": result.source_note_count,
                        "index_path": str(result.index_path),
                        "log_path": str(result.log_path),
                    },
                )
            if method == "POST" and path == "/api/lint":
                warnings = lint_wiki(self._current_cfg())
                return self._json(
                    start_response,
                    {
                        "passed": not warnings,
                        "warnings": warnings,
                    },
                )
            if method == "GET" and path == "/api/search":
                query = parse_qs(environ.get("QUERY_STRING", "")).get("q", [""])[0]
                results = [
                    search_result_payload(item)
                    for item in search_wiki(self._current_cfg(), query, include_raw=True)
                ]
                return self._json(start_response, {"results": results})
            if method == "POST" and path == "/api/query":
                payload = self._read_json(environ)
                answer = answer_wiki_query(
                    self._current_cfg(),
                    str(payload.get("question", "")).strip(),
                    save=bool(payload.get("save", True)),
                )
                return self._json(
                    start_response,
                    {
                        "title": answer.title,
                        "answer_markdown": answer.answer_markdown,
                        "follow_up_questions": list(answer.follow_up_questions),
                        "saved_path": str(answer.saved_path) if answer.saved_path else None,
                        "search_results": [
                            search_result_payload(item) for item in answer.search_results
                        ],
                        "llm": (
                            {
                                "profile_id": answer.completion.profile_id,
                                "provider": answer.completion.provider,
                                "model": answer.completion.model,
                            }
                            if answer.completion is not None
                            else None
                        ),
                    },
                )
            if method == "POST" and path == "/api/sync":
                payload = self._read_json(environ)
                raw_limit = payload.get("limit")
                limit = int(raw_limit) if raw_limit not in (None, "") else None
                result = auto_sync_wiki(self._current_cfg(), limit=limit)
                return self._json(
                    start_response,
                    {
                        "processed_sources": result.processed_sources,
                        "created_pages": result.created_pages,
                        "updated_pages": result.updated_pages,
                        "pages": [
                            {
                                "name": page.name,
                                "kind": page.kind,
                                "path": str(page.path),
                                "action": page.action,
                            }
                            for page in result.pages
                        ],
                    },
                )
            if method == "GET" and path == "/api/settings":
                settings = load_runtime_settings(self._current_cfg())
                return self._json(
                    start_response,
                    runtime_settings_public_payload(settings),
                )
            if method == "GET" and path == "/api/models":
                query = parse_qs(environ.get("QUERY_STRING", ""))
                base_url = query.get("base_url", [""])[0].strip()
                if not base_url:
                    raise ValueError("Base URL is required before fetching models.")
                return self._json(
                    start_response,
                    {"models": _fetch_openai_compatible_models(base_url)},
                )
            if method == "POST" and path == "/api/settings/defaults":
                settings = save_runtime_settings(
                    self._current_cfg(),
                    default_runtime_settings_payload(),
                )
                return self._json(
                    start_response,
                    runtime_settings_public_payload(settings),
                )
            if method == "POST" and path == "/api/settings":
                payload = self._read_json(environ)
                cfg = self._current_cfg()
                existing = load_runtime_settings(cfg)
                merged = merge_runtime_settings_payload(existing, payload)
                settings = save_runtime_settings(cfg, merged)
                return self._json(
                    start_response,
                    runtime_settings_public_payload(settings),
                )
            return self._json(
                start_response,
                {"error": f"Unknown route: {method} {path}"},
                status=HTTPStatus.NOT_FOUND,
            )
        except Exception as exc:
            return self._json(
                start_response,
                {"error": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )

    def _read_json(self, environ) -> dict[str, object]:
        length = int(environ.get("CONTENT_LENGTH") or "0")
        body = environ["wsgi.input"].read(length) if length > 0 else b"{}"
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def _html(self, start_response, content: str):
        encoded = content.encode("utf-8")
        start_response(
            f"{HTTPStatus.OK.value} {HTTPStatus.OK.phrase}",
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(encoded))),
            ],
        )
        return [encoded]

    def _json(self, start_response, payload: dict[str, object], *, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        start_response(
            f"{status.value} {status.phrase}",
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    def _status_payload(self) -> dict[str, object]:
        cfg = self._current_cfg()
        source_dirs = [
            {
                "path": str(path),
                "exists": None,
            }
            for path in cfg.wiki_ingest.source_dirs
        ]
        return {
            "config_path": str(self.config_path) if self.config_path is not None else None,
            "root_dir": str(cfg.wiki.root_dir),
            "root_exists": None,
            "source_dirs": [item["path"] for item in source_dirs],
            "source_dir_status": source_dirs,
            "index_path": str(cfg.wiki.index_path),
            "index_exists": None,
            "log_path": str(cfg.wiki.log_path),
            "serve_host": cfg.wiki_runtime.serve_host,
            "serve_port": cfg.wiki_runtime.serve_port,
        }

    def _current_cfg(self) -> AppConfig:
        with self._cfg_lock:
            return self.cfg


def _payload_source_dirs(value: object) -> list[Path]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        items = str(value or "").replace(";", "\n").splitlines()
        items = [item.strip() for item in items]
    return [Path(item) for item in items if item]


def _fetch_openai_compatible_models(base_url: str, *, timeout_seconds: int = 8) -> list[str]:
    url = _models_url(base_url)
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset("utf-8")
            raw = response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"{url} returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        reason = str(exc.reason)
        raise RuntimeError(
            f"Could not connect to {url}. Start the LM Studio Local Server, "
            f"load a model, and confirm the port. Details: {reason}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{url} returned invalid JSON.") from exc
    models = _model_ids_from_payload(payload)
    if not models:
        raise RuntimeError(f"{url} did not return any model ids.")
    return models


def _models_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/models"):
        return normalized
    return normalized + "/models"


def _model_ids_from_payload(payload: object) -> list[str]:
    if isinstance(payload, dict):
        raw_models = payload.get("data")
        if raw_models is None:
            raw_models = payload.get("models")
    else:
        raw_models = payload
    if not isinstance(raw_models, list):
        return []

    model_ids: list[str] = []
    seen: set[str] = set()
    for item in raw_models:
        model_id = ""
        if isinstance(item, str):
            model_id = item
        elif isinstance(item, dict):
            for key in ("id", "model", "name", "path"):
                value = item.get(key)
                if value:
                    model_id = str(value)
                    break
        model_id = model_id.strip()
        if model_id and model_id not in seen:
            model_ids.append(model_id)
            seen.add(model_id)
    return model_ids


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LMIT-2 Wiki Console</title>
  <style>
    :root {
      --bg: #f4f6f5;
      --panel: #ffffff;
      --ink: #1e2430;
      --muted: #697281;
      --line: #d7dedb;
      --accent: #0f766e;
      --accent-soft: #e2f2ef;
      --warm: #b9472f;
      --shadow: 0 10px 30px rgba(30, 36, 48, 0.06);
      --radius: 8px;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Noto Sans", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }

    .shell {
      max-width: 1440px;
      margin: 0 auto;
      padding: 20px;
    }

    .hero {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 18px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
      margin-bottom: 16px;
    }

    .hero h1 {
      margin: 0 0 4px;
      font-size: 28px;
      line-height: 1.1;
      letter-spacing: 0;
    }

    .hero p {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      word-break: break-all;
    }

    .grid {
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 24px;
      align-items: start;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 20px;
    }

    h2 {
      margin: 0 0 14px;
      font-size: 20px;
      letter-spacing: -0.02em;
    }

    .stack { display: grid; gap: 14px; }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; }
    input:not([type="checkbox"]), textarea, select {
      width: 100%;
      padding: 12px 14px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      font: inherit;
    }

    select {
      min-height: 46px;
    }

    textarea { min-height: 120px; resize: vertical; }

    button {
      border: 0;
      border-radius: var(--radius);
      padding: 11px 18px;
      font: inherit;
      cursor: pointer;
      background: var(--accent);
      color: white;
    }

    button.secondary {
      background: var(--accent-soft);
      color: var(--accent);
    }

    button.warm {
      background: var(--warm);
    }

    .button-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: var(--radius);
      padding: 11px 18px;
      color: white;
      background: var(--accent);
      text-decoration: none;
      white-space: nowrap;
    }

    .button-link.secondary {
      color: var(--accent);
      background: var(--accent-soft);
    }

    .result, .profile {
      padding: 14px;
      border-radius: var(--radius);
      background: #fff;
      border: 1px solid var(--line);
    }

    .result h3, .profile h3 {
      margin: 0 0 6px;
      font-size: 16px;
    }

    .meta {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }

    .mono {
      font-family: Consolas, "Courier New", monospace;
      white-space: pre-wrap;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 14px;
      line-height: 1.6;
      overflow-x: auto;
    }

    .row {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .tiny {
      color: var(--muted);
      font-size: 12px;
    }

    .path-list {
      display: grid;
      gap: 8px;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      color: var(--muted);
      word-break: break-all;
    }

    .path-input {
      min-height: 74px;
    }

    .field {
      display: grid;
      gap: 6px;
      min-width: 0;
    }

    .field span {
      color: var(--muted);
      font-size: 12px;
    }

    .profile-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .profile-header h3 {
      margin: 0;
    }

    .profile-header button {
      padding: 8px 12px;
    }

    .empty-state {
      color: var(--muted);
      font-size: 13px;
      padding: 12px 0;
    }

    .status {
      min-height: 22px;
      color: var(--muted);
      font-size: 13px;
    }

    .manual-panel {
      display: none;
      margin-bottom: 16px;
    }

    .manual-panel.open {
      display: block;
    }

    .manual-panel ol, .manual-panel ul {
      margin: 0 0 0 20px;
      padding: 0;
    }

    .manual-panel li {
      margin: 6px 0;
    }

    .manual-panel code {
      background: #eef2f1;
      border-radius: 4px;
      padding: 2px 5px;
    }

    @media (max-width: 980px) {
      .grid { grid-template-columns: 1fr; }
      .row { grid-template-columns: 1fr; }
      .hero { align-items: stretch; flex-direction: column; }
      .button-link { width: 100%; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div>
        <h1>LMIT-2 Wiki Console</h1>
        <p>Config: <span id="configPath">loading...</span></p>
      </div>
      <button class="secondary" onclick="toggleManual()">Web UI Guide</button>
    </section>

    <section id="manualPanel" class="panel manual-panel">
      <h2>Web UI Guide</h2>
      <div class="row">
        <div>
          <h3>First Run</h3>
          <ol>
            <li>If no config exists yet, the launcher asks for the knowledge base and raw Markdown folders.</li>
            <li>Inside the Web UI, you can still edit <code>Knowledge Base Path</code> and <code>Raw Source Paths</code>.</li>
            <li>Click <code>Save Paths</code>, then run <code>Ingest</code>.</li>
            <li>For LM Studio, start its Local Server, then use <code>Fetch Models</code> to fill the model id.</li>
          </ol>
        </div>
        <div>
          <h3>Troubleshooting</h3>
          <ul>
            <li><code>Save Paths</code> does not run Ingest or use any LLM.</li>
            <li>If a button times out, check the message shown under that button.</li>
            <li>Launcher logs are under <code>%APPDATA%\\LMIT-2\\logs</code>.</li>
          </ul>
        </div>
      </div>
    </section>

    <div class="grid">
      <section class="panel stack">
        <div>
          <h2>Search</h2>
          <div class="toolbar">
            <input id="searchInput" placeholder="Search the wiki, source notes, and raw markdown">
            <button onclick="runSearch()">Search</button>
          </div>
        </div>
        <div id="searchStatus" class="status"></div>
        <div id="searchResults" class="stack"></div>

        <div>
          <h2>Ask The Wiki</h2>
          <div class="stack">
            <textarea id="questionInput" placeholder="Ask a synthesis question. The answer will be grounded in the current wiki and can be filed back into wiki/queries."></textarea>
            <div class="toolbar">
              <button class="warm" onclick="runQuery(true)">Ask And Save</button>
              <button class="secondary" onclick="runQuery(false)">Ask Only</button>
            </div>
          </div>
        </div>
        <div id="queryStatus" class="status"></div>
        <div id="queryOutput" class="stack"></div>

        <div>
          <h2>Knowledge Base</h2>
          <div class="row">
            <label class="field">
              <span>Knowledge Base Path</span>
              <textarea id="kbRootInput" class="path-input" placeholder="C:\\Users\\You\\Documents\\LMIT-2\\knowledge_base"></textarea>
            </label>
            <label class="field">
              <span>Raw Source Paths</span>
              <textarea id="sourceDirsInput" class="path-input" placeholder="C:\\Users\\You\\Documents\\LMIT\\output\\raw"></textarea>
            </label>
          </div>
          <div id="kbStatusPanel" class="path-list"></div>
        </div>
        <div class="toolbar">
          <button onclick="savePaths()">Save Paths</button>
          <button onclick="runIngest()">Ingest</button>
          <button class="secondary" onclick="runLint()">Lint</button>
        </div>
        <div id="pathStatus" class="status"></div>
        <div id="ingestStatus" class="status"></div>
        <div id="ingestOutput" class="stack"></div>
        <div id="lintStatus" class="status"></div>
        <div id="lintOutput" class="stack"></div>

        <div>
          <h2>Auto Sync</h2>
          <div class="toolbar">
            <input id="syncLimit" placeholder="Optional source limit">
            <button onclick="runSync()">Sync Now</button>
          </div>
        </div>
        <div id="syncStatus" class="status"></div>
        <div id="syncOutput" class="stack"></div>
      </section>

      <section class="panel stack">

        <div>
          <h2>LLM Settings</h2>
        </div>
        <div class="row">
          <label class="field">
            <span>Active Profile</span>
            <input id="activeProfile" placeholder="ollama-local">
          </label>
          <label class="field">
            <span>Fallback Order</span>
            <input id="fallbackOrder" placeholder="ollama-local, lm-studio-local, openai-compatible, gemini">
          </label>
        </div>
        <div class="toolbar">
          <button class="secondary" onclick="addProfile('ollama')">Add Ollama</button>
          <button class="secondary" onclick="addProfile('lmstudio')">Add LM Studio</button>
          <button class="secondary" onclick="addProfile('openai')">Add OpenAI</button>
          <button class="secondary" onclick="addProfile('gemini')">Add Gemini</button>
          <button class="secondary" onclick="restoreDefaults()">Restore Defaults</button>
          <button onclick="saveSettings()">Save Settings</button>
        </div>
        <div id="profiles" class="stack"></div>
        <div id="settingsStatus" class="status"></div>
      </section>
    </div>
  </div>

  <template id="profileTemplate">
    <div class="profile stack">
      <div class="profile-header">
        <h3 data-field="heading">Profile</h3>
        <button class="secondary" onclick="removeProfile(this)">Remove</button>
      </div>
      <div class="row">
        <label class="field">
          <span>Profile ID</span>
          <input data-field="id" placeholder="ollama-local">
        </label>
        <label class="field">
          <span>Label</span>
          <input data-field="label" placeholder="Local Ollama">
        </label>
      </div>
      <div class="row">
        <label class="field">
          <span>Provider</span>
          <select data-field="provider">
            <option value="ollama">ollama</option>
            <option value="openai_compatible">openai_compatible</option>
            <option value="gemini">gemini</option>
          </select>
        </label>
        <label class="field">
          <span>Model</span>
          <input data-field="model" placeholder="llama3.1">
        </label>
      </div>
      <label class="field">
        <span>Base URL</span>
        <input data-field="base_url" placeholder="http://localhost:11434/api">
      </label>
      <div class="toolbar">
        <button class="secondary" onclick="fetchModels(this)">Fetch Models</button>
      </div>
      <div class="tiny" data-field="model_hint">For LM Studio, start the Local Server first, then fetch models and use the returned id.</div>
      <label class="field">
        <span>API Key Environment Variable</span>
        <input data-field="api_key_env" placeholder="OPENAI_API_KEY">
      </label>
      <div class="tiny" data-field="api_key_hint"></div>
      <div class="row">
        <label class="field">
          <span>Temperature</span>
          <input data-field="temperature" placeholder="0.2">
        </label>
        <label class="field">
          <span>Timeout Seconds</span>
          <input data-field="timeout_seconds" placeholder="120">
        </label>
      </div>
      <label class="tiny"><input type="checkbox" data-field="enabled"> Enabled</label>
    </div>
  </template>

  <script>
    const PROFILE_PRESETS = {
      ollama: {
        id: "ollama-local",
        provider: "ollama",
        label: "Local Ollama",
        base_url: "http://localhost:11434/api",
        model: "llama3.1",
        api_key_env: "",
        enabled: false,
        temperature: 0.2,
        timeout_seconds: 120
      },
      openai: {
        id: "openai-compatible",
        provider: "openai_compatible",
        label: "OpenAI Compatible",
        base_url: "https://api.openai.com/v1",
        model: "gpt-4.1-mini",
        api_key_env: "OPENAI_API_KEY",
        enabled: false,
        temperature: 0.2,
        timeout_seconds: 120
      },
      lmstudio: {
        id: "lm-studio-local",
        provider: "openai_compatible",
        label: "LM Studio Local",
        base_url: "http://localhost:1234/v1",
        model: "local-model",
        api_key_env: "",
        enabled: false,
        temperature: 0.2,
        timeout_seconds: 120
      },
      gemini: {
        id: "gemini",
        provider: "gemini",
        label: "Gemini",
        base_url: "https://generativelanguage.googleapis.com/v1beta",
        model: "gemini-2.5-flash",
        api_key_env: "GEMINI_API_KEY",
        enabled: false,
        temperature: 0.2,
        timeout_seconds: 120
      }
    };

    async function boot() {
      await loadStatus();
      await loadSettings();
    }

    function status(id, text) {
      document.getElementById(id).textContent = text || "";
    }

    function toggleManual() {
      document.getElementById("manualPanel").classList.toggle("open");
    }

    function requestJson(url, options = {}, timeoutSeconds = 20) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutSeconds * 1000);
      return fetch(url, { ...options, signal: controller.signal })
        .then(async (response) => {
          const data = await response.json().catch(() => ({}));
          if (!response.ok || data.error) {
            throw new Error(data.error || `${response.status} ${response.statusText}`);
          }
          return data;
        })
        .catch((error) => {
          if (error.name === "AbortError") {
            throw new Error(`Request timed out after ${timeoutSeconds} seconds.`);
          }
          throw error;
        })
        .finally(() => clearTimeout(timer));
    }

    async function loadStatus() {
      try {
        const data = await requestJson("/api/status", {}, 10);
        document.getElementById("configPath").textContent = data.config_path || "not saved";
        document.getElementById("kbRootInput").value = data.root_dir || "";
        document.getElementById("sourceDirsInput").value = (data.source_dirs || []).join("\\n");
        const root = document.getElementById("kbStatusPanel");
        root.innerHTML = "";
        const rows = [
          ["KB", data.root_dir || ""],
          ["Raw", (data.source_dir_status || []).map((item) => item.path).join("; ")],
          ["Index", data.index_path],
          ["Log", data.log_path]
        ];
        for (const [label, value] of rows) {
          const div = document.createElement("div");
          div.textContent = `${label}: ${value || ""}`;
          root.appendChild(div);
        }
      } catch (error) {
        document.getElementById("configPath").textContent = "status unavailable";
        status("pathStatus", `Status load failed: ${error.message}`);
      }
    }

    async function savePaths() {
      try {
        status("pathStatus", "Saving paths...");
        const sourceDirs = document.getElementById("sourceDirsInput").value
          .split(/\\r?\\n|;/)
          .map((item) => item.trim())
          .filter(Boolean);
        const payload = {
          root_dir: document.getElementById("kbRootInput").value.trim(),
          source_dirs: sourceDirs
        };
        await requestJson("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }, 20);
        await loadStatus();
        await loadSettings();
        status("pathStatus", "Paths saved. This did not run Ingest or call an LLM.");
      } catch (error) {
        status("pathStatus", `Save failed: ${error.message}`);
      }
    }

    function renderProfiles(profiles) {
      const root = document.getElementById("profiles");
      root.innerHTML = "";
      if (!profiles.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "No LLM profiles are configured.";
        root.appendChild(empty);
        return;
      }
      for (const profile of profiles) {
        const node = document.getElementById("profileTemplate").content.firstElementChild.cloneNode(true);
        node.querySelector('[data-field="heading"]').textContent = profile.label || profile.id || "Profile";
        node.querySelector('[data-field="id"]').value = profile.id || "";
        node.querySelector('[data-field="label"]').value = profile.label || "";
        node.querySelector('[data-field="provider"]').value = profile.provider || "ollama";
        node.querySelector('[data-field="model"]').value = profile.model || "";
        node.querySelector('[data-field="base_url"]').value = profile.base_url || "";
        const apiKeyInput = node.querySelector('[data-field="api_key_env"]');
        apiKeyInput.value = profile.api_key_env || "";
        apiKeyInput.placeholder = apiKeyPlaceholder(profile.provider || "ollama");
        node.querySelector('[data-field="temperature"]').value = profile.temperature ?? 0.2;
        node.querySelector('[data-field="timeout_seconds"]').value = profile.timeout_seconds ?? 90;
        node.querySelector('[data-field="enabled"]').checked = Boolean(profile.enabled);
        node.querySelector('[data-field="api_key_hint"]').textContent = profile.api_key_present
          ? `Stored key: ${profile.api_key_masked}`
          : "No stored key";
        root.appendChild(node);
      }
    }

    function apiKeyPlaceholder(provider) {
      if (provider === "openai_compatible") {
        return "OPENAI_API_KEY";
      }
      if (provider === "gemini") {
        return "GEMINI_API_KEY";
      }
      return "";
    }

    async function fetchModels(button) {
      const node = button.closest(".profile");
      const provider = node.querySelector('[data-field="provider"]').value;
      const baseUrl = node.querySelector('[data-field="base_url"]').value.trim();
      const modelInput = node.querySelector('[data-field="model"]');
      const hint = node.querySelector('[data-field="model_hint"]');
      if (provider !== "openai_compatible") {
        hint.textContent = "Fetch Models currently supports LM Studio and other OpenAI-compatible endpoints.";
        return;
      }
      if (!baseUrl) {
        hint.textContent = "Enter a Base URL before fetching models.";
        return;
      }
      try {
        hint.textContent = "Fetching models...";
        const data = await requestJson(`/api/models?base_url=${encodeURIComponent(baseUrl)}`, {}, 8);
        const models = data.models || [];
        if (!models.length) {
          hint.textContent = "No models returned by this endpoint.";
          return;
        }
        modelInput.value = models[0];
        hint.textContent = `Models: ${models.join(", ")}`;
      } catch (error) {
        hint.textContent = `Model fetch failed: ${error.message}`;
      }
    }

    function addProfile(kind) {
      const preset = PROFILE_PRESETS[kind] || PROFILE_PRESETS.ollama;
      const profile = { ...preset };
      profile.id = uniqueProfileId(profile.id);
      if (profile.id !== preset.id) {
        profile.label = `${preset.label} ${profile.id.replace(`${preset.id}-`, "")}`;
      }
      renderProfiles([...collectProfiles({ includeIncomplete: true }), profile]);
      status("settingsStatus", `Added ${profile.label}.`);
    }

    function removeProfile(button) {
      button.closest(".profile").remove();
      status("settingsStatus", "Profile removed. Save settings to keep this change.");
    }

    function uniqueProfileId(base) {
      const ids = new Set(collectProfiles({ includeIncomplete: true }).map((profile) => profile.id).filter(Boolean));
      if (!ids.has(base)) {
        return base;
      }
      let index = 2;
      while (ids.has(`${base}-${index}`)) {
        index += 1;
      }
      return `${base}-${index}`;
    }

    function collectProfiles(options = {}) {
      return [...document.querySelectorAll(".profile")].map((node) => ({
        id: node.querySelector('[data-field="id"]').value.trim(),
        label: node.querySelector('[data-field="label"]').value.trim(),
        provider: node.querySelector('[data-field="provider"]').value,
        model: node.querySelector('[data-field="model"]').value.trim(),
        base_url: node.querySelector('[data-field="base_url"]').value.trim(),
        api_key_env: node.querySelector('[data-field="api_key_env"]').value.trim(),
        enabled: node.querySelector('[data-field="enabled"]').checked,
        temperature: Number(node.querySelector('[data-field="temperature"]').value || "0.2"),
        timeout_seconds: Number(node.querySelector('[data-field="timeout_seconds"]').value || "90")
      })).filter((profile) => options.includeIncomplete || profile.id);
    }

    async function loadSettings() {
      try {
        status("settingsStatus", "Loading settings...");
        const data = await requestJson("/api/settings", {}, 10);
        document.getElementById("activeProfile").value = data.active_profile || "";
        document.getElementById("fallbackOrder").value = (data.fallback_order || []).join(", ");
        renderProfiles(data.profiles || []);
        status("settingsStatus", "Settings loaded.");
      } catch (error) {
        renderProfiles([]);
        status("settingsStatus", `Settings load failed: ${error.message}`);
      }
    }

    async function restoreDefaults() {
      try {
        status("settingsStatus", "Restoring default profiles...");
        const data = await requestJson("/api/settings/defaults", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}"
        }, 20);
        document.getElementById("activeProfile").value = data.active_profile || "";
        document.getElementById("fallbackOrder").value = (data.fallback_order || []).join(", ");
        renderProfiles(data.profiles || []);
        status("settingsStatus", "Default profiles restored.");
      } catch (error) {
        status("settingsStatus", `Restore failed: ${error.message}`);
      }
    }

    async function saveSettings() {
      try {
        status("settingsStatus", "Saving settings...");
        const profiles = collectProfiles({ includeIncomplete: true });
        const missingId = profiles.find((profile) => !profile.id);
        if (missingId) {
          status("settingsStatus", "Every profile needs a Profile ID before saving.");
          return;
        }
        const seen = new Set();
        for (const profile of profiles) {
          if (seen.has(profile.id)) {
            status("settingsStatus", `Duplicate profile id: ${profile.id}`);
            return;
          }
          seen.add(profile.id);
        }
        const payload = {
          active_profile: document.getElementById("activeProfile").value.trim() || null,
          fallback_order: document.getElementById("fallbackOrder").value.split(",").map((item) => item.trim()).filter(Boolean),
          profiles
        };
        const data = await requestJson("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }, 20);
        renderProfiles(data.profiles || []);
        status("settingsStatus", "Settings saved. This did not call an LLM.");
      } catch (error) {
        status("settingsStatus", `Save failed: ${error.message}`);
      }
    }

    async function runSearch() {
      const query = document.getElementById("searchInput").value.trim();
      if (!query) {
        status("searchStatus", "Enter a search query.");
        return;
      }
      status("searchStatus", "Searching...");
      const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
      const data = await response.json();
      const root = document.getElementById("searchResults");
      root.innerHTML = "";
      for (const item of data.results || []) {
        const div = document.createElement("div");
        div.className = "result";
        div.innerHTML = `<h3>${escapeHtml(item.title)}</h3><div class="meta">${escapeHtml(item.kind)} / ${escapeHtml(item.rel_path)} / score ${item.score}</div><div>${escapeHtml(item.snippet)}</div>`;
        root.appendChild(div);
      }
      status("searchStatus", `${(data.results || []).length} result(s).`);
    }

    async function runQuery(save) {
      const question = document.getElementById("questionInput").value.trim();
      if (!question) {
        status("queryStatus", "Enter a question.");
        return;
      }
      status("queryStatus", save ? "Asking and saving..." : "Asking...");
      const response = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, save })
      });
      const data = await response.json();
      const root = document.getElementById("queryOutput");
      root.innerHTML = "";
      if (data.error) {
        status("queryStatus", data.error);
        return;
      }
      const meta = document.createElement("div");
      meta.className = "result";
      meta.innerHTML = `<h3>${escapeHtml(data.title || "Answer")}</h3><div class="meta">${data.saved_path ? escapeHtml(data.saved_path) : "not saved"}${data.llm ? ` / ${escapeHtml(data.llm.profile_id)} / ${escapeHtml(data.llm.model)}` : ""}</div>`;
      root.appendChild(meta);
      const answer = document.createElement("div");
      answer.className = "mono";
      answer.textContent = data.answer_markdown || "";
      root.appendChild(answer);
      if ((data.follow_up_questions || []).length) {
        const follow = document.createElement("div");
        follow.className = "result";
        follow.innerHTML = `<h3>Follow-up Questions</h3>${data.follow_up_questions.map((item) => `<div>- ${escapeHtml(item)}</div>`).join("")}`;
        root.appendChild(follow);
      }
      status("queryStatus", "Answer ready.");
    }

    async function runIngest() {
      status("ingestStatus", "Ingesting...");
      const response = await fetch("/api/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      const data = await response.json();
      const root = document.getElementById("ingestOutput");
      root.innerHTML = "";
      if (data.error) {
        status("ingestStatus", data.error);
        return;
      }
      const summary = document.createElement("div");
      summary.className = "result";
      summary.innerHTML = `<h3>Ingest Summary</h3><div class="meta">sources ${data.source_count} / raw copies ${data.copied_raw_count} / source notes ${data.source_note_count}</div><div class="tiny">${escapeHtml(data.index_path || "")}</div>`;
      root.appendChild(summary);
      await loadStatus();
      status("ingestStatus", "Ingest complete.");
    }

    async function runLint() {
      status("lintStatus", "Linting...");
      const response = await fetch("/api/lint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      const data = await response.json();
      const root = document.getElementById("lintOutput");
      root.innerHTML = "";
      if (data.error) {
        status("lintStatus", data.error);
        return;
      }
      const summary = document.createElement("div");
      summary.className = "result";
      if (data.passed) {
        summary.innerHTML = '<h3>Lint Passed</h3><div class="meta">No warnings found.</div>';
      } else {
        summary.innerHTML = `<h3>Lint Warnings</h3>${(data.warnings || []).map((item) => `<div>${escapeHtml(item)}</div>`).join("")}`;
      }
      root.appendChild(summary);
      status("lintStatus", data.passed ? "Lint passed." : `${(data.warnings || []).length} warning(s).`);
    }

    async function runSync() {
      status("syncStatus", "Syncing...");
      const raw = document.getElementById("syncLimit").value.trim();
      const payload = raw ? { limit: Number(raw) } : {};
      const response = await fetch("/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      const root = document.getElementById("syncOutput");
      root.innerHTML = "";
      if (data.error) {
        status("syncStatus", data.error);
        return;
      }
      const summary = document.createElement("div");
      summary.className = "result";
      summary.innerHTML = `<h3>Sync Summary</h3><div class="meta">processed ${data.processed_sources} source(s) / created ${data.created_pages} / updated ${data.updated_pages}</div>`;
      root.appendChild(summary);
      for (const page of data.pages || []) {
        const item = document.createElement("div");
        item.className = "result";
        item.innerHTML = `<h3>${escapeHtml(page.name)}</h3><div class="meta">${escapeHtml(page.kind)} / ${escapeHtml(page.action)} / ${escapeHtml(page.path)}</div>`;
        root.appendChild(item);
      }
      status("syncStatus", "Sync complete.");
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    boot();
  </script>
</body>
</html>
"""


MANUAL_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LMIT-2 Web UI Guide</title>
  <style>
    body {
      margin: 0;
      font-family: "Segoe UI", "Noto Sans TC", "Noto Sans", sans-serif;
      color: #1e2430;
      background: #f4f6f5;
      line-height: 1.65;
    }
    main {
      max-width: 920px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }
    section {
      background: #fff;
      border: 1px solid #d7dedb;
      border-radius: 8px;
      padding: 20px;
      margin-top: 16px;
    }
    h1, h2 { line-height: 1.2; }
    code {
      background: #eef2f1;
      padding: 2px 5px;
      border-radius: 4px;
    }
    li { margin: 6px 0; }
  </style>
</head>
<body>
  <main>
    <h1>LMIT-2 Web UI 操作教學</h1>
    <section>
      <h2>第一次啟動</h2>
      <ol>
        <li>開啟 <code>LMIT-2 Wiki Console</code>。</li>
        <li>第一次啟動若還沒有 <code>%APPDATA%\\LMIT-2\\wiki-only.toml</code>，啟動器會先請你選擇 knowledge base 資料夾與 LMIT-1 raw Markdown 來源資料夾。</li>
        <li>進入 Web UI 後，仍可在 <code>Knowledge Base Path</code> 與 <code>Raw Source Paths</code> 修改路徑。</li>
        <li>按 <code>Save Paths</code>。LMIT-2 會寫回目前使用者的 <code>wiki-only.toml</code>，並初始化 knowledge base 目錄；它不會執行 Ingest，也不會呼叫任何 LLM。</li>
      </ol>
    </section>
    <section>
      <h2>日常流程</h2>
      <ol>
        <li><code>Ingest</code>：讀取 raw Markdown，複製成安全短檔名，產生 source notes、manifest 與 index。</li>
        <li><code>Lint</code>：檢查 knowledge base 必要目錄與索引是否存在。</li>
        <li><code>Search</code>：查詢已 ingest 的 source notes、raw copy 與 wiki 頁面。</li>
        <li><code>Ask The Wiki</code>：根據目前 wiki 回答問題；<code>Ask And Save</code> 會把結果存入 <code>wiki/queries</code>。</li>
      </ol>
    </section>
    <section>
      <h2>LLM Settings</h2>
      <ul>
        <li><code>Add Ollama</code> 建立本機 Ollama profile；通常不需要 API key env。</li>
        <li><code>Add LM Studio</code> 建立本機 OpenAI-compatible profile，預設使用 <code>http://localhost:1234/v1</code>，通常不需要 API key env。</li>
        <li><code>Add OpenAI</code> 或 <code>Add Gemini</code> 只保存環境變數名稱，不保存密鑰值。</li>
        <li>API key 可放在 Windows 使用者/系統環境變數，也可放在安裝資料夾的 <code>.env</code> 檔，例如 <code>OPENAI_API_KEY=...</code>。</li>
        <li>LM Studio 的 <code>Model</code> 要填 API 回傳的 model id。先在 LM Studio 啟動 Local Server 並載入模型，再按 <code>Fetch Models</code>。</li>
        <li>若 <code>Fetch Models</code> 顯示無法連線，通常是 LM Studio server 沒啟動、port 不是 1234，或被防火牆/權限擋住。</li>
        <li><code>Active Profile</code> 是優先使用的 profile；<code>Fallback Order</code> 是失敗時的備援順序。</li>
        <li>修改 profile 後必須按 <code>Save Settings</code>。</li>
        <li><code>Restore Defaults</code> 會重建預設 profile 清單。</li>
      </ul>
    </section>
    <section>
      <h2>LM Studio 原生 REST API</h2>
      <ul>
        <li>LM Studio 原生 REST API <code>/api/v1/*</code> 適合模型管理、載入/卸載、stateful chat 與 MCP。</li>
        <li>LMIT-2 目前只需要一般 chat completion 與 citation workflow，所以先使用 OpenAI-compatible <code>/v1/chat/completions</code>。</li>
        <li>之後若要在 Web UI 內管理 LM Studio 模型，再接原生 REST API 會更合適。</li>
      </ul>
    </section>
    <section>
      <h2>Auto Sync 與排程</h2>
      <ul>
        <li><code>Sync Now</code> 會使用已啟用的 LLM profile 更新 topic/entity 頁面。</li>
        <li>第一次安裝不建議立即建立排程，因為路徑、ingest 結果與 LLM profile 通常還沒確認。</li>
        <li>需要自動化時，再重新安裝並勾選排程，或用工作排程器手動建立。</li>
      </ul>
    </section>
    <section>
      <h2>疑難排解</h2>
      <ul>
        <li>Web UI 打不開時，先確認 <code>127.0.0.1:8765</code> 沒被其他程式佔用。</li>
        <li>啟動器錯誤記錄位於 <code>%APPDATA%\\LMIT-2\\logs</code>。</li>
        <li>Ingest 找不到資料時，檢查 <code>Raw Source Paths</code> 是否指向 LMIT-1 的 <code>output/raw</code>。</li>
        <li><code>Save Paths</code> 只儲存路徑與初始化 KB，不會執行 Ingest，也不會呼叫任何 LLM。</li>
        <li>如果按鈕顯示 timeout，通常是 server 未回應、路徑位於慢速/離線磁碟，或另一個長時間操作仍在執行。</li>
      </ul>
    </section>
  </main>
</body>
</html>
"""

