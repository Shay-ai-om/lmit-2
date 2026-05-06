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

The installer writes a default active config to:

```text
%APPDATA%\LMIT-2\wiki-only.toml
```

The installer no longer asks for the knowledge-base folder or the LMIT-1 raw
Markdown folder.

If the config is missing when `LMIT-2 Wiki Console` starts, the launcher
asks for the knowledge-base folder and the LMIT-1 raw Markdown folder before
starting the server. The paths can still be changed from the web UI after
startup through `Knowledge Base Path` and `Raw Source Paths`.

Generated wiki files use short ASCII storage names. Original long filenames stay
in manifest/source-note metadata and are not reused as internal KB filenames.

## Shortcuts

Start Menu shortcuts:

- `LMIT-2 Wiki Console`: starts the local web UI and opens
  `http://127.0.0.1:8765`. The shortcut targets `powershell.exe`, which launches
  `{app}\scripts\start-console.ps1`.
- `LMIT-2 CLI Help`: opens the command-line help for advanced/manual use

Use the web UI for day-to-day actions:

- `Ingest`: imports current LMIT-1 raw Markdown
- `Sync`: runs the LLM-driven topic/entity update flow
- `Lint`: validates the knowledge-base structure

See [web-ui-guide.md](web-ui-guide.md) for the current Web UI operation guide.

## Scheduled Tasks

The scheduled-task option is intentionally unchecked by default. First-run users
should confirm paths, run ingest once, and configure LLM profiles before enabling
automation. When selected during install, these current-user tasks are created:

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

## API Keys

LLM Settings stores environment variable names, not secret values. The app reads
keys from normal Windows environment variables first. If a key is not found
there, the packaged app also reads a `.env` file in the install folder, for
example:

```text
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
```

The installer includes `.env.example` as a template. Copy or rename it to `.env`
and fill in only the variables you use.

## LM Studio

LMIT-2 now keeps only the native LM Studio REST profile in the default Web UI.
Use `http://localhost:1234/api/v1` when LM Studio's Local Server is running on
port 1234.

The `Model` field must use the id or key returned by the API, not necessarily
the model name shown in the download list.

```powershell
Invoke-RestMethod http://localhost:1234/api/v1/models | ConvertTo-Json -Depth 5
```

If that request is refused, start LM Studio's Local Server and confirm the port
before changing LMIT-2 settings.

## LiteLLM

LiteLLM Proxy Server uses an OpenAI-style gateway. The official quick start
shows the local proxy on:

```text
http://localhost:4000
```

Use that value for the default `LiteLLM` profile in the Web UI. If your proxy
requires a master key, set it through an env var such as `LITELLM_API_KEY` and
enter only the variable name in LMIT-2.

Local LM Studio, LiteLLM, and Ollama profiles now default to `Timeout Seconds = 300`.
If `Ask The Wiki` still fails with `timed out`, increase that value further for
the active profile before retrying.

## Manual Verification

After installing on a clean Windows machine:

```powershell
lmit-wiki.exe init --config "$env:APPDATA\LMIT-2\wiki-only.toml"
lmit-wiki.exe ingest --config "$env:APPDATA\LMIT-2\wiki-only.toml"
lmit-wiki.exe search --config "$env:APPDATA\LMIT-2\wiki-only.toml" "openclaw"
lmit-wiki.exe lint --config "$env:APPDATA\LMIT-2\wiki-only.toml"
lmit-wiki.exe stop --config "$env:APPDATA\LMIT-2\wiki-only.toml"
```

Then open:

```text
http://127.0.0.1:8765
```

If the UI still does not appear, check that port `8765` is free on the machine.
Launcher logs are written under `%APPDATA%\LMIT-2\logs`.
If `%APPDATA%\LMIT-2\wiki-only.toml` exists but is invalid, the launcher now
backs it up as `wiki-only.toml.broken-*.bak` and asks for folders again.

Confirm that long LMIT-1 filenames are visible in `manifest.json` as original
paths, while files under `knowledge_base/raw/` and `knowledge_base/wiki/` have
short portable names.
