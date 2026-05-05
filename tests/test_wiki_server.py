from __future__ import annotations

from io import BytesIO
import json

from lmit_wiki.config import default_config, load_config, write_local_config
from lmit_wiki.server import INDEX_HTML, ThreadingWSGIServer, WikiWebApp


def test_web_ui_exposes_status_ingest_and_lint(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "source.md").write_text("# Source\n\nLMIT Web UI ingest.", encoding="utf-8")

    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)

    status_payload = _call_json(app, "GET", "/api/status")
    assert status_payload["root_dir"] == str(cfg.wiki.root_dir)
    assert status_payload["source_dirs"] == [str(raw_dir)]
    assert status_payload["root_exists"] is None
    assert status_payload["source_dir_status"][0]["exists"] is None

    ingest_payload = _call_json(app, "POST", "/api/ingest")
    assert ingest_payload["source_count"] == 1
    assert ingest_payload["copied_raw_count"] == 1
    assert ingest_payload["source_note_count"] == 1

    lint_payload = _call_json(app, "POST", "/api/lint")
    assert lint_payload["passed"] is True
    assert lint_payload["warnings"] == []


def test_web_ui_exposes_llm_profile_controls_and_default_restore(tmp_path):
    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)

    html = _call_html(app, "GET", "/")
    assert "Add Ollama" in html
    assert "Add LM Studio" in html
    assert "Add LM Studio REST" in html
    assert "Add OpenAI" in html
    assert "Add Gemini" in html
    assert "Restore Defaults" in html
    assert "Fetch Models" in html

    defaults = _call_json(app, "POST", "/api/settings/defaults")
    profile_ids = [profile["id"] for profile in defaults["profiles"]]
    assert profile_ids == [
        "ollama-local",
        "lm-studio-local",
        "lm-studio-rest",
        "openai-compatible",
        "gemini",
    ]
    lm_studio = defaults["profiles"][1]
    lm_studio_rest = defaults["profiles"][2]
    assert lm_studio["base_url"] == "http://localhost:1234/v1"
    assert lm_studio_rest["base_url"] == "http://localhost:1234/api/v1"
    assert lm_studio["api_key_env"] == ""
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


def _call_json(
    app: WikiWebApp,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    query_string: str = "",
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
    assert str(captured["status"]).startswith("200 ")
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
