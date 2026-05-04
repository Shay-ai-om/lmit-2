from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server
import json

from lmit_wiki.builder import ingest_wiki, lint_wiki
from lmit_wiki.config import AppConfig
from lmit_wiki.auto import auto_sync_wiki
from lmit_wiki.query import answer_wiki_query
from lmit_wiki.runtime import (
    load_runtime_settings,
    merge_runtime_settings_payload,
    runtime_settings_public_payload,
    save_runtime_settings,
)
from lmit_wiki.search import search_result_payload, search_wiki


def serve_wiki_ui(
    cfg: AppConfig,
    *,
    host: str | None = None,
    port: int | None = None,
) -> None:
    host = host or cfg.wiki_runtime.serve_host
    port = port or cfg.wiki_runtime.serve_port
    app = WikiWebApp(cfg)
    with make_server(host, port, app) as server:
        print(f"Wiki UI: http://{host}:{port}")
        server.serve_forever()


class WikiWebApp:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def __call__(self, environ, start_response):
        method = environ["REQUEST_METHOD"].upper()
        path = environ.get("PATH_INFO", "/")
        try:
            if method == "GET" and path == "/":
                return self._html(start_response, INDEX_HTML)
            if method == "GET" and path == "/api/status":
                return self._json(
                    start_response,
                    {
                        "root_dir": str(self.cfg.wiki.root_dir),
                        "source_dirs": [str(path) for path in self.cfg.wiki_ingest.source_dirs],
                        "index_path": str(self.cfg.wiki.index_path),
                        "log_path": str(self.cfg.wiki.log_path),
                        "serve_host": self.cfg.wiki_runtime.serve_host,
                        "serve_port": self.cfg.wiki_runtime.serve_port,
                    },
                )
            if method == "POST" and path == "/api/ingest":
                result = ingest_wiki(self.cfg)
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
                warnings = lint_wiki(self.cfg)
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
                    for item in search_wiki(self.cfg, query, include_raw=True)
                ]
                return self._json(start_response, {"results": results})
            if method == "POST" and path == "/api/query":
                payload = self._read_json(environ)
                answer = answer_wiki_query(
                    self.cfg,
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
                result = auto_sync_wiki(self.cfg, limit=limit)
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
                settings = load_runtime_settings(self.cfg)
                return self._json(
                    start_response,
                    runtime_settings_public_payload(settings),
                )
            if method == "POST" and path == "/api/settings":
                payload = self._read_json(environ)
                existing = load_runtime_settings(self.cfg)
                merged = merge_runtime_settings_payload(existing, payload)
                settings = save_runtime_settings(self.cfg, merged)
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


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LMIT Wiki Console</title>
  <style>
    :root {
      --bg: #f6f1e8;
      --panel: #fffaf2;
      --ink: #1e2430;
      --muted: #697281;
      --line: #d8cbb8;
      --accent: #136f63;
      --accent-soft: #d9efe6;
      --warm: #c05d33;
      --shadow: 0 18px 50px rgba(30, 36, 48, 0.08);
      --radius: 18px;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Noto Sans", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(19, 111, 99, 0.14), transparent 28%),
        radial-gradient(circle at bottom right, rgba(192, 93, 51, 0.12), transparent 22%),
        var(--bg);
    }

    .shell {
      max-width: 1400px;
      margin: 0 auto;
      padding: 24px;
    }

    .hero {
      padding: 24px;
      border: 1px solid var(--line);
      border-radius: 28px;
      background: linear-gradient(135deg, rgba(255,250,242,0.96), rgba(236,248,243,0.96));
      box-shadow: var(--shadow);
      margin-bottom: 24px;
    }

    .hero h1 {
      margin: 0 0 8px;
      font-size: clamp(32px, 6vw, 56px);
      line-height: 0.95;
      letter-spacing: -0.03em;
    }

    .hero p {
      margin: 0;
      max-width: 760px;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.6;
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
    .toolbar input, textarea, select {
      width: 100%;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      font: inherit;
    }

    textarea { min-height: 120px; resize: vertical; }

    button {
      border: 0;
      border-radius: 999px;
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

    .result, .profile {
      padding: 14px;
      border-radius: 14px;
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
      border-radius: 14px;
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

    .status {
      min-height: 22px;
      color: var(--muted);
      font-size: 13px;
    }

    @media (max-width: 980px) {
      .grid { grid-template-columns: 1fr; }
      .row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>LMIT Wiki Console</h1>
      <p>Search the wiki, ask grounded questions, auto-sync topic and entity pages with LLMs, and manage provider fallback without leaving the browser.</p>
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
      </section>

      <section class="panel stack">
        <div>
          <h2>Knowledge Base</h2>
          <div id="kbStatusPanel" class="path-list"></div>
        </div>
        <div class="toolbar">
          <button onclick="runIngest()">Ingest</button>
          <button class="secondary" onclick="runLint()">Lint</button>
        </div>
        <div id="ingestStatus" class="status"></div>
        <div id="ingestOutput" class="stack"></div>
        <div id="lintStatus" class="status"></div>
        <div id="lintOutput" class="stack"></div>

        <div>
          <h2>LLM Settings</h2>
          <p class="tiny">OpenAI-compatible, Gemini, and Ollama are supported. Store API keys in environment variables and put only the variable name here. Fallback order uses profile ids.</p>
        </div>
        <div class="row">
          <input id="activeProfile" placeholder="Active profile id">
          <input id="fallbackOrder" placeholder="Fallback order, comma-separated">
        </div>
        <div id="profiles" class="stack"></div>
        <div class="toolbar">
          <button class="secondary" onclick="addProfile()">Add Profile</button>
          <button onclick="saveSettings()">Save Settings</button>
        </div>
        <div id="settingsStatus" class="status"></div>

        <div>
          <h2>Auto Sync</h2>
          <p class="tiny">Runs the LLM-driven topic/entity selection flow against sources that have not yet been synced, then appends source-grounded updates into wiki pages.</p>
          <div class="toolbar">
            <input id="syncLimit" placeholder="Optional source limit">
            <button onclick="runSync()">Sync Now</button>
          </div>
        </div>
        <div id="syncStatus" class="status"></div>
        <div id="syncOutput" class="stack"></div>
      </section>
    </div>
  </div>

  <template id="profileTemplate">
    <div class="profile stack">
      <h3>Profile</h3>
      <div class="row">
        <input data-field="id" placeholder="profile id">
        <input data-field="label" placeholder="label">
      </div>
      <div class="row">
        <select data-field="provider">
          <option value="ollama">ollama</option>
          <option value="openai_compatible">openai_compatible</option>
          <option value="gemini">gemini</option>
        </select>
        <input data-field="model" placeholder="model">
      </div>
      <input data-field="base_url" placeholder="base URL">
      <input data-field="api_key_env" placeholder="API key env var, e.g. OPENAI_API_KEY">
      <div class="tiny" data-field="api_key_hint"></div>
      <div class="row">
        <input data-field="temperature" placeholder="temperature">
        <input data-field="timeout_seconds" placeholder="timeout seconds">
      </div>
      <label class="tiny"><input type="checkbox" data-field="enabled"> Enabled</label>
    </div>
  </template>

  <script>
    async function boot() {
      await loadStatus();
      await loadSettings();
    }

    function status(id, text) {
      document.getElementById(id).textContent = text || "";
    }

    async function loadStatus() {
      const response = await fetch("/api/status");
      const data = await response.json();
      const root = document.getElementById("kbStatusPanel");
      root.innerHTML = "";
      const rows = [
        ["KB", data.root_dir],
        ["Raw", (data.source_dirs || []).join("; ")],
        ["Index", data.index_path],
        ["Log", data.log_path]
      ];
      for (const [label, value] of rows) {
        const div = document.createElement("div");
        div.textContent = `${label}: ${value || ""}`;
        root.appendChild(div);
      }
    }

    function renderProfiles(profiles) {
      const root = document.getElementById("profiles");
      root.innerHTML = "";
      for (const profile of profiles) {
        const node = document.getElementById("profileTemplate").content.firstElementChild.cloneNode(true);
        node.querySelector('[data-field="id"]').value = profile.id || "";
        node.querySelector('[data-field="label"]').value = profile.label || "";
        node.querySelector('[data-field="provider"]').value = profile.provider || "ollama";
        node.querySelector('[data-field="model"]').value = profile.model || "";
        node.querySelector('[data-field="base_url"]').value = profile.base_url || "";
        node.querySelector('[data-field="api_key_env"]').value = profile.api_key_env || "";
        node.querySelector('[data-field="temperature"]').value = profile.temperature ?? 0.2;
        node.querySelector('[data-field="timeout_seconds"]').value = profile.timeout_seconds ?? 90;
        node.querySelector('[data-field="enabled"]').checked = Boolean(profile.enabled);
        node.querySelector('[data-field="api_key_hint"]').textContent = profile.api_key_present
          ? `Stored key: ${profile.api_key_masked}`
          : "No stored key";
        root.appendChild(node);
      }
    }

    function addProfile() {
      const root = document.getElementById("profiles");
      renderProfiles([...collectProfiles(), {
        id: "",
        label: "",
        provider: "ollama",
        model: "",
        base_url: "",
        api_key_env: "",
        enabled: true,
        temperature: 0.2,
        timeout_seconds: 90,
        api_key_present: false,
        api_key_masked: ""
      }]);
    }

    function collectProfiles() {
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
      })).filter((profile) => profile.id);
    }

    async function loadSettings() {
      status("settingsStatus", "Loading settings...");
      const response = await fetch("/api/settings");
      const data = await response.json();
      document.getElementById("activeProfile").value = data.active_profile || "";
      document.getElementById("fallbackOrder").value = (data.fallback_order || []).join(", ");
      renderProfiles(data.profiles || []);
      status("settingsStatus", "Settings loaded.");
    }

    async function saveSettings() {
      status("settingsStatus", "Saving settings...");
      const payload = {
        active_profile: document.getElementById("activeProfile").value.trim() || null,
        fallback_order: document.getElementById("fallbackOrder").value.split(",").map((item) => item.trim()).filter(Boolean),
        profiles: collectProfiles()
      };
      const response = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (data.error) {
        status("settingsStatus", data.error);
        return;
      }
      renderProfiles(data.profiles || []);
      status("settingsStatus", "Settings saved.");
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
        summary.innerHTML = "<h3>Lint Passed</h3><div class=\"meta\">No warnings found.</div>";
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

