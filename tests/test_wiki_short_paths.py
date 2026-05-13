from __future__ import annotations

import json
from pathlib import Path

from lmit_wiki.builder import ingest_wiki
from lmit_wiki.config import default_config
from lmit_wiki.query import answer_wiki_query
from lmit_wiki.runtime import LLMCompletion
from lmit_wiki.search import search_wiki
from lmit_wiki.text import MAX_PORTABLE_FILENAME_CHARS, portable_markdown_filename


def test_ingest_uses_short_internal_paths_for_long_lmit1_filenames(tmp_path):
    source_dir = tmp_path / "output" / "raw"
    deep_dir = source_dir / "nested" / "level" / "from-lmit-1"
    deep_dir.mkdir(parents=True)
    long_stem = "超長檔名" * 45
    long_file = deep_dir / f"{long_stem}.md"
    long_file.write_text("# Long CJK Title\n\nOpenClaw source body.", encoding="utf-8")

    same_a = source_dir / "a" / "same-name.md"
    same_b = source_dir / "b" / "same-name.md"
    same_a.parent.mkdir()
    same_b.parent.mkdir()
    same_a.write_text("# Duplicate A\n\nAlpha body.", encoding="utf-8")
    same_b.write_text("# Duplicate B\n\nBeta body.", encoding="utf-8")

    cfg = default_config(tmp_path)

    ingest_wiki(cfg, source_dirs=[source_dir])

    raw_files = sorted(cfg.wiki.raw_dir.rglob("*.md"))
    source_notes = sorted(cfg.wiki.sources_dir.rglob("*.md"))
    assert len(raw_files) == 3
    assert len(source_notes) == 3
    assert all(len(path.name.encode("utf-8")) <= MAX_PORTABLE_FILENAME_CHARS for path in raw_files)
    assert all(len(path.name.encode("utf-8")) <= MAX_PORTABLE_FILENAME_CHARS for path in source_notes)
    assert all(long_stem not in path.name for path in raw_files)

    manifest = json.loads((cfg.wiki.root_dir / "manifest.json").read_text(encoding="utf-8"))
    records = manifest["sources"]
    long_record = next(item for item in records if item["title"] == "Long CJK Title")
    assert long_record["original_source_path"] == str(long_file.resolve())
    assert long_record["original_relative_path"].endswith(f"{long_stem}.md")
    assert long_record["stored_raw_path"].startswith("raw/")
    assert long_record["stored_raw_path"] != long_record["original_relative_path"]
    assert Path(long_record["raw_path"]).exists()

    duplicate_raw_paths = {
        item["stored_raw_path"] for item in records if item["original_relative_path"].endswith("same-name.md")
    }
    assert len(duplicate_raw_paths) == 2


def test_short_internal_paths_preserve_search_and_query_traceability(tmp_path, monkeypatch):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    long_name = f"{'知識庫' * 30}.md"
    (source_dir / long_name).write_text(
        "# OpenClaw Deployment\n\nOpenClaw uses a local wiki console.",
        encoding="utf-8",
    )
    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[source_dir])

    results = search_wiki(cfg, "OpenClaw")
    assert results
    assert results[0].path.exists()

    seen_policies: list[str] = []

    def fake_completion(cfg_arg, messages, *, purpose, llm_policy):
        seen_policies.append(llm_policy)
        return {
            "title": "OpenClaw deployment",
            "answer_markdown": "OpenClaw uses the local wiki console. [S1]",
            "follow_up_questions": [],
        }, LLMCompletion(
            profile_id="local",
            provider="ollama",
            model="test-model",
            content="{}",
            attempts=(),
        )

    monkeypatch.setattr("lmit_wiki.query.invoke_json_completion", fake_completion)

    answer = answer_wiki_query(cfg, "How is OpenClaw deployed?", save=True)

    assert answer.saved_path is not None
    assert len(answer.saved_path.name.encode("utf-8")) <= MAX_PORTABLE_FILENAME_CHARS
    assert seen_policies == ["external_llm_allowed"]


def test_portable_markdown_filename_bounds_long_titles():
    title = "這是一個非常長的中文主題標題" * 20

    filename = portable_markdown_filename(title, fallback="topic")

    assert filename.endswith(".md")
    assert len(filename.encode("utf-8")) <= MAX_PORTABLE_FILENAME_CHARS
    assert filename.startswith("topic-")
