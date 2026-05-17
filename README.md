# LMIT-2: Local Windows Wiki

LMIT-2 is the local wiki layer for Markdown exported by LMIT-1. It imports one
or more raw Markdown folders into a portable knowledge base, keeps source notes
and a manifest for traceability, provides local search, and uses configured LLM
profiles to maintain topic/entity pages or answer grounded wiki questions.

LMIT-2 does not fetch websites, log in to services, run browser automation, or
convert PDFs, Office files, images, or web pages. Those capture/conversion jobs
belong to LMIT-1. LMIT-2 starts after Markdown already exists on disk.

## Current Workflow

Most users should use the Windows Web UI:

1. Install or run `LMIT-2 Wiki Console`.
2. Set `Knowledge Base Path` and one or more `Raw Source Paths`.
3. Click `Save Paths`.
4. Click `Ingest` to import Markdown into the wiki.
5. Configure an LLM profile in `LLM Settings`.
6. Use `Search`, `Ask The Wiki`, and `Sync Now` from the Web UI.

`Ingest` can run without an enabled LLM profile. In that case it creates the raw
copies, source notes, manifest, source catalog, and fallback homepage. `Sync Now`
is the LLM-backed step that promotes imported source material into durable topic
and entity pages.

## Windows Install

The primary deployment target is a local Windows install.

- PyInstaller builds `lmit-wiki.exe`.
- Inno Setup packages it into a versioned installer:

```text
LMIT-2-Wiki-Setup-<version>.exe
```

The installer creates Start Menu entries for:

- `LMIT-2 Wiki Console`
- `LMIT-2 CLI Help`

The console binds to `127.0.0.1:8765` by default. If the active config does not
exist yet, the launcher asks for the knowledge-base folder and LMIT-1 raw
Markdown folder before starting the server. Paths can still be changed later in
the Web UI.

Optional scheduled tasks can run ingest, sync, and lint on intervals. They are
unchecked by default because first-run users usually need to verify paths and LLM
settings before enabling automation.

See [docs/windows-local-install.md](docs/windows-local-install.md) for the
installer flow and verification checklist.

## Build The Installer

From the repository root:

```powershell
.\packaging\windows\build.ps1
```

If Inno Setup is not installed yet, build only the PyInstaller executable:

```powershell
.\packaging\windows\build.ps1 -SkipInstaller
```

Expected outputs:

```text
dist/lmit-wiki/lmit-wiki.exe
dist/installer/LMIT-2-Wiki-Setup-<version>.exe
```

## Knowledge Base Layout

LMIT-2 writes a local knowledge base with stable, portable filenames:

```text
knowledge_base/
  manifest.json
  .wiki_runtime.json
  raw/
  wiki/
    index.md
    log.md
    system/sources.md
    sources/
    topics/
    entities/
    queries/
    sessions/
    hubs/
```

LMIT-1 can produce useful but very long Markdown filenames, especially when
titles include CJK text. LMIT-2 stores imported raw copies and wiki pages using
short ASCII slug/hash filenames while preserving original paths in
`manifest.json` and source-note metadata.

The homepage is intentionally compact. It links to the source catalog, hub
pages, topic/entity pages, recent query pages, and recent sync activity instead
of dumping every imported source into the first screen.

## Web UI Features

### Search

Search scans the wiki, source notes, system pages, and raw Markdown copies. The
`Search Result Limit` setting controls how many top results are returned after
searching the full index.

### Ask The Wiki

`Ask The Wiki` answers synthesis questions from the current wiki context.

- `Ask Only` appends the turn to the current session.
- `Ask And Save` appends the turn and writes a query page under `wiki/queries/`.
- `Save This Answer` can file a previously unsaved session answer later.
- Citations refer only to the current search results, not prior assistant text.

Ask sessions are persisted under `wiki/sessions/` as both canonical JSON and a
Markdown transcript. `New Session` starts a blank active session state; the first
question you ask becomes that session title. `Recent Sessions` lists past
non-empty sessions, and `Archive` hides old sessions from that list without
deleting their files.

`Compact` summarizes older turns into the session compact summary while keeping
recent turns verbatim. Auto-compact also runs before answers when a session grows
past the safe history threshold.

### Sync

`Sync Now` runs the LLM-backed auto-sync job in the background. It reads the
manifest, source notes, and raw excerpts, then creates or updates durable topic
and entity pages.

- `Stop Sync` asks the current job to stop after the active source finishes.
- `Resume Sync` continues from the next unfinished source.
- `Force Sync` reprocesses sources even if they were already marked synced.
- The optional source limit on the Auto Sync panel caps how many pending sources
  that particular run will process.

Normal sync detects more than raw file hashes. Its sync signature also includes
source-note text, manifest excerpt text, storage key, and the current raw excerpt
capacity. If you increase the raw excerpt capacity and ingest again, normal sync
will see that the prompt context changed and rerun affected sources.

## LLM Settings

Runtime settings live in:

```text
knowledge_base/.wiki_runtime.json
```

The Web UI stores API key environment variable names, not secret values. Actual
keys should be set as user/process environment variables or placed in a `.env`
file beside the packaged `lmit-wiki.exe`.

Default profile presets include:

- Ollama
- LM Studio REST
- LiteLLM
- OpenAI-compatible
- Gemini

LM Studio should use the native REST profile with:

```text
http://localhost:1234/api/v1
```

LiteLLM defaults to:

```text
http://localhost:4000
```

Local profiles such as Ollama, LM Studio REST, and LiteLLM default to longer
timeouts because local models may take time to load or begin streaming.

### Global Depth Settings

The LLM Settings panel includes three global controls with hover help:

- `Search Result Limit`: maximum number of top-ranked search results returned
  after searching the full index.
- `Ask Context Capacity`: maximum characters from each search result sent into
  Ask The Wiki. Long results prefer matched excerpts plus document openings.
- `Raw Excerpt Capacity`: maximum raw/source-note excerpt size used by ingest
  and sync. Raising it helps sync see deeper source content, at the cost of
  larger prompts.

## CLI Development

Install in editable mode:

```powershell
python -m pip install -e ".[dev]"
```

Initialize and ingest:

```powershell
lmit-wiki init --config config/wiki-only.windows.example.toml
lmit-wiki ingest --config config/wiki-only.windows.example.toml
lmit-wiki search --config config/wiki-only.windows.example.toml "openclaw"
```

Run the web UI:

```powershell
lmit-wiki serve --config config/wiki-only.windows.example.toml --host 127.0.0.1 --port 8765
```

Run tests:

```powershell
python -B -m pytest -q -p no:cacheprovider
```

The test suite provides its own `tmp_path` fixture because some Windows Python
versions create pytest temp folders with ACLs that are too restrictive in this
workspace. Set `LMIT_WIKI_TEST_TMPDIR` if you need those temp files somewhere
specific.
