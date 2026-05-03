from __future__ import annotations

import json

from lmit_wiki.config import default_config
from lmit_wiki.builder import init_wiki
from lmit_wiki.runtime import invoke_text_completion, save_runtime_settings
from lmit_wiki.server import INDEX_HTML


def test_runtime_settings_serializes_api_key_env_not_secret(tmp_path):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    payload = {
        "active_profile": "openai",
        "fallback_order": ["openai"],
        "profiles": [
            {
                "id": "openai",
                "provider": "openai_compatible",
                "label": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4.1-mini",
                "api_key_env": "OPENAI_API_KEY",
                "api_key": "sk-should-not-be-written",
                "enabled": True,
            }
        ],
    }

    save_runtime_settings(cfg, payload)

    text = cfg.wiki_runtime.settings_path.read_text(encoding="utf-8")
    stored = json.loads(text)
    assert stored["profiles"][0]["api_key_env"] == "OPENAI_API_KEY"
    assert "api_key" not in stored["profiles"][0]
    assert "sk-should-not-be-written" not in text


def test_openai_compatible_profile_resolves_key_from_configured_env(
    tmp_path,
    monkeypatch,
):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    save_runtime_settings(
        cfg,
        {
            "active_profile": "openai",
            "fallback_order": ["openai"],
            "profiles": [
                {
                    "id": "openai",
                    "provider": "openai_compatible",
                    "label": "OpenAI",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4.1-mini",
                    "api_key_env": "CUSTOM_OPENAI_KEY",
                    "enabled": True,
                }
            ],
        },
    )
    monkeypatch.setenv("CUSTOM_OPENAI_KEY", "sk-from-env")
    captured: dict[str, object] = {}

    def fake_post_json(url, headers, payload, *, timeout_seconds):
        captured["headers"] = headers
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr("lmit_wiki.runtime._post_json", fake_post_json)

    completion = invoke_text_completion(
        cfg,
        [{"role": "user", "content": "hello"}],
        purpose="secret test",
    )

    assert completion.content == "ok"
    assert captured["headers"]["Authorization"] == "Bearer sk-from-env"


def test_wiki_settings_ui_uses_api_key_env_field():
    assert 'data-field="api_key_env"' in INDEX_HTML
    assert 'data-field="api_key"' not in INDEX_HTML

