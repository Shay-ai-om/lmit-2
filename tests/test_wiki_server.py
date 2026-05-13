from __future__ import annotations

from io import BytesIO
import json
import time
from threading import Event

from lmit_wiki.query import QueryAnswer
from lmit_wiki.config import default_config, load_config, write_local_config
from lmit_wiki.auto import AutoSyncResult, SyncedPage
from lmit_wiki.runtime import save_runtime_settings
from lmit_wiki.server import INDEX_HTML, ThreadingWSGIServer, WikiWebApp, server_pid_path, stop_wiki_ui


def test_web_ui_exposes_status_ingest_and_lint(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "source.md").write_text("# Source\n\nLMIT Web UI ingest.", encoding="utf-8")

    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)

    status_payload = _call_json(app, "GET", "/api/status")
    assert status_payload["root_dir"] == str(cfg.wiki.root_dir)
    assert status_payload["source_dirs"] == [str(raw_dir.resolve())]
    assert status_payload["root_exists"] is None
    assert status_payload["source_dir_status"][0]["exists"] is None

    ingest_payload = _call_json(app, "POST", "/api/ingest")
    assert ingest_payload["source_count"] == 1
    assert ingest_payload["copied_raw_count"] == 1
    assert ingest_payload["source_note_count"] == 1

    lint_payload = _call_json(app, "POST", "/api/lint")
    assert lint_payload["passed"] is True
    assert lint_payload["warnings"] == []


def test_web_ui_search_results_include_document_and_raw_links(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "source.md").write_text("# Source\n\nOpenClaw raw source body.", encoding="utf-8")

    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)
    _call_json(app, "POST", "/api/ingest")

    payload = _call_json(app, "GET", "/api/search", query_string="q=OpenClaw")

    assert payload["results"]
    first = payload["results"][0]
    assert first["document_url"].startswith("/document?path=")
    if first["kind"] == "source":
        assert first["raw_url"]


def test_web_ui_exposes_llm_profile_controls_and_default_restore(tmp_path):
    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)

    html = _call_html(app, "GET", "/")
    assert "Add Ollama" in html
    assert "Add LM Studio REST" in html
    assert "Add LiteLLM" in html
    assert "Add OpenAI" in html
    assert "Add Gemini" in html
    assert "Restore Defaults" in html
    assert "Fetch Models" in html

    defaults = _call_json(app, "POST", "/api/settings/defaults")
    profile_ids = [profile["id"] for profile in defaults["profiles"]]
    assert profile_ids == [
        "ollama-local",
        "lm-studio-rest",
        "litellm-local",
        "openai-compatible",
        "gemini",
    ]
    lm_studio_rest = defaults["profiles"][1]
    litellm = defaults["profiles"][2]
    assert lm_studio_rest["base_url"] == "http://localhost:1234/api/v1"
    assert litellm["base_url"] == "http://localhost:4000"
    assert lm_studio_rest["timeout_seconds"] == 300
    assert litellm["timeout_seconds"] == 300
    assert litellm["api_key_env"] == "LITELLM_API_KEY"
    assert defaults["active_profile"] == "ollama-local"


def test_web_ui_can_save_knowledge_base_and_source_paths(tmp_path):
    config_path = tmp_path / "wiki-only.toml"
    cfg = write_local_config(
        config_path,
        root_dir=tmp_path / "initial_kb",
        source_dirs=[tmp_path / "initial_raw"],
    )
    app = WikiWebApp(cfg, config_path=config_path)

    new_root = tmp_path / "selected_kb"
    new_raw = tmp_path / "selected_raw"
    payload = _call_json(
        app,
        "POST",
        "/api/config",
        {
            "root_dir": str(new_root),
            "source_dirs": [str(new_raw)],
        },
    )

    assert payload["root_dir"] == str(new_root.resolve())
    assert payload["source_dirs"] == [str(new_raw.resolve())]
    assert (new_root / "wiki" / "index.md").exists()

    reloaded = load_config(config_path)
    assert reloaded.wiki.root_dir == new_root.resolve()
    assert reloaded.wiki_ingest.source_dirs == (new_raw.resolve(),)


def test_web_ui_save_paths_preserves_llm_settings_for_new_knowledge_base(tmp_path):
    config_path = tmp_path / "wiki-only.toml"
    cfg = write_local_config(
        config_path,
        root_dir=tmp_path / "initial_kb",
        source_dirs=[tmp_path / "initial_raw"],
    )
    save_runtime_settings(
        cfg,
        {
            "active_profile": "litellm-local",
            "fallback_order": ["litellm-local"],
            "profiles": [
                {
                    "id": "litellm-local",
                    "provider": "openai_compatible",
                    "label": "LiteLLM",
                    "base_url": "http://localhost:4000",
                    "model": "gpt-5",
                    "api_key_env": "LITELLM_API_KEY",
                    "enabled": True,
                    "temperature": 0.1,
                    "timeout_seconds": 300,
                }
            ],
        },
    )
    app = WikiWebApp(cfg, config_path=config_path)

    new_root = tmp_path / "selected_kb"
    _call_json(
        app,
        "POST",
        "/api/config",
        {
            "root_dir": str(new_root),
            "source_dirs": [str(tmp_path / "selected_raw")],
        },
    )
    settings = _call_json(app, "GET", "/api/settings")

    assert settings["active_profile"] == "litellm-local"
    assert settings["fallback_order"] == ["litellm-local"]
    assert settings["profiles"][0]["id"] == "litellm-local"
    assert settings["profiles"][0]["model"] == "gpt-5"
    assert settings["profiles"][0]["enabled"] is True
    assert json.loads((new_root / ".wiki_runtime.json").read_text(encoding="utf-8"))[
        "active_profile"
    ] == "litellm-local"


def test_web_ui_links_manual_and_exposes_path_controls(tmp_path):
    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)

    html = _call_html(app, "GET", "/")
    assert 'id="manualPanel"' in html
    assert "toggleManual()" in html
    assert 'href="/manual"' not in html
    assert "Knowledge Base Path" in html
    assert "Raw Source Paths" in html
    assert "Save Paths" in html
    assert "This did not run Ingest or call an LLM." in html
    assert html.index("<h2>Auto Sync</h2>") < html.index("<h2>LLM Settings</h2>")

    manual = _call_html(app, "GET", "/manual")
    assert "LMIT-2 Web UI 操作教學" in manual
    assert "Save Paths" in manual
    assert "Fetch Models" in manual
    assert "LM Studio REST" in manual
    assert "LiteLLM" in manual


def test_web_ui_script_keeps_newline_escape_sequences():
    assert 'buffer.indexOf("\\n")' in INDEX_HTML
    assert 'split(/\\r?\\n|;/)' in INDEX_HTML
    assert 'buffer.indexOf("' + "\n" + '")' not in INDEX_HTML


def test_web_ui_layout_keeps_long_paths_from_pushing_settings_panel():
    assert "min-width: 0;" in INDEX_HTML
    assert ".toolbar input:not([type=\"checkbox\"])" in INDEX_HTML
    assert "flex: 1 1 220px;" in INDEX_HTML


def test_document_route_serves_wiki_markdown_and_blocks_missing_paths(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "source.md").write_text("# Raw Title\n\nOpenClaw raw body.", encoding="utf-8")

    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)
    _call_json(app, "POST", "/api/ingest")

    search_payload = _call_json(app, "GET", "/api/search", query_string="q=OpenClaw")
    raw_item = next(item for item in search_payload["results"] if item["kind"] == "raw")
    document_html = _call_text(
        app,
        "GET",
        "/document",
        query_string=raw_item["document_url"].split("?", 1)[1],
    )

    assert "OpenClaw raw body." in document_html
    error_payload = _call_json(
        app,
        "GET",
        "/document",
        query_string="path=..%2Foutside.md",
        expect_ok=False,
    )
    assert "error" in error_payload


def test_web_ui_sync_runs_as_background_job(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)
    started = Event()
    release = Event()

    def fake_sync(cfg_arg, *, limit=None, progress=None, should_stop=None):
        assert cfg_arg == cfg
        assert limit == 2
        if progress is not None:
            progress(
                {
                    "message": "Preparing to sync 2 source(s).",
                    "total_sources": 2,
                    "processed_sources": 0,
                    "created_pages": 0,
                    "updated_pages": 0,
                }
            )
            progress(
                {
                    "message": "Syncing source 1 of 2: Alpha",
                    "total_sources": 2,
                    "processed_sources": 0,
                    "created_pages": 0,
                    "updated_pages": 0,
                    "current_source_title": "Alpha",
                    "current_relative_path": "wiki/sources/alpha.md",
                }
            )
        started.set()
        assert release.wait(2), "background sync never resumed"
        return AutoSyncResult(
            processed_sources=2,
            created_pages=1,
            updated_pages=1,
            pages=(
                SyncedPage(
                    name="Alpha Topic",
                    kind="topic",
                    path=cfg.wiki.topics_dir / "alpha-topic.md",
                    action="created",
                ),
                SyncedPage(
                    name="Alpha Entity",
                    kind="entity",
                    path=cfg.wiki.entities_dir / "alpha-entity.md",
                    action="updated",
                ),
            ),
        )

    monkeypatch.setattr("lmit_wiki.server.auto_sync_wiki", fake_sync)

    start_payload = _call_json(app, "POST", "/api/sync", {"limit": 2})
    assert start_payload["started"] is True
    job_id = start_payload["job"]["job_id"]
    assert started.wait(1)

    reused_payload = _call_json(app, "POST", "/api/sync", {"limit": 2})
    assert reused_payload["started"] is False
    assert reused_payload["job"]["job_id"] == job_id

    running_payload = _call_json(app, "GET", "/api/sync", query_string=f"job_id={job_id}")
    assert running_payload["job"]["status"] == "running"
    assert running_payload["job"]["current_source_title"] == "Alpha"
    assert running_payload["job"]["total_sources"] == 2

    release.set()
    deadline = time.time() + 2
    completed_payload = running_payload
    while time.time() < deadline:
        completed_payload = _call_json(app, "GET", "/api/sync", query_string=f"job_id={job_id}")
        if completed_payload["job"]["status"] == "completed":
            break
        time.sleep(0.05)

    assert completed_payload["job"]["status"] == "completed"
    assert completed_payload["job"]["processed_sources"] == 2
    assert completed_payload["job"]["created_pages"] == 1
    assert completed_payload["job"]["updated_pages"] == 1
    assert completed_payload["job"]["pages"][0]["name"] == "Alpha Topic"


def test_web_ui_sync_can_stop_and_resume_background_job(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)
    started = Event()
    calls = {"count": 0}

    def fake_sync(cfg_arg, *, limit=None, progress=None, should_stop=None):
        assert cfg_arg == cfg
        calls["count"] += 1
        if calls["count"] == 1:
            if progress is not None:
                progress(
                    {
                        "message": "Syncing source 1 of 2: Alpha",
                        "total_sources": 2,
                        "processed_sources": 0,
                        "created_pages": 0,
                        "updated_pages": 0,
                        "current_source_title": "Alpha",
                        "current_relative_path": "wiki/sources/alpha.md",
                        "stage": "source",
                    }
                )
            started.set()
            deadline = time.time() + 2
            while time.time() < deadline:
                if should_stop is not None and should_stop():
                    return AutoSyncResult(
                        processed_sources=1,
                        created_pages=0,
                        updated_pages=0,
                        pages=(),
                        status="stopped",
                    )
                time.sleep(0.02)
            raise AssertionError("stop was never requested")
        return AutoSyncResult(
            processed_sources=1,
            created_pages=1,
            updated_pages=0,
            pages=(
                SyncedPage(
                    name="Beta Topic",
                    kind="topic",
                    path=cfg.wiki.topics_dir / "beta-topic.md",
                    action="created",
                ),
            ),
            status="completed",
        )

    monkeypatch.setattr("lmit_wiki.server.auto_sync_wiki", fake_sync)

    start_payload = _call_json(app, "POST", "/api/sync", {"limit": 2})
    first_job_id = start_payload["job"]["job_id"]
    assert start_payload["started"] is True
    assert started.wait(1)

    stop_payload = _call_json(app, "POST", "/api/sync/stop")
    assert stop_payload["stopped"] is True
    assert stop_payload["job"]["job_id"] == first_job_id

    deadline = time.time() + 2
    stopped_payload = stop_payload
    while time.time() < deadline:
        stopped_payload = _call_json(app, "GET", "/api/sync", query_string=f"job_id={first_job_id}")
        if stopped_payload["job"]["status"] == "stopped":
            break
        time.sleep(0.05)

    assert stopped_payload["job"]["status"] == "stopped"
    assert stopped_payload["job"]["processed_sources"] == 1

    resume_payload = _call_json(app, "POST", "/api/sync/resume", {"limit": 2})
    assert resume_payload["started"] is True
    assert resume_payload["job"]["job_id"] != first_job_id

    second_job_id = resume_payload["job"]["job_id"]
    deadline = time.time() + 2
    completed_payload = resume_payload
    while time.time() < deadline:
        completed_payload = _call_json(app, "GET", "/api/sync", query_string=f"job_id={second_job_id}")
        if completed_payload["job"]["status"] == "completed":
            break
        time.sleep(0.05)

    assert completed_payload["job"]["status"] == "completed"
    assert completed_payload["job"]["pages"][0]["name"] == "Beta Topic"
    assert calls["count"] == 2


def test_web_ui_streams_query_answer_chunks(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)

    def fake_stream_query(cfg_arg, question, *, save=True, on_chunk=None):
        assert cfg_arg == cfg
        assert question == "what changed?"
        assert save is False
        if on_chunk is not None:
            on_chunk("# Changes\n\n")
            on_chunk("The wiki changed. [S1]")
        return QueryAnswer(
            question=question,
            title="Changes",
            answer_markdown="# Changes\n\nThe wiki changed. [S1]",
            follow_up_questions=("What should we update next?",),
            search_results=(),
            completion=None,
            saved_path=None,
        )

    monkeypatch.setattr("lmit_wiki.server.stream_wiki_query_answer", fake_stream_query)

    body = _call_text(
        app,
        "POST",
        "/api/query/stream",
        payload={"question": "what changed?", "save": False},
        content_length=True,
    )

    events = [json.loads(line) for line in body.splitlines() if line.strip()]
    assert events[0]["type"] == "start"
    assert events[1] == {"type": "chunk", "text": "# Changes\n\n"}
    assert events[2] == {"type": "chunk", "text": "The wiki changed. [S1]"}
    assert events[3]["type"] == "done"
    assert events[3]["title"] == "Changes"
    assert events[3]["follow_up_questions"] == ["What should we update next?"]


def test_web_ui_fetches_model_choices_for_supported_providers(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)

    def fake_fetch(provider: str, base_url: str, *, api_key_env: str = "", timeout_seconds: int = 8):
        assert provider == "lmstudio_rest"
        assert base_url == "http://localhost:1234/api/v1"
        assert api_key_env == "LM_STUDIO_API_TOKEN"
        assert timeout_seconds == 8
        return [{"id": "google/gemma-4-e4b-it", "label": "Gemma -> google/gemma-4-e4b-it [loaded]"}]

    monkeypatch.setattr("lmit_wiki.server.fetch_model_choices", fake_fetch)

    payload = _call_json(
        app,
        "GET",
        "/api/models",
        query_string=(
            "provider=lmstudio_rest&base_url=http%3A%2F%2Flocalhost%3A1234%2Fapi%2Fv1"
            "&api_key_env=LM_STUDIO_API_TOKEN"
        ),
    )

    assert payload["models"][0]["id"] == "google/gemma-4-e4b-it"


def test_web_ui_uses_threaded_server_and_nonblocking_status():
    assert ThreadingWSGIServer.daemon_threads is True
    assert 'item.exists ? "yes" : "no"' not in INDEX_HTML


def test_stop_wiki_ui_uses_pid_file_from_config_dir(tmp_path, monkeypatch):
    config_path = tmp_path / "cfg" / "wiki-only.toml"
    cfg = write_local_config(
        config_path,
        root_dir=tmp_path / "kb",
        source_dirs=[tmp_path / "raw"],
    )
    pid_path = server_pid_path(cfg, config_path=config_path)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("43210", encoding="utf-8")
    seen: dict[str, int] = {}

    def fake_kill(pid: int, sig: int) -> None:
        seen["pid"] = pid
        seen["sig"] = sig

    monkeypatch.setattr("lmit_wiki.server.os.kill", fake_kill)

    stopped, message = stop_wiki_ui(cfg, config_path=config_path)

    assert stopped is True
    assert "43210" in message
    assert seen == {"pid": 43210, "sig": 15}
    assert not pid_path.exists()


def _call_json(
    app: WikiWebApp,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    query_string: str = "",
    expect_ok: bool = True,
) -> dict:
    body = json.dumps(payload or {}).encode("utf-8") if method == "POST" else b""
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = headers

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": BytesIO(body),
    }

    response_body = b"".join(app(environ, start_response))
    if expect_ok:
        assert str(captured["status"]).startswith("2")
    else:
        assert not str(captured["status"]).startswith("2")
    return json.loads(response_body.decode("utf-8"))


def _call_html(app: WikiWebApp, method: str, path: str) -> str:
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = headers

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_LENGTH": "0",
        "wsgi.input": BytesIO(b""),
    }

    response_body = b"".join(app(environ, start_response))
    assert str(captured["status"]).startswith("200 ")
    return response_body.decode("utf-8")


def _call_text(
    app: WikiWebApp,
    method: str,
    path: str,
    *,
    query_string: str = "",
    payload: dict | None = None,
    content_length: bool = False,
) -> str:
    body = json.dumps(payload or {}).encode("utf-8") if method == "POST" else b""
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = headers

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "CONTENT_LENGTH": str(len(body)) if content_length else "0",
        "wsgi.input": BytesIO(body),
    }

    response_body = b"".join(app(environ, start_response))
    assert str(captured["status"]).startswith("200 ")
    return response_body.decode("utf-8")
