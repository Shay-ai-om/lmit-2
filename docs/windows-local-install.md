# LMIT-2 Windows Local Install

This is the primary deployment path for LMIT-2. It runs on the local Windows
machine, reads raw Markdown produced by LMIT-1, and keeps the wiki UI on
`127.0.0.1:8765`.

## Build

Install development dependencies, PyInstaller, and Inno Setup. Then run:

```powershell
.\packaging\windows\build.ps1
```

For a PyInstaller-only build:

```powershell
.\packaging\windows\build.ps1 -SkipInstaller
```

Expected outputs:

```text
dist/lmit-wiki/lmit-wiki.exe
dist/installer/LMIT-2-Wiki-Setup.exe
```

## Installer Flow

The installer asks for:

- knowledge base folder, where LMIT-2 writes `manifest.json`, source notes, and
  wiki pages
- LMIT-1 raw Markdown folder, usually `...\LMIT\output\raw`

The installer writes the active config to:

```text
%APPDATA%\LMIT-2\wiki-only.toml
```

Generated wiki files use short ASCII storage names. Original long filenames stay
in manifest/source-note metadata and are not reused as internal KB filenames.

## Shortcuts

Start Menu shortcuts:

- `LMIT-2 Wiki Console`: starts the local web UI and opens
  `http://127.0.0.1:8765`
- `LMIT-2 Ingest Now`: imports current LMIT-1 raw Markdown
- `LMIT-2 Sync Now`: runs the LLM-driven topic/entity update flow
- `LMIT-2 Lint Now`: validates the knowledge-base structure

## Scheduled Tasks

When selected during install, these current-user tasks are created:

```text
LMIT-2 Wiki Ingest
LMIT-2 Wiki Sync
LMIT-2 Wiki Lint
```

The default intervals are:

- ingest: every 60 minutes
- sync: every 240 minutes
- lint: every 1440 minutes

Uninstall runs `remove-scheduled-tasks.ps1` to unregister those tasks.

## Manual Verification

After installing on a clean Windows machine:

```powershell
lmit-wiki.exe init --config "$env:APPDATA\LMIT-2\wiki-only.toml"
lmit-wiki.exe ingest --config "$env:APPDATA\LMIT-2\wiki-only.toml"
lmit-wiki.exe search --config "$env:APPDATA\LMIT-2\wiki-only.toml" "openclaw"
lmit-wiki.exe lint --config "$env:APPDATA\LMIT-2\wiki-only.toml"
```

Then open:

```text
http://127.0.0.1:8765
```

Confirm that long LMIT-1 filenames are visible in `manifest.json` as original
paths, while files under `knowledge_base/raw/` and `knowledge_base/wiki/` have
short portable names.
