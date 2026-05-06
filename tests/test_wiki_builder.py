from __future__ import annotations

from lmit_wiki.builder import ingest_wiki
from lmit_wiki.builder import init_wiki, refresh_index
from lmit_wiki.config import default_config
from lmit_wiki.search import search_wiki


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
