from __future__ import annotations

from io import BytesIO
import json

from lmit_wiki.config import default_config
from lmit_wiki.server import WikiWebApp


def test_web_ui_exposes_status_ingest_and_lint(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "source.md").write_text("# Source\n\nLMIT Web UI ingest.", encoding="utf-8")

    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)

    status_payload = _call_json(app, "GET", "/api/status")
    assert status_payload["root_dir"] == str(cfg.wiki.root_dir)
    assert status_payload["source_dirs"] == [str(raw_dir)]

    ingest_payload = _call_json(app, "POST", "/api/ingest")
    assert ingest_payload["source_count"] == 1
    assert ingest_payload["copied_raw_count"] == 1
    assert ingest_payload["source_note_count"] == 1

    lint_payload = _call_json(app, "POST", "/api/lint")
    assert lint_payload["passed"] is True
    assert lint_payload["warnings"] == []


def _call_json(app: WikiWebApp, method: str, path: str) -> dict:
    body = b"{}" if method == "POST" else b""
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = headers

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": BytesIO(body),
    }

    response_body = b"".join(app(environ, start_response))
    assert str(captured["status"]).startswith("200 ")
    return json.loads(response_body.decode("utf-8"))
