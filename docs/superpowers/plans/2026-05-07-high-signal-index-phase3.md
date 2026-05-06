# High-Signal Index Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce hub pages as higher-signal navigation surfaces, auto-maintain a small core set of hubs, and link them from the compact homepage.

**Architecture:** Extend the deterministic wiki build layer to generate a core set of auto-maintained hubs under `wiki/hubs/` from existing topics, entities, queries, source catalog, and homepage state. Keep the homepage compact, but give it a dedicated `Hub Pages` section and make hubs searchable like other wiki content.

**Tech Stack:** Python 3, pytest, markdown generation, existing wiki state/homepage metadata.

---

## File Map

- Modify: `src/lmit_wiki/builder.py`
  - Generate core hub pages and list them on the homepage.
- Modify: `src/lmit_wiki/search.py`
  - Include hub pages in search results.
- Modify: `src/lmit_wiki/server.py`
  - Update manual copy for hub pages.
- Modify: `docs/web-ui-guide.md`
  - Document hub pages and how they behave.
- Modify: `docs/windows-local-install.md`
  - Mention auto-maintained hubs in the local install flow.
- Modify: `tests/test_wiki_builder.py`
  - Cover hub page generation, homepage links, open-question aggregation, and search discoverability.

### Task 1: Generate Core Hub Pages and Link Them from the Homepage

**Files:**
- Modify: `tests/test_wiki_builder.py`
- Modify: `src/lmit_wiki/builder.py`

- [ ] **Step 1: Write the failing hub-generation test**

```python
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
```

- [ ] **Step 2: Run the hub-generation test to verify it fails**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_refresh_index_writes_core_hub_pages_and_links_homepage -v -p no:cacheprovider`

Expected: FAIL because `refresh_index()` does not yet create `wiki/hubs/*.md` or render a `Hub Pages` section.

- [ ] **Step 3: Add core hub generation to the build flow**

```python
write_core_hub_pages(
    cfg,
    topic_entries=topic_entries,
    entity_entries=entity_entries,
    query_entries=query_entries,
    catalog_path=catalog_path,
)
```

- [ ] **Step 4: Re-run the hub-generation test to verify it passes**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_refresh_index_writes_core_hub_pages_and_links_homepage -v -p no:cacheprovider`

Expected: PASS

### Task 2: Aggregate Open Questions into a Dedicated Hub

**Files:**
- Modify: `tests/test_wiki_builder.py`
- Modify: `src/lmit_wiki/builder.py`

- [ ] **Step 1: Write the failing open-questions hub test**

```python
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
    hub_text = (cfg.wiki.root_dir / "wiki" / "hubs" / "open-questions.md").read_text(encoding="utf-8")

    assert "What changed?" in hub_text
    assert "Who owns this?" in hub_text
    assert "Alpha Topic" in hub_text
    assert "Beta Entity" in hub_text
```

- [ ] **Step 2: Run the open-questions test to verify it fails**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_open_questions_hub_collects_questions_from_topic_and_entity_pages -v -p no:cacheprovider`

Expected: FAIL because no hub aggregates `## Open Questions` sections yet.

- [ ] **Step 3: Implement open-question extraction and hub rendering**

```python
questions = _collect_open_questions(cfg)
safe_write_text(open_questions_path, cfg.wiki.root_dir, render_open_questions_hub(...))
```

- [ ] **Step 4: Re-run the open-questions test to verify it passes**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_open_questions_hub_collects_questions_from_topic_and_entity_pages -v -p no:cacheprovider`

Expected: PASS

### Task 3: Make Hub Pages Searchable

**Files:**
- Modify: `tests/test_wiki_builder.py`
- Modify: `src/lmit_wiki/search.py`

- [ ] **Step 1: Write the failing hub search test**

```python
def test_search_finds_hub_pages(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "alpha.md").write_text("# Alpha\n\nAlpha body.", encoding="utf-8")

    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[raw_dir], ingest_mode="fallback")

    results = search_wiki(cfg, "Knowledge Map", include_raw=False)

    assert any(item.kind == "hub" for item in results)
```

- [ ] **Step 2: Run the hub search test to verify it fails**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_search_finds_hub_pages -v -p no:cacheprovider`

Expected: FAIL because `load_wiki_documents()` does not load `wiki/hubs`.

- [ ] **Step 3: Add hub scanning to search**

```python
docs.extend(_load_dir(cfg.wiki.root_dir / "wiki" / "hubs", cfg.wiki.root_dir, "hub"))
```

- [ ] **Step 4: Re-run the hub search test to verify it passes**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_search_finds_hub_pages -v -p no:cacheprovider`

Expected: PASS

### Task 4: Verify the Phase 3 Slice

**Files:**
- Modify: `src/lmit_wiki/builder.py`
- Modify: `src/lmit_wiki/search.py`
- Modify: `src/lmit_wiki/server.py`
- Modify: `docs/web-ui-guide.md`
- Modify: `docs/windows-local-install.md`
- Modify: `tests/test_wiki_builder.py`

- [ ] **Step 1: Run focused hub tests**

Run: `python -B -m pytest tests/test_wiki_builder.py -v -p no:cacheprovider`

Expected: PASS

- [ ] **Step 2: Run the full suite**

Run: `python -B -m pytest -p no:cacheprovider`

Expected: PASS
