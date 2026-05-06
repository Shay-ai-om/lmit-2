from __future__ import annotations

import json

import pytest

from lmit_wiki.auto import auto_sync_wiki
from lmit_wiki.builder import ingest_wiki
from lmit_wiki.config import default_config


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
        first_record["relative_path"]: first_record["content_hash"]
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
