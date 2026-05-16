from __future__ import annotations

import json
from pathlib import Path

import pytest

from lmit_wiki.auto import auto_sync_wiki, _curate_extracted_updates, _sync_source_signature
from lmit_wiki.builder import ingest_wiki
from lmit_wiki.config import default_config
from lmit_wiki.runtime import LLMInvocationError, default_runtime_settings_payload, save_runtime_settings


def test_auto_sync_saves_completed_progress_before_later_failure(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    raw_dir = cfg.wiki_ingest.source_dirs[0]
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "alpha.md").write_text("# Alpha\n\none", encoding="utf-8")
    (raw_dir / "beta.md").write_text("# Beta\n\ntwo", encoding="utf-8")
    ingest_wiki(cfg)

    manifest = json.loads((cfg.wiki.root_dir / "manifest.json").read_text(encoding="utf-8"))
    first_record = manifest["sources"][0]

    calls: list[str] = []

    def fake_extract(cfg_arg, record, catalog):
        calls.append(str(record["title"]))
        if len(calls) == 1:
            return {"topics": [], "entities": []}
        raise RuntimeError("litellm-local returned invalid JSON for wiki auto sync: Extra data")

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", fake_extract)

    with pytest.raises(RuntimeError):
        auto_sync_wiki(cfg)

    state = json.loads(cfg.wiki_runtime.state_path.read_text(encoding="utf-8"))
    assert state["processed_sources"] == {
        first_record["relative_path"]: _sync_source_signature(first_record)
    }


def test_auto_sync_resume_skips_sources_already_saved_in_state(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    raw_dir = cfg.wiki_ingest.source_dirs[0]
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "alpha.md").write_text("# Alpha\n\none", encoding="utf-8")
    (raw_dir / "beta.md").write_text("# Beta\n\ntwo", encoding="utf-8")
    ingest_wiki(cfg)

    calls: list[str] = []

    def fail_on_second(cfg_arg, record, catalog):
        calls.append(str(record["title"]))
        if len(calls) == 1:
            return {"topics": [], "entities": []}
        raise RuntimeError("invalid JSON on second source")

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", fail_on_second)
    with pytest.raises(RuntimeError):
        auto_sync_wiki(cfg)

    resumed_calls: list[str] = []

    def resume_only_remaining(cfg_arg, record, catalog):
        resumed_calls.append(str(record["title"]))
        return {"topics": [], "entities": []}

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", resume_only_remaining)
    result = auto_sync_wiki(cfg)

    assert result.processed_sources == 1
    assert resumed_calls == ["Beta"]


def test_auto_sync_reruns_when_source_note_signature_changes(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    raw_dir = cfg.wiki_ingest.source_dirs[0]
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "alpha.md").write_text("# Alpha\n\none", encoding="utf-8")
    ingest_wiki(cfg)

    calls: list[str] = []

    def fake_extract(cfg_arg, record, catalog):
        calls.append(str(record["title"]))
        return {"topics": [], "entities": []}

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", fake_extract)
    first = auto_sync_wiki(cfg)
    assert first.processed_sources == 1
    assert calls == ["Alpha"]

    calls.clear()
    second = auto_sync_wiki(cfg)
    assert second.processed_sources == 0
    assert calls == []

    manifest = json.loads((cfg.wiki.root_dir / "manifest.json").read_text(encoding="utf-8"))
    note_path = Path(manifest["sources"][0]["source_note_path"])
    note_path.write_text(
        note_path.read_text(encoding="utf-8") + "\n\nNew generated source-note context.",
        encoding="utf-8",
    )

    third = auto_sync_wiki(cfg)
    assert third.processed_sources == 1
    assert calls == ["Alpha"]


def test_auto_sync_force_processes_saved_sources(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    raw_dir = cfg.wiki_ingest.source_dirs[0]
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "alpha.md").write_text("# Alpha\n\none", encoding="utf-8")
    ingest_wiki(cfg)

    calls: list[str] = []

    def fake_extract(cfg_arg, record, catalog):
        calls.append(str(record["title"]))
        return {"topics": [], "entities": []}

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", fake_extract)
    first = auto_sync_wiki(cfg)
    assert first.processed_sources == 1
    calls.clear()

    normal = auto_sync_wiki(cfg)
    forced = auto_sync_wiki(cfg, force=True)

    assert normal.processed_sources == 0
    assert forced.processed_sources == 1
    assert calls == ["Alpha"]


def test_auto_sync_reruns_and_uses_global_raw_excerpt_capacity(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    raw_dir = cfg.wiki_ingest.source_dirs[0]
    raw_dir.mkdir(parents=True, exist_ok=True)
    late_detail = "Late raw detail only visible when sync raw excerpt capacity is higher."
    (raw_dir / "alpha.md").write_text(
        "# Alpha\n\n" + ("opening filler " * 80) + "\n\n" + late_detail,
        encoding="utf-8",
    )
    settings = default_runtime_settings_payload()
    settings["raw_excerpt_char_limit"] = 500
    save_runtime_settings(cfg, settings)
    ingest_wiki(cfg)

    captured_prompts: list[str] = []

    def fake_completion(cfg_arg, messages, *, purpose, llm_policy):
        captured_prompts.append(messages[-1]["content"])
        return {"topics": [], "entities": []}, None

    monkeypatch.setattr("lmit_wiki.auto.invoke_json_completion", fake_completion)

    first = auto_sync_wiki(cfg)
    assert first.processed_sources == 1
    assert late_detail not in captured_prompts[-1]

    captured_prompts.clear()
    unchanged = auto_sync_wiki(cfg)
    assert unchanged.processed_sources == 0
    assert captured_prompts == []

    settings["raw_excerpt_char_limit"] = 2500
    save_runtime_settings(cfg, settings)
    rerun = auto_sync_wiki(cfg)

    assert rerun.processed_sources == 1
    assert late_detail in captured_prompts[-1]


def test_auto_sync_records_llm_failures_and_continues_sources(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    raw_dir = cfg.wiki_ingest.source_dirs[0]
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "alpha.md").write_text("# Alpha\n\none", encoding="utf-8")
    (raw_dir / "beta.md").write_text("# Beta\n\ntwo", encoding="utf-8")
    ingest_wiki(cfg)

    calls: list[str] = []

    def fail_first_source(cfg_arg, record, catalog):
        calls.append(str(record["title"]))
        if len(calls) == 1:
            raise LLMInvocationError("provider timed out")
        return {
            "topics": [
                {
                    "name": "Beta Topic",
                    "summary": "Beta summary",
                    "key_points": ["Beta point"],
                    "open_questions": [],
                }
            ],
            "entities": [],
        }

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", fail_first_source)

    result = auto_sync_wiki(cfg)

    assert result.status == "completed_with_errors"
    assert result.processed_sources == 2
    assert [failed.title for failed in result.failed_sources] == ["Alpha"]
    assert [page.name for page in result.pages] == ["Beta Topic"]
    manifest = json.loads((cfg.wiki.root_dir / "manifest.json").read_text(encoding="utf-8"))
    alpha_record, beta_record = manifest["sources"]
    state = json.loads(cfg.wiki_runtime.state_path.read_text(encoding="utf-8"))
    failed = state["failed_sources"][alpha_record["relative_path"]]
    assert failed["content_hash"] == alpha_record["content_hash"]
    assert failed["title"] == "Alpha"
    assert failed["relative_path"] == alpha_record["relative_path"]
    assert failed["error"] == "provider timed out"
    assert failed["sync_signature"] == _sync_source_signature(alpha_record)
    assert state["processed_sources"] == {
        beta_record["relative_path"]: _sync_source_signature(beta_record)
    }


def test_auto_sync_can_stop_after_current_source_and_resume_remaining(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    raw_dir = cfg.wiki_ingest.source_dirs[0]
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "alpha.md").write_text("# Alpha\n\none", encoding="utf-8")
    (raw_dir / "beta.md").write_text("# Beta\n\ntwo", encoding="utf-8")
    ingest_wiki(cfg)

    should_stop = {"value": False}
    seen_first_run: list[str] = []

    def fake_extract(cfg_arg, record, catalog):
        seen_first_run.append(str(record["title"]))
        return {"topics": [], "entities": []}

    def progress(update: dict[str, object]) -> None:
        if update.get("stage") == "source_complete":
            should_stop["value"] = True

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", fake_extract)
    stopped = auto_sync_wiki(cfg, progress=progress, should_stop=lambda: should_stop["value"])

    assert stopped.status == "stopped"
    assert stopped.processed_sources == 1
    assert seen_first_run == ["Alpha"]

    seen_resume: list[str] = []

    def fake_resume_extract(cfg_arg, record, catalog):
        seen_resume.append(str(record["title"]))
        return {"topics": [], "entities": []}

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", fake_resume_extract)
    resumed = auto_sync_wiki(cfg)

    assert resumed.status == "completed"
    assert resumed.processed_sources == 1
    assert seen_resume == ["Beta"]


def test_auto_sync_stop_refreshes_homepage_after_partial_page_updates(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    raw_dir = cfg.wiki_ingest.source_dirs[0]
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "alpha.md").write_text("# Alpha\n\none", encoding="utf-8")
    (raw_dir / "beta.md").write_text("# Beta\n\ntwo", encoding="utf-8")
    ingest_wiki(cfg)

    should_stop = {"value": False}

    def fake_extract(cfg_arg, record, catalog):
        return {
            "topics": [
                {
                    "name": "Alpha Topic",
                    "summary": "Partial sync summary",
                    "key_points": ["Partial sync point"],
                    "open_questions": [],
                }
            ],
            "entities": [],
        }

    def progress(update: dict[str, object]) -> None:
        if update.get("stage") == "source_complete":
            should_stop["value"] = True

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", fake_extract)

    stopped = auto_sync_wiki(cfg, progress=progress, should_stop=lambda: should_stop["value"])

    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")
    assert stopped.status == "stopped"
    assert "## Next Step" not in homepage
    assert "Recent Sync Changes" in homepage
    assert "Alpha Topic" in homepage
    assert "Status: stopped" in homepage


def test_auto_sync_curates_index_updates_to_avoid_page_sprawl():
    payload = {
        "topics": [
            {"name": "Notes", "summary": "generic", "key_points": ["skip"], "open_questions": []},
            {"name": "OpenClaw", "summary": "reuse", "key_points": ["one"], "open_questions": []},
            {"name": "OpenClaw", "summary": "duplicate", "key_points": ["two"], "open_questions": []},
            {"name": "Long Running Automation", "summary": "", "key_points": [], "open_questions": []},
            {
                "name": "Durable Indexing",
                "summary": "keep",
                "key_points": ["bounded topic"],
                "open_questions": [],
            },
            {
                "name": "Extra Topic",
                "summary": "over cap",
                "key_points": ["skip"],
                "open_questions": [],
            },
        ],
        "entities": [
            {"name": "Article", "summary": "generic", "key_points": ["skip"], "open_questions": []},
            {"name": "LMIT", "summary": "keep", "key_points": ["one"], "open_questions": []},
            {"name": "OpenAI", "summary": "keep", "key_points": ["two"], "open_questions": []},
            {"name": "LiteLLM", "summary": "keep", "key_points": ["three"], "open_questions": []},
            {"name": "Gemini", "summary": "over cap", "key_points": ["skip"], "open_questions": []},
        ],
    }

    curated = _curate_extracted_updates(
        payload,
        {
            "topic": ["OpenClaw"],
            "entity": [],
        },
    )

    assert [item["name"] for item in curated["topics"]] == ["OpenClaw", "Durable Indexing"]
    assert [item["name"] for item in curated["entities"]] == ["LMIT", "OpenAI", "LiteLLM"]


def test_auto_sync_records_recent_sync_changes_on_homepage(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    raw_dir = cfg.wiki_ingest.source_dirs[0]
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "alpha.md").write_text("# Alpha\n\none", encoding="utf-8")
    ingest_wiki(cfg, ingest_mode="fallback")

    def fake_extract(cfg_arg, record, catalog):
        return {
            "topics": [
                {
                    "name": "Alpha Topic",
                    "summary": "sum",
                    "key_points": [],
                    "open_questions": [],
                }
            ],
            "entities": [],
        }

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", fake_extract)
    auto_sync_wiki(cfg)

    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")
    assert "Recent Sync Changes" in homepage
    assert "Alpha Topic" in homepage
