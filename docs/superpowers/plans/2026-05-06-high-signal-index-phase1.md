# High-Signal Index Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `wiki/index.md` into a compact homepage, move full source inventory to a secondary catalog page, and add an explicit no-LLM ingest warning with a deterministic fallback path.

**Architecture:** Keep ingest deterministic and manifest-driven, but separate human-facing navigation from machine/accounting output. `builder.py` will render both a homepage and a source catalog, `search.py` will load the new catalog page, and `server.py` will gate Web UI ingest behind a warning when no enabled LLM profile exists.

**Tech Stack:** Python 3, pytest, WSGI Web UI, markdown file generation, local JSON runtime settings.

---

## File Map

- Modify: `src/lmit_wiki/builder.py`
  - Render homepage and source catalog separately.
  - Return source catalog path and fallback mode metadata from ingest.
- Modify: `src/lmit_wiki/models.py`
  - Extend `IngestResult` with source catalog metadata and ingest mode.
- Modify: `src/lmit_wiki/runtime.py`
  - Add a helper that reports whether any enabled LLM profiles are configured.
- Modify: `src/lmit_wiki/search.py`
  - Include the new `wiki/system/*.md` catalog page in search.
- Modify: `src/lmit_wiki/server.py`
  - Add warning-first ingest API behavior and wire the Web UI flow to confirm fallback ingest.
- Modify: `tests/test_wiki_server.py`
  - Cover the ingest warning/confirm flow and UI text expectations.
- Create: `tests/test_wiki_builder.py`
  - Cover homepage/catalog rendering and pre-curation fallback messaging.

### Task 1: Lock Down Homepage and Catalog Behavior

**Files:**
- Create: `tests/test_wiki_builder.py`
- Modify: `src/lmit_wiki/builder.py`
- Modify: `src/lmit_wiki/models.py`

- [ ] **Step 1: Write the failing homepage/catalog tests**

```python
def test_ingest_writes_compact_homepage_and_source_catalog(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "alpha.md").write_text("# Alpha\n\nAlpha body.", encoding="utf-8")
    (raw_dir / "beta.md").write_text("# Beta\n\nBeta body.", encoding="utf-8")

    cfg = default_config(tmp_path)
    result = ingest_wiki(cfg, source_dirs=[raw_dir])

    homepage = cfg.wiki.index_path.read_text(encoding="utf-8")
    catalog = result.source_catalog_path.read_text(encoding="utf-8")

    assert "## Sources" not in homepage
    assert "Source Catalog" in homepage
    assert "Sources imported, but no LLM curation has been configured yet." in homepage
    assert "- [Alpha]" in catalog
    assert "- [Beta]" in catalog
```

- [ ] **Step 2: Run the builder test to verify it fails**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_ingest_writes_compact_homepage_and_source_catalog -v -p no:cacheprovider`

Expected: FAIL because `IngestResult` has no `source_catalog_path` and `render_index()` still emits the full `## Sources` section.

- [ ] **Step 3: Write the minimal homepage/catalog implementation**

```python
@dataclass(frozen=True)
class IngestResult:
    source_count: int
    copied_raw_count: int
    source_note_count: int
    index_path: Path
    source_catalog_path: Path
    log_path: Path
    ingest_mode: str
```

```python
def refresh_index(cfg: AppConfig, *, docs: list[SourceDocument] | None = None, ingest_mode: str = "standard") -> Path:
    if docs is None:
        docs = load_manifest_sources(cfg)
    catalog_path = write_source_catalog(cfg, docs)
    safe_write_text(cfg.wiki.index_path, cfg.wiki.root_dir, render_index(docs, cfg, catalog_path=catalog_path, ingest_mode=ingest_mode))
    return catalog_path
```

- [ ] **Step 4: Re-run the builder test to verify it passes**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_ingest_writes_compact_homepage_and_source_catalog -v -p no:cacheprovider`

Expected: PASS

### Task 2: Make the Catalog Discoverable and the Homepage High-Signal

**Files:**
- Create: `tests/test_wiki_builder.py`
- Modify: `src/lmit_wiki/search.py`
- Modify: `src/lmit_wiki/builder.py`

- [ ] **Step 1: Write the failing search/catalog test**

```python
def test_search_finds_system_source_catalog(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "alpha.md").write_text("# Alpha\n\nSource catalog marker.", encoding="utf-8")

    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[raw_dir])

    results = search_wiki(cfg, "Source Catalog", include_raw=False)

    assert any(item.kind == "system" and item.rel_path == "wiki/system/sources.md" for item in results)
```

- [ ] **Step 2: Run the search test to verify it fails**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_search_finds_system_source_catalog -v -p no:cacheprovider`

Expected: FAIL because `load_wiki_documents()` does not scan `wiki/system`.

- [ ] **Step 3: Add the system catalog loader**

```python
docs.extend(_load_dir(cfg.wiki.root_dir / "wiki" / "system", cfg.wiki.root_dir, "system"))
```

- [ ] **Step 4: Re-run the search test to verify it passes**

Run: `python -B -m pytest tests/test_wiki_builder.py::test_search_finds_system_source_catalog -v -p no:cacheprovider`

Expected: PASS

### Task 3: Gate Web UI Ingest with a No-LLM Warning and Fallback Confirm

**Files:**
- Modify: `tests/test_wiki_server.py`
- Modify: `src/lmit_wiki/server.py`
- Modify: `src/lmit_wiki/runtime.py`
- Modify: `src/lmit_wiki/models.py`

- [ ] **Step 1: Write the failing ingest warning test**

```python
def test_web_ui_ingest_requires_confirmation_when_no_llm_is_enabled(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "source.md").write_text("# Source\n\nFallback ingest.", encoding="utf-8")

    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)

    warning = _call_json(app, "POST", "/api/ingest", expect_ok=False)

    assert warning["requires_confirmation"] is True
    assert warning["ingest_mode"] == "fallback"

    confirmed = _call_json(app, "POST", "/api/ingest", {"confirm_fallback": True})

    assert confirmed["ingest_mode"] == "fallback"
    assert confirmed["source_catalog_path"].endswith("wiki/system/sources.md")
```

- [ ] **Step 2: Run the ingest warning test to verify it fails**

Run: `python -B -m pytest tests/test_wiki_server.py::test_web_ui_ingest_requires_confirmation_when_no_llm_is_enabled -v -p no:cacheprovider`

Expected: FAIL because `/api/ingest` ingests immediately and returns no confirmation payload.

- [ ] **Step 3: Add enabled-profile detection and warning-first API behavior**

```python
def has_enabled_llm_profiles(cfg: AppConfig) -> bool:
    settings = load_runtime_settings(cfg)
    return bool(settings.ordered_profiles())
```

```python
if method == "POST" and path == "/api/ingest":
    payload = self._read_json(environ)
    confirm_fallback = bool(payload.get("confirm_fallback"))
    if not has_enabled_llm_profiles(self._current_cfg()) and not confirm_fallback:
        return self._json(start_response, {...}, status=HTTPStatus.CONFLICT)
```

- [ ] **Step 4: Re-run the ingest warning test to verify it passes**

Run: `python -B -m pytest tests/test_wiki_server.py::test_web_ui_ingest_requires_confirmation_when_no_llm_is_enabled -v -p no:cacheprovider`

Expected: PASS

### Task 4: Wire the Web UI to the New Ingest Flow

**Files:**
- Modify: `tests/test_wiki_server.py`
- Modify: `src/lmit_wiki/server.py`

- [ ] **Step 1: Write the failing UI text/flow assertions**

```python
def test_web_ui_mentions_ingest_warning_and_source_catalog(tmp_path):
    cfg = default_config(tmp_path)
    app = WikiWebApp(cfg)

    html = _call_html(app, "GET", "/")

    assert "configure an LLM first" in html
    assert "continue with fallback ingest" in html
    assert "Source Catalog" in html
```

- [ ] **Step 2: Run the UI test to verify it fails**

Run: `python -B -m pytest tests/test_wiki_server.py::test_web_ui_mentions_ingest_warning_and_source_catalog -v -p no:cacheprovider`

Expected: FAIL because the current UI has no fallback warning text or catalog messaging.

- [ ] **Step 3: Update the inline Web UI script and copy**

```javascript
if (data.requires_confirmation) {
  const proceed = window.confirm(data.warning || "No LLM profile is enabled. Continue with fallback ingest?");
  if (!proceed) {
    status("ingestStatus", "Ingest cancelled so you can configure an LLM first.");
    return;
  }
  return runIngest({ confirmFallback: true });
}
```

- [ ] **Step 4: Re-run the UI test to verify it passes**

Run: `python -B -m pytest tests/test_wiki_server.py::test_web_ui_mentions_ingest_warning_and_source_catalog -v -p no:cacheprovider`

Expected: PASS

### Task 5: Verify the Whole Slice

**Files:**
- Modify: `tests/test_wiki_builder.py`
- Modify: `tests/test_wiki_server.py`
- Modify: `src/lmit_wiki/builder.py`
- Modify: `src/lmit_wiki/server.py`
- Modify: `src/lmit_wiki/runtime.py`
- Modify: `src/lmit_wiki/search.py`
- Modify: `src/lmit_wiki/models.py`

- [ ] **Step 1: Run the focused redesign tests**

Run: `python -B -m pytest tests/test_wiki_builder.py tests/test_wiki_server.py -v -p no:cacheprovider`

Expected: PASS

- [ ] **Step 2: Run the full suite**

Run: `python -B -m pytest -p no:cacheprovider`

Expected: PASS

- [ ] **Step 3: Commit the phase 1 slice**

```bash
git add docs/superpowers/plans/2026-05-06-high-signal-index-phase1.md \
  src/lmit_wiki/builder.py src/lmit_wiki/models.py src/lmit_wiki/runtime.py \
  src/lmit_wiki/search.py src/lmit_wiki/server.py tests/test_wiki_builder.py tests/test_wiki_server.py
git commit -m "feat: split wiki homepage from source catalog"
```
