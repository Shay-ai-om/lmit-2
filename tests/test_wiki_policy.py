from __future__ import annotations

import json

from lmit_wiki.config import default_config
from lmit_wiki.auto import auto_sync_wiki
from lmit_wiki.builder import ingest_wiki, init_wiki
from lmit_wiki.policy import (
    filter_profiles_for_policy,
    llm_policy_for_sources,
    provider_is_external,
    source_visibility,
)
from lmit_wiki.runtime import LLMProfile, invoke_text_completion
from lmit_wiki.query import answer_wiki_query


def test_source_visibility_marks_login_and_local_sources_private():
    assert (
        source_visibility({"urls": ["https://m.facebook.com/story.php?id=1"]})
        == "restricted_or_login"
    )
    assert source_visibility({"urls": []}) == "local_private"


def test_source_visibility_treats_malformed_urls_as_local_private():
    assert source_visibility({"urls": ["https://[not-ipv6/path"]}) == "local_private"


def test_llm_policy_does_not_block_configured_providers():
    assert llm_policy_for_sources([{"visibility": "public_web"}]) == "external_llm_allowed"
    assert (
        llm_policy_for_sources(
            [{"visibility": "public_web"}, {"visibility": "local_private"}]
        )
        == "external_llm_allowed"
    )


def test_openai_compatible_private_lan_endpoint_is_not_external():
    profile = LLMProfile(
        profile_id="lan",
        provider="openai_compatible",
        label="LAN",
        base_url="http://192.168.1.10:1234/v1",
        model="local-model",
        api_key_env="LAN_LLM_API_KEY",
    )

    assert provider_is_external(profile) is False


def test_lmstudio_rest_endpoint_is_not_external():
    profile = LLMProfile(
        profile_id="lmstudio",
        provider="lmstudio_rest",
        label="LM Studio REST",
        base_url="http://localhost:1234/api/v1",
        model="google/gemma-4-e4b-it",
        api_key_env="",
    )

    assert provider_is_external(profile) is False


def test_policy_filter_keeps_all_configured_profiles():
    external = LLMProfile(
        profile_id="openai",
        provider="openai_compatible",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        model="gpt-4.1-mini",
        api_key_env="OPENAI_API_KEY",
    )
    local = LLMProfile(
        profile_id="ollama",
        provider="ollama",
        label="Ollama",
        base_url="http://localhost:11434/api",
        model="llama3.1",
        api_key_env="",
    )

    assert filter_profiles_for_policy([external, local], "local_only") == [external, local]


def test_ingest_manifest_records_source_visibility_and_llm_policy(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "facebook.md").write_text(
        "Source: https://www.facebook.com/share/p/example/",
        encoding="utf-8",
    )
    cfg = default_config(tmp_path)

    ingest_wiki(cfg, source_dirs=[source_dir])

    manifest = json.loads((cfg.wiki.root_dir / "manifest.json").read_text(encoding="utf-8"))
    record = manifest["sources"][0]
    assert record["visibility"] == "restricted_or_login"
    assert record["llm_policy"] == "external_llm_allowed"


def test_ingest_manifest_handles_malformed_url_without_ipv6_error(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "malformed.md").write_text(
        "Broken URL copied from a page: https://[not-ipv6/path",
        encoding="utf-8",
    )
    cfg = default_config(tmp_path)

    ingest_wiki(cfg, source_dirs=[source_dir])

    manifest = json.loads((cfg.wiki.root_dir / "manifest.json").read_text(encoding="utf-8"))
    record = manifest["sources"][0]
    assert record["urls"] == ["https://[not-ipv6/path"]
    assert record["visibility"] == "local_private"
    assert record["llm_policy"] == "external_llm_allowed"


def test_runtime_allows_external_profiles_for_local_only_policy(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    cfg.wiki_runtime.settings_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_profile": "openai",
                "fallback_order": ["openai"],
                "profiles": [
                    {
                        "id": "openai",
                        "provider": "openai_compatible",
                        "label": "OpenAI",
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4.1-mini",
                        "api_key": "sk-test",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_post_json(url, headers, payload, *, timeout_seconds):
        captured["url"] = url
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("lmit_wiki.runtime._post_json", fake_post_json)

    completion = invoke_text_completion(
        cfg,
        [{"role": "user", "content": "hello"}],
        purpose="policy test",
        llm_policy="local_only",
    )

    assert completion.content == "ok"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"


def test_auto_sync_passes_source_policy_to_llm(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "private.md").write_text(
        "Private source https://www.facebook.com/share/p/example/",
        encoding="utf-8",
    )
    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[source_dir])
    seen_policies: list[str] = []

    def fake_completion(cfg_arg, messages, *, purpose, llm_policy):
        seen_policies.append(llm_policy)
        return {"source_summary": "x", "topics": [], "entities": []}, object()

    monkeypatch.setattr("lmit_wiki.auto.invoke_json_completion", fake_completion)

    auto_sync_wiki(cfg, limit=1)

    assert seen_policies == ["external_llm_allowed"]


def test_query_passes_combined_search_policy_to_llm(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "private.md").write_text(
        "Private source https://www.facebook.com/share/p/example/",
        encoding="utf-8",
    )
    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[source_dir])
    seen_policies: list[str] = []

    def fake_completion(cfg_arg, messages, *, purpose, llm_policy):
        seen_policies.append(llm_policy)
        return {
            "title": "Facebook source",
            "answer_markdown": "Only local context was used. [S1]",
            "follow_up_questions": [],
        }, object()

    monkeypatch.setattr("lmit_wiki.query.invoke_json_completion", fake_completion)

    answer = answer_wiki_query(cfg, "facebook", save=False)

    assert answer.title == "Facebook source"
    assert seen_policies == ["external_llm_allowed"]

