# High-Signal Wiki/Index Redesign

Date: 2026-05-06
Status: Draft
Scope: design only, no implementation yet

## Summary

The current LMIT-2 pipeline treats `ingest` as a deterministic file-import step and
uses `wiki/index.md` as a full catalog of imported sources plus generated pages.
That is good for traceability, but weak for an `llm-wiki` style workflow, where the
main wiki surface should stay high-signal, compact, and knowledge-oriented.

This redesign keeps the current strengths:

- stable short filenames
- raw/source note traceability
- manifest-driven sync
- local-first reproducibility

But changes the wiki surface so the primary entry point is curated knowledge, not a
flat dump of everything that was imported.

## Problem Statement

Today:

1. `ingest` does not use an LLM. It copies raw markdown into KB storage, writes
   source notes, writes `manifest.json`, and regenerates `wiki/index.md`.
2. `sync` depends on `manifest.json`, not directly on the current source folders.
3. `wiki/index.md` is rebuilt as a rules-based listing of:
   - all sources
   - all topic pages
   - all entity pages
   - all query pages

This causes three problems:

1. The main wiki homepage becomes noisy as source volume grows.
2. Human readers and LLM workflows are both encouraged to start from a low-signal
   catalog rather than a compact knowledge map.
3. The current `index` mixes two different responsibilities:
   - machine/accounting traceability
   - human/LLM knowledge navigation

## Goals

1. Make the main wiki entry point high-signal and knowledge-first.
2. Preserve full source traceability and raw-file recovery.
3. Keep `ingest` deterministic and local; do not require an LLM just to stage data.
4. Make `sync` the step that promotes raw/source-note material into durable wiki
   knowledge surfaces.
5. Reduce the amount of low-value source listing placed in the primary homepage.
6. Make the product behavior explicit when a user tries to ingest without any LLM
   configured, so fallback mode is intentional rather than accidental.

## Non-Goals

1. Replacing `manifest.json` as the canonical sync ledger.
2. Making `ingest` LLM-dependent.
3. Removing source notes or raw copies.
4. Replacing the existing topic/entity/query page layout in one step.

## Recommended Direction

Use a two-layer wiki model:

1. Data layer:
   - `manifest.json`
   - `raw/`
   - `wiki/sources/`
   - runtime state files

2. Knowledge layer:
   - `wiki/index.md` as a compact homepage
   - `wiki/topics/`
   - `wiki/entities/`
   - `wiki/queries/`
   - optional hub pages such as `wiki/hubs/`

The key change is that `wiki/index.md` stops being a full source catalog and becomes
the top-level knowledge homepage.

## Proposed Information Architecture

### 1. `wiki/index.md` becomes homepage, not full inventory

The new homepage should contain:

- short summary of KB status
- featured topics
- featured entities
- recent query pages
- recent sync summary
- links to deeper catalogs

It should not inline the full source list.

### 2. Move source inventory into a secondary system page

Introduce one secondary page such as:

- `wiki/system/sources.md`

or:

- `wiki/sources/_catalog.md`

This page can list all imported source notes and raw links for auditability. It is
still useful, but it is no longer the homepage.

### 3. Keep `manifest.json` as machine ledger

The manifest remains the canonical source list for:

- sync selection
- resume behavior
- traceability
- raw/source note lookup

It should not be treated as a user-facing navigation surface.

### 4. Add optional high-signal hub pages

Future sync or manual curation can maintain lightweight hub pages such as:

- `wiki/hubs/market-map.md`
- `wiki/hubs/people.md`
- `wiki/hubs/open-questions.md`

These become better LLM entry points than a flat source inventory.

## Pipeline Behavior After Redesign

### Ingest

`ingest` should still:

- read current raw source folders
- store KB-safe raw copies
- create/update source notes
- update `manifest.json`

But it should no longer treat the main homepage as a full source table.

Suggested output changes:

- rebuild `wiki/system/sources.md` or `wiki/sources/_catalog.md`
- lightly update `wiki/index.md` metadata or summary blocks only

### Ingest without LLM configured

This redesign should explicitly acknowledge two ingest modes:

1. preferred mode: LLM-ready wiki workflow
2. fallback mode: deterministic ingest without LLM support

Proposed behavior:

1. When the user clicks `Ingest` and no enabled LLM profile is configured, the
   system should show a warning first.
2. That warning should explain:
   - the preferred workflow is to configure an LLM before ingest/sync so the wiki
     can move toward curated, high-signal pages
   - continuing now will still import the material, but only through the
     deterministic fallback path
3. The warning should offer two clear actions:
   - configure LLM first
   - continue with fallback ingest
4. If the user chooses to continue, ingest should still run successfully using the
   non-LLM rules-based path.

In other words, deterministic ingest remains supported, but it becomes an
intentional fallback rather than the silent default experience.

### Fallback ingest output

When the user proceeds without an LLM:

1. raw copies, source notes, and `manifest.json` are still created
2. the source catalog is still updated
3. the main homepage remains compact and does not expand into a giant source dump
4. the resulting wiki should clearly indicate it is still in a pre-curation state

Suggested homepage language in fallback mode:

- "Sources imported, but no LLM curation has been configured yet."
- "Configure an LLM and run Sync to promote imported material into topic/entity pages."

This keeps fallback mode useful for staging and traceability, while avoiding the
illusion that a high-quality wiki already exists.

### Sync

`sync` remains the knowledge-promotion step:

- read `manifest.json`
- select pending sources
- update topic/entity pages
- optionally update homepage signals and hub pages

Suggested new responsibilities:

- recompute featured topics/entities
- update "recent sync changes" section on homepage
- optionally maintain curated summary pages

## Content Quality Strategy

To better match `llm-wiki` assumptions, the homepage should be intentionally sparse.

Recommended rules:

1. Homepage only shows capped counts, for example:
   - top 10 topics
   - top 10 entities
   - latest 10 query pages
2. Source inventory moves off-homepage entirely.
3. Topic/entity inclusion should prefer:
   - durable concepts
   - repeated appearances across sources
   - manually promoted or curated pages
4. Low-confidence or one-off material stays in source notes until promoted.

## Migration Plan

### Phase 1: Split homepage and source catalog

1. Add dedicated source catalog page.
2. Change `refresh_index()` so homepage no longer renders the full source list.
3. Keep all current raw/source note links reachable from the catalog page.
4. Add an ingest warning flow for the "no LLM configured" case, with explicit
   fallback confirmation.

### Phase 2: Add homepage summary blocks

1. Add counts for sources/topics/entities/queries.
2. Add recent updates section from sync/query activity.
3. Show topic/entity/query links with small caps instead of full dumps.
4. Add homepage messaging for the pre-curation / fallback-ingest state.

### Phase 3: Optional curated hubs

1. Introduce hub pages.
2. Decide whether hubs are manual-first, sync-assisted, or both.
3. Add homepage links to the most useful hubs.

## Technical Notes

Files/functions likely affected:

- `src/lmit_wiki/builder.py`
  - `refresh_index()`
  - `render_index()`
- `src/lmit_wiki/auto.py`
  - post-sync homepage updates
- `src/lmit_wiki/search.py`
  - confirm source catalog and homepage remain discoverable
- `src/lmit_wiki/server.py`
  - Web UI wording around homepage vs source catalog

Potential new files:

- `wiki/system/sources.md`
- `wiki/system/overview.md`
- `wiki/hubs/*.md`

## Risks

1. If homepage becomes too sparse, users may feel sources are "missing".
2. If we remove source links from homepage without adding a clear catalog page,
   traceability will feel worse.
3. If sync starts editing too many high-level pages at once, homepage churn may
   become noisy.
4. If fallback ingest is too easy to ignore, users may continue building large
   low-signal KBs without realizing they never moved into the LLM-curated mode.

## Acceptance Criteria

The redesign is successful when:

1. `wiki/index.md` is meaningfully shorter and more knowledge-focused than today.
2. Full source traceability still exists through source notes, raw links, and
   manifest-backed lookup.
3. Search still finds source notes, raw copies, and curated pages.
4. Users can clearly distinguish:
   - imported source material
   - curated wiki knowledge
5. The homepage is useful as the first page for both humans and LLM-assisted
   workflows.
6. If no LLM is configured, `Ingest` warns the user first and only proceeds into
   deterministic fallback mode after explicit confirmation.
7. Deterministic fallback ingest still preserves traceability, but does not cause
   the homepage to regress into a low-signal full source inventory.

## Recommendation

Implement Phase 1 first:

- keep ingest deterministic
- move full source listings off the homepage
- turn `wiki/index.md` into a compact knowledge-first surface

This gives the largest quality improvement with the smallest architectural risk.
