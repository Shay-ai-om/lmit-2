from __future__ import annotations

from lmit_wiki.builder import ingest_wiki
from lmit_wiki.builder import init_wiki, refresh_index
from lmit_wiki.config import default_config
from lmit_wiki.search import search_wiki
from lmit_wiki.state import record_recent_sync


def test_ingest_writes_compact_homepage_and_source_catalog(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "alpha.md").write_text("# Alpha\n\nAlpha body.", encoding="utf-8")
    (raw_dir / "beta.md").write_text("# Beta\n\nBeta body.", encoding="utf-8")

    cfg = default_config(tmp_path)
    result = ingest_wiki(cfg, source_dirs=[raw_dir], ingest_mode="fallback")

    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")
    catalog = result.source_catalog_path.read_text(encoding="utf-8")

    assert "## Sources" not in homepage
    assert "Source Catalog" in homepage
    assert "Sources imported, but no LLM curation has been configured yet." in homepage
    assert "[Alpha]" in catalog
    assert "[Beta]" in catalog


def test_search_finds_system_source_catalog(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "alpha.md").write_text("# Alpha\n\nSource catalog marker.", encoding="utf-8")

    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[raw_dir])

    results = search_wiki(cfg, "Source Catalog", include_raw=False)

    assert any(
        item.kind == "system" and item.rel_path == "wiki/system/sources.md"
        for item in results
    )


def test_ingest_source_note_keeps_generous_source_preview(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    late_detail = "Late source detail: durable context should still appear in the source note."
    (raw_dir / "alpha.md").write_text(
        "# Alpha\n\n" + ("opening filler " * 80) + "\n\n" + late_detail,
        encoding="utf-8",
    )

    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[raw_dir])
    [source_note_path] = list(cfg.wiki.sources_dir.glob("*.md"))

    source_note = source_note_path.read_text(encoding="utf-8")

    assert late_detail in source_note


def test_refresh_index_preserves_fallback_homepage_mode(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "alpha.md").write_text("# Alpha\n\nAlpha body.", encoding="utf-8")

    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[raw_dir], ingest_mode="fallback")

    refresh_index(cfg)
    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")

    assert "Sources imported, but no LLM curation has been configured yet." in homepage


def test_homepage_shows_recent_query_pages_newest_first(tmp_path):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    older = cfg.wiki.queries_dir / "20260506T000000Z-older.md"
    newer = cfg.wiki.queries_dir / "20260507T000000Z-newer.md"
    older.write_text("# Older Query\n", encoding="utf-8")
    newer.write_text("# Newer Query\n", encoding="utf-8")

    refresh_index(cfg)
    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")

    assert homepage.index("Newer Query") < homepage.index("Older Query")


def test_refresh_index_writes_core_hub_pages_and_links_homepage(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "alpha.md").write_text("# Alpha\n\nAlpha body.", encoding="utf-8")

    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[raw_dir], ingest_mode="fallback")

    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")
    hub_dir = cfg.wiki.root_dir / "wiki" / "hubs"

    assert (hub_dir / "knowledge-map.md").exists()
    assert (hub_dir / "recent-work.md").exists()
    assert (hub_dir / "open-questions.md").exists()
    assert "## Hub Pages" in homepage
    assert "Knowledge Map" in homepage


def test_homepage_page_sections_link_relative_to_index(tmp_path):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    (cfg.wiki.topics_dir / "alpha.md").write_text("# Alpha Topic\n", encoding="utf-8")
    (cfg.wiki.entities_dir / "acme.md").write_text("# ACME Entity\n", encoding="utf-8")
    (cfg.wiki.queries_dir / "20260507T000000Z-question.md").write_text(
        "# Question Page\n",
        encoding="utf-8",
    )

    refresh_index(cfg)
    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")

    assert "- [Knowledge Map](hubs/knowledge-map.md)" in homepage
    assert "- [Alpha Topic](topics/alpha.md)" in homepage
    assert "- [ACME Entity](entities/acme.md)" in homepage
    assert "- [Question Page](queries/20260507T000000Z-question.md)" in homepage
    assert "(wiki/hubs/knowledge-map.md)" not in homepage
    assert "(wiki/topics/alpha.md)" not in homepage
    assert "(wiki/entities/acme.md)" not in homepage
    assert "(wiki/queries/20260507T000000Z-question.md)" not in homepage


def test_homepage_recent_sync_links_relative_to_index(tmp_path):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    topic = cfg.wiki.topics_dir / "recent.md"
    topic.write_text("# Recent Topic\n", encoding="utf-8")
    record_recent_sync(
        cfg,
        status="completed",
        processed_sources=1,
        created_pages=0,
        updated_pages=1,
        pages=[
            {
                "name": "Recent Topic",
                "kind": "topic",
                "path": topic.relative_to(cfg.wiki.root_dir).as_posix(),
                "action": "updated",
            }
        ],
    )

    refresh_index(cfg)
    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")

    assert "- updated: [Recent Topic](topics/recent.md)" in homepage
    assert "(wiki/topics/recent.md)" not in homepage


def test_refresh_index_writes_topic_and_entity_catalog_pages(tmp_path):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    (cfg.wiki.topics_dir / "alpha.md").write_text("# Alpha Topic\n", encoding="utf-8")
    (cfg.wiki.topics_dir / "beta.md").write_text("# Beta Topic\n", encoding="utf-8")
    (cfg.wiki.entities_dir / "acme.md").write_text("# ACME Entity\n", encoding="utf-8")

    refresh_index(cfg)

    topic_catalog = cfg.wiki.root_dir / "wiki" / "system" / "topics.md"
    entity_catalog = cfg.wiki.root_dir / "wiki" / "system" / "entities.md"
    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")
    knowledge_map = (cfg.wiki.root_dir / "wiki" / "hubs" / "knowledge-map.md").read_text(
        encoding="utf-8"
    )

    assert topic_catalog.exists()
    assert entity_catalog.exists()
    assert "Total topics: 2" in topic_catalog.read_text(encoding="utf-8")
    assert "[Alpha Topic]" in topic_catalog.read_text(encoding="utf-8")
    assert "[Beta Topic]" in topic_catalog.read_text(encoding="utf-8")
    assert "Total entities: 1" in entity_catalog.read_text(encoding="utf-8")
    assert "[ACME Entity]" in entity_catalog.read_text(encoding="utf-8")
    assert "Topic Catalog" in homepage
    assert "Entity Catalog" in homepage
    assert "Topic Catalog" in knowledge_map
    assert "Entity Catalog" in knowledge_map


def test_featured_topics_and_entities_use_featured_and_recent_signals(tmp_path):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    for index in range(12):
        (cfg.wiki.topics_dir / f"aaa-{index:02d}.md").write_text(
            f"# Alphabetical Topic {index:02d}\n",
            encoding="utf-8",
        )
        (cfg.wiki.entities_dir / f"aaa-{index:02d}.md").write_text(
            f"# Alphabetical Entity {index:02d}\n",
            encoding="utf-8",
        )
    (cfg.wiki.topics_dir / "zzz-featured.md").write_text(
        "---\nfeatured: true\n---\n\n# Featured Topic\n",
        encoding="utf-8",
    )
    (cfg.wiki.topics_dir / "zzz-supported.md").write_text(
        "# Supported Topic\n\n"
        "## LMIT Auto Updates\n\n"
        "### 2026-05-14 | Source A | source-hash: aaaaaaaa1111\n\n"
        "### 2026-05-14 | Source B | source-hash: bbbbbbbb2222\n",
        encoding="utf-8",
    )
    recent_entity = cfg.wiki.entities_dir / "zzz-recent.md"
    recent_entity.write_text("# Recent Entity\n", encoding="utf-8")
    record_recent_sync(
        cfg,
        status="completed",
        processed_sources=1,
        created_pages=1,
        updated_pages=0,
        pages=[
            {
                "name": "Recent Entity",
                "kind": "entity",
                "path": recent_entity.relative_to(cfg.wiki.root_dir).as_posix(),
                "action": "updated",
            }
        ],
    )

    refresh_index(cfg)

    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")
    topic_section = homepage.split("## Featured Topics", 1)[1].split("## Featured Entities", 1)[0]
    entity_section = homepage.split("## Featured Entities", 1)[1].split(
        "## Recent Query Pages",
        1,
    )[0]
    assert "- [Featured Topic]" in topic_section
    assert "- [Supported Topic]" in topic_section
    assert "- [Recent Entity]" in entity_section
    assert topic_section.index("Featured Topic") < topic_section.index("Alphabetical Topic 00")
    assert topic_section.index("Supported Topic") < topic_section.index("Alphabetical Topic 00")
    assert entity_section.index("Recent Entity") < entity_section.index("Alphabetical Entity 00")


def test_open_questions_hub_collects_questions_from_topic_and_entity_pages(tmp_path):
    cfg = default_config(tmp_path)
    init_wiki(cfg)
    (cfg.wiki.topics_dir / "alpha.md").write_text(
        "# Alpha Topic\n\n## Open Questions\n\n- What changed?\n",
        encoding="utf-8",
    )
    (cfg.wiki.entities_dir / "beta.md").write_text(
        "# Beta Entity\n\n## Open Questions\n\n- Who owns this?\n",
        encoding="utf-8",
    )

    refresh_index(cfg)
    hub_text = (cfg.wiki.root_dir / "wiki" / "hubs" / "open-questions.md").read_text(
        encoding="utf-8"
    )

    assert "What changed?" in hub_text
    assert "Who owns this?" in hub_text
    assert "Alpha Topic" in hub_text
    assert "Beta Entity" in hub_text


def test_search_finds_hub_pages(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "alpha.md").write_text("# Alpha\n\nAlpha body.", encoding="utf-8")

    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[raw_dir], ingest_mode="fallback")

    results = search_wiki(cfg, "Knowledge Map", include_raw=False)

    assert any(item.kind == "hub" for item in results)
