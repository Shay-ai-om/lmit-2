from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_standalone_cli_and_no_browser_install():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python:3.11-slim" in text
    assert "lmit-wiki" in text
    assert "playwright install" not in text.lower()
    assert "chromium" not in text.lower()


def test_compose_mounts_raw_read_only_and_kb_writable():
    text = (ROOT / "docker-compose.example.yml").read_text(encoding="utf-8")

    assert "/data/raw/ai:ro" in text
    assert "/data/knowledge_base" in text
    assert "8765:8765" in text
    assert "Dockerfile" in text


def test_nas_deployment_docs_name_external_llm_and_unraid():
    text = (ROOT / "docs" / "nas-docker-deployment.md").read_text(encoding="utf-8")

    assert "Unraid" in text
    assert "external LLM" in text
    assert "read-only" in text
