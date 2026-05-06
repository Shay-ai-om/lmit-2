# High-Signal Index Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist homepage curation state across refreshes and surface recent sync/query activity on the compact wiki homepage.

**Architecture:** Store lightweight homepage metadata in the existing runtime state file, update it from ingest/sync/query flows, and teach `render_index()` to read that state when composing homepage sections. Keep the source catalog split from phase 1 intact.

**Tech Stack:** Python 3, pytest, markdown generation, JSON runtime state.

---

## File Map

- Create: `src/lmit_wiki/state.py`
  - Shared helpers for loading/saving runtime state and homepage metadata.
- Modify: `src/lmit_wiki/builder.py`
  - Render current-mode and recent-activity sections from persisted state.
- Modify: `src/lmit_wiki/auto.py`
  - Record recent sync summary and changed pages into state before refreshing the homepage.
- Modify: `src/lmit_wiki/query.py`
  - Preserve homepage mode when query saves refresh the index.
- Create/Modify: `tests/test_wiki_builder.py`
  - Cover fallback-mode persistence and recent query ordering.
- Modify: `tests/test_wiki_auto.py`
  - Cover recent sync summary rendering on homepage.

### Task 1: Persist Homepage Mode Across Refreshes

**Files:**
- Create: `src/lmit_wiki/state.py`
- Modify: `src/lmit_wiki/builder.py`
- Modify: `tests/test_wiki_builder.py`

- [ ] **Step 1: Write the failing persistence test**

```python
def test_refresh_index_preserves_fallback_homepage_mode(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "alpha.md").write_text("# Alpha\n\nAlpha body.", encoding="utf-8")

    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[raw_dir], ingest_mode="fallback")

    refresh_index(cfg)
    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")

    assert "Sources imported, but no LLM curation has been configured yet." in homepage
```

- [ ] **Step 2: Run the persistence test to verify it fails**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_refresh_index_preserves_fallback_homepage_mode -v -p no:cacheprovider`

Expected: FAIL because a later `refresh_index(cfg)` drops back to the default non-fallback homepage copy.

- [ ] **Step 3: Add shared state helpers and use them from ingest/index rendering**

```python
def set_homepage_mode(cfg: AppConfig, mode: str) -> None:
    state = load_wiki_state(cfg)
    state["homepage"]["mode"] = mode
    save_wiki_state(cfg, state)
```

```python
if ingest_mode is not None:
    set_homepage_mode(cfg, "fallback_pending_llm" if ingest_mode == "fallback" else "sync_pending")
```

- [ ] **Step 4: Re-run the persistence test to verify it passes**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_refresh_index_preserves_fallback_homepage_mode -v -p no:cacheprovider`

Expected: PASS

### Task 2: Show Recent Query Pages in Useful Order

**Files:**
- Modify: `src/lmit_wiki/builder.py`
- Modify: `tests/test_wiki_builder.py`

- [ ] **Step 1: Write the failing recent-query ordering test**

```python
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
```

- [ ] **Step 2: Run the recent-query test to verify it fails**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_homepage_shows_recent_query_pages_newest_first -v -p no:cacheprovider`

Expected: FAIL because query entries are currently sorted alphabetically by path.

- [ ] **Step 3: Change homepage query listing to modified/newest-first**

```python
query_entries = _page_entries(cfg.wiki.queries_dir, cfg.wiki.root_dir, sort_mode="newest_first")
```

- [ ] **Step 4: Re-run the recent-query test to verify it passes**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_homepage_shows_recent_query_pages_newest_first -v -p no:cacheprovider`

Expected: PASS

### Task 3: Surface Recent Sync Summary on Homepage

**Files:**
- Modify: `src/lmit_wiki/state.py`
- Modify: `src/lmit_wiki/auto.py`
- Modify: `src/lmit_wiki/builder.py`
- Modify: `tests/test_wiki_auto.py`

- [ ] **Step 1: Write the failing recent-sync homepage test**

```python
def test_auto_sync_records_recent_sync_changes_on_homepage(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    raw_dir = cfg.wiki_ingest.source_dirs[0]
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "alpha.md").write_text("# Alpha\n\none", encoding="utf-8")
    ingest_wiki(cfg, ingest_mode="fallback")

    def fake_extract(cfg_arg, record, catalog):
        return {"topics": [{"name": "Alpha Topic", "summary": "sum", "key_points": [], "open_questions": []}], "entities": []}

    monkeypatch.setattr("lmit_wiki.auto._extract_source_updates", fake_extract)
    auto_sync_wiki(cfg)

    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")
    assert "Recent Sync Changes" in homepage
    assert "Alpha Topic" in homepage
```

- [ ] **Step 2: Run the recent-sync test to verify it fails**

Run: `python -B -m pytest tests/test_wiki_auto.py::test_auto_sync_records_recent_sync_changes_on_homepage -v -p no:cacheprovider`

Expected: FAIL because sync does not persist recent page changes for the homepage.

- [ ] **Step 3: Record sync summary before homepage refresh and render it**

```python
record_recent_sync(
    cfg,
    status=result_status,
    processed_sources=len(pending),
    created_pages=created,
    updated_pages=updated,
    pages=pages,
)
```

- [ ] **Step 4: Re-run the recent-sync test to verify it passes**

Run: `python -B -m pytest tests/test_wiki_auto.py::test_auto_sync_records_recent_sync_changes_on_homepage -v -p no:cacheprovider`

Expected: PASS

### Task 4: Verify the Phase 2 Slice

**Files:**
- Modify: `src/lmit_wiki/state.py`
- Modify: `src/lmit_wiki/builder.py`
- Modify: `src/lmit_wiki/auto.py`
- Modify: `src/lmit_wiki/query.py`
- Modify: `tests/test_wiki_builder.py`
- Modify: `tests/test_wiki_auto.py`

- [ ] **Step 1: Run focused phase-2 tests**

Run: `python -B -m pytest tests/test_wiki_builder.py tests/test_wiki_auto.py -v -p no:cacheprovider`

Expected: PASS

- [ ] **Step 2: Run the full suite**

Run: `python -B -m pytest -p no:cacheprovider`

Expected: PASS
