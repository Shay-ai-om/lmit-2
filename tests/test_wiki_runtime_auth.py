from __future__ import annotations

import json
import sys

from lmit_wiki.config import default_config
from lmit_wiki.builder import init_wiki
from lmit_wiki.runtime import (
    invoke_text_completion,
    load_runtime_settings,
    parse_json_document,
    RuntimeSettingsError,
    runtime_settings_public_payload,
    save_runtime_settings,
)
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


def test_openai_compatible_profile_resolves_key_from_dotenv_file(
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
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "CUSTOM_OPENAI_KEY=sk-from-dotenv\nGEMINI_API_KEY='gemini-from-dotenv'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("CUSTOM_OPENAI_KEY", raising=False)
    monkeypatch.setenv("LMIT_WIKI_DOTENV", str(dotenv))
    captured: dict[str, object] = {}

    def fake_post_json(url, headers, payload, *, timeout_seconds):
        captured["headers"] = headers
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr("lmit_wiki.runtime._post_json", fake_post_json)

    completion = invoke_text_completion(
        cfg,
        [{"role": "user", "content": "hello"}],
        purpose="dotenv secret test",
    )

    assert completion.content == "ok"
    assert captured["headers"]["Authorization"] == "Bearer sk-from-dotenv"


def test_local_openai_compatible_profile_can_omit_api_key(
    tmp_path,
    monkeypatch,
):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    save_runtime_settings(
        cfg,
        {
            "active_profile": "lm-studio",
            "fallback_order": ["lm-studio"],
            "profiles": [
                {
                    "id": "lm-studio",
                    "provider": "openai_compatible",
                    "label": "LM Studio",
                    "base_url": "http://localhost:1234/v1",
                    "model": "local-model",
                    "api_key_env": "",
                    "enabled": True,
                }
            ],
        },
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    captured: dict[str, object] = {}

    def fake_post_json(url, headers, payload, *, timeout_seconds):
        captured["url"] = url
        captured["headers"] = headers
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr("lmit_wiki.runtime._post_json", fake_post_json)

    completion = invoke_text_completion(
        cfg,
        [{"role": "user", "content": "hello"}],
        purpose="lm studio local test",
    )

    assert completion.content == "ok"
    assert captured["url"] == "http://localhost:1234/v1/chat/completions"
    assert "Authorization" not in captured["headers"]


def test_lmstudio_rest_profile_can_omit_api_key(
    tmp_path,
    monkeypatch,
):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    save_runtime_settings(
        cfg,
        {
            "active_profile": "lmstudio-rest",
            "fallback_order": ["lmstudio-rest"],
            "profiles": [
                {
                    "id": "lmstudio-rest",
                    "provider": "lmstudio_rest",
                    "label": "LM Studio REST",
                    "base_url": "http://localhost:1234/api/v1",
                    "model": "google/gemma-4-e4b-it",
                    "api_key_env": "",
                    "enabled": True,
                }
            ],
        },
    )
    monkeypatch.delenv("LM_STUDIO_API_TOKEN", raising=False)
    captured: dict[str, object] = {}

    def fake_post_json(url, headers, payload, *, timeout_seconds):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = payload
        return {"output": [{"type": "message", "content": "ok"}]}

    monkeypatch.setattr("lmit_wiki.runtime._post_json", fake_post_json)

    completion = invoke_text_completion(
        cfg,
        [{"role": "system", "content": "be grounded"}, {"role": "user", "content": "hello"}],
        purpose="lm studio rest local test",
    )

    assert completion.content == "ok"
    assert captured["url"] == "http://localhost:1234/api/v1/chat"
    assert captured["payload"]["system_prompt"] == "be grounded"
    assert captured["payload"]["input"] == "User:\nhello"
    assert "Authorization" not in captured["headers"]


def test_runtime_public_payload_does_not_expose_stored_key_status(tmp_path, monkeypatch):
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
                    "api_key_env": "OPENAI_API_KEY",
                    "enabled": True,
                }
            ],
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live")

    payload = runtime_settings_public_payload(load_runtime_settings(cfg))

    assert "api_key_present" not in payload["profiles"][0]
    assert "api_key_masked" not in payload["profiles"][0]


def test_default_local_profile_timeout_is_upgraded_to_300_seconds(tmp_path):
    cfg = default_config(tmp_path)
    init_wiki(cfg)

    save_runtime_settings(
        cfg,
        {
            "active_profile": "litellm-local",
            "fallback_order": ["litellm-local", "lm-studio-rest"],
            "profiles": [
                {
                    "id": "litellm-local",
                    "provider": "openai_compatible",
                    "label": "LiteLLM",
                    "base_url": "http://localhost:4000",
                    "model": "gpt-5",
                    "enabled": True,
                    "timeout_seconds": 120,
                },
                {
                    "id": "lm-studio-rest",
                    "provider": "lmstudio_rest",
                    "label": "LM Studio REST",
                    "base_url": "http://localhost:1234/api/v1",
                    "model": "local-model",
                    "enabled": True,
                    "timeout_seconds": 120,
                },
            ],
        },
    )

    settings = load_runtime_settings(cfg)

    assert settings.profiles[0].timeout_seconds == 300
    assert settings.profiles[1].timeout_seconds == 300


def test_openai_compatible_lmstudio_errors_include_rest_hint(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    save_runtime_settings(
        cfg,
        {
            "active_profile": "lm-studio",
            "fallback_order": ["lm-studio"],
            "profiles": [
                {
                    "id": "lm-studio",
                    "provider": "openai_compatible",
                    "label": "LM Studio",
                    "base_url": "http://localhost:1234/v1",
                    "model": "bad-model",
                    "enabled": True,
                }
            ],
        },
    )

    def fake_post_json(url, headers, payload, *, timeout_seconds):
        raise RuntimeSettingsError("400 from http://localhost:1234/v1/chat/completions: model is not loaded")

    monkeypatch.setattr("lmit_wiki.runtime._post_json", fake_post_json)

    try:
        invoke_text_completion(
            cfg,
            [{"role": "user", "content": "hello"}],
            purpose="lm studio bad model test",
        )
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("expected invoke_text_completion to fail")

    assert "LM Studio REST" in message


def test_lmstudio_timeout_message_suggests_longer_timeout(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    save_runtime_settings(
        cfg,
        {
            "active_profile": "lm-studio",
            "fallback_order": ["lm-studio"],
            "profiles": [
                {
                    "id": "lm-studio",
                    "provider": "openai_compatible",
                    "label": "LM Studio",
                    "base_url": "http://localhost:1234/v1",
                    "model": "slow-model",
                    "enabled": True,
                    "timeout_seconds": 300,
                }
            ],
        },
    )

    def fake_post_json(url, headers, payload, *, timeout_seconds):
        raise RuntimeSettingsError(
            "Request to http://localhost:1234/v1/chat/completions timed out after 300 seconds. "
            "Increase Timeout Seconds for this profile, or switch to LM Studio REST if the OpenAI-compatible path is slow on this model."
        )

    monkeypatch.setattr("lmit_wiki.runtime._post_json", fake_post_json)

    try:
        invoke_text_completion(
            cfg,
            [{"role": "user", "content": "hello"}],
            purpose="lm studio timeout test",
        )
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("expected invoke_text_completion to fail")

    assert "Increase Timeout Seconds" in message
    assert "LM Studio REST" in message


def test_packaged_dotenv_path_is_install_folder(tmp_path, monkeypatch):
    import lmit_wiki.runtime as runtime

    exe = tmp_path / "LMIT-2 Wiki" / "lmit-wiki.exe"
    monkeypatch.setenv("LMIT_WIKI_DOTENV", "")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    paths = runtime._dotenv_candidate_paths()

    assert paths[0] == exe.parent / ".env"


def test_wiki_settings_ui_uses_api_key_env_field():
    assert 'data-field="api_key_env"' in INDEX_HTML
    assert 'data-field="api_key"' not in INDEX_HTML
    assert "Stored key:" not in INDEX_HTML


def test_parse_json_document_accepts_trailing_text_after_first_object():
    payload = parse_json_document(
        '{"source_summary":"ok","topics":[],"entities":[]}\n\nAdditional notes that should be ignored.'
    )

    assert payload == {
        "source_summary": "ok",
        "topics": [],
        "entities": [],
    }

