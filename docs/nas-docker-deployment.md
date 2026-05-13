# LMIT Part 2 Legacy NAS Docker Deployment

This is now an optional legacy deployment path. The primary LMIT-2 target is the
local Windows installer documented in `docs/windows-local-install.md`.

This deployment runs the wiki-only layer on a NAS or Unraid host. The NAS stores
files, serves the lightweight web UI, and runs scheduled commands. It does not
perform model inference. Any LLM work must use an external LLM endpoint, either a
cloud API or another LAN machine with the required compute.

## Data Flow

```text
Part 1 desktop
  -> output/raw/
  -> sync to NAS share
  -> llm-wiki container reads /data/raw/* read-only
  -> llm-wiki writes /data/knowledge_base
```

Use Syncthing, rsync, Nextcloud sync, or another file sync tool to move Part 1
raw Markdown to the NAS. Keep browser sessions, cookies, login state, and URL
fetching outside this container.

## Volumes

Mount raw Markdown folders as read-only. The final path segment becomes the
stable source namespace in `knowledge_base/raw/`, so prefer durable names such as
`ai`, `articles`, and `notes`.

```yaml
volumes:
  - /mnt/user/appdata/lmit-wiki/wiki-only.toml:/config/wiki-only.toml:ro
  - /mnt/user/appdata/lmit-wiki/knowledge_base:/data/knowledge_base
  - /mnt/user/Nextcloud/LMIT/raw/ai:/data/raw/ai:ro
  - /mnt/user/Nextcloud/LMIT/raw/articles:/data/raw/articles:ro
  - /mnt/user/Nextcloud/LMIT/raw/notes:/data/raw/notes:ro
```

Only `/data/knowledge_base` should be writable by the container.

## Unraid Template Notes

- Repository/image: build from `Dockerfile` or push the built image to a
  registry and use that image name.
- Web UI port: map container `8765` to host `8765`.
- Config path: mount `/config/wiki-only.toml` read-only.
- Knowledge base path: mount `/data/knowledge_base` read/write.
- Raw source paths: mount each source root under `/data/raw/<stable-id>` as
  read-only.
- Network: bridge mode is enough for cloud APIs. Use host or a custom bridge
  only if the container must reach a LAN LLM endpoint that is otherwise blocked.

## External LLM Configuration

Store API keys in environment variables, not in `.wiki_runtime.json`.

Examples:

```text
OPENAI_API_KEY=...
GEMINI_API_KEY=...
OPENROUTER_API_KEY=...
```

For a LAN model server, configure an OpenAI-compatible profile with a private
base URL such as `http://192.168.1.10:1234/v1`, or use Ollama at
`http://192.168.1.10:11434/api`.

Source visibility is kept as metadata, not as a provider gate. LMIT-2 uses the
enabled profiles and fallback order configured in LLM Settings.

## Scheduled Commands

Run these from Unraid User Scripts or cron:

```bash
docker compose -f docker-compose.example.yml run --rm llm-wiki \
  lmit-wiki ingest --config /config/wiki-only.toml

docker compose -f docker-compose.example.yml run --rm llm-wiki \
  lmit-wiki sync --config /config/wiki-only.toml --limit 20

docker compose -f docker-compose.example.yml run --rm llm-wiki \
  lmit-wiki lint --config /config/wiki-only.toml
```

Keep the web UI on LAN or VPN. If it is exposed through a reverse proxy, require
authentication at the proxy.
