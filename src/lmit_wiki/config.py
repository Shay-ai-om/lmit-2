from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class WikiConfig:
    root_dir: Path
    raw_dir: Path
    sources_dir: Path
    topics_dir: Path
    entities_dir: Path
    queries_dir: Path
    schema_dir: Path
    log_path: Path
    index_path: Path


@dataclass(frozen=True)
class WikiIngestConfig:
    source_dirs: tuple[Path, ...]


@dataclass(frozen=True)
class WikiRuntimeConfig:
    settings_path: Path
    state_path: Path
    auto_sync_on_ingest: bool
    search_limit: int
    serve_host: str
    serve_port: int


@dataclass(frozen=True)
class AppConfig:
    wiki: WikiConfig
    wiki_ingest: WikiIngestConfig
    wiki_runtime: WikiRuntimeConfig


def default_config(cwd: Path | None = None) -> AppConfig:
    base = (cwd or Path.cwd()).resolve()
    wiki_root = base / "knowledge_base"
    return AppConfig(
        wiki=WikiConfig(
            root_dir=wiki_root,
            raw_dir=wiki_root / "raw",
            sources_dir=wiki_root / "wiki" / "sources",
            topics_dir=wiki_root / "wiki" / "topics",
            entities_dir=wiki_root / "wiki" / "entities",
            queries_dir=wiki_root / "wiki" / "queries",
            schema_dir=wiki_root / "schema",
            log_path=wiki_root / "wiki" / "log.md",
            index_path=wiki_root / "wiki" / "index.md",
        ),
        wiki_ingest=WikiIngestConfig(source_dirs=(base / "raw",)),
        wiki_runtime=WikiRuntimeConfig(
            settings_path=wiki_root / ".wiki_runtime.json",
            state_path=wiki_root / ".wiki_state.json",
            auto_sync_on_ingest=False,
            search_limit=8,
            serve_host="127.0.0.1",
            serve_port=8765,
        ),
    )


def load_config(path: Path | None = None) -> AppConfig:
    cfg = default_config(Path.cwd())
    if path is None:
        return cfg

    config_path = path.resolve()
    base = config_path.parent
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))

    wiki_data = data.get("wiki", {})
    wiki_root = _resolve_path(wiki_data.get("root_dir"), cfg.wiki.root_dir, base)
    wiki = WikiConfig(
        root_dir=wiki_root,
        raw_dir=_resolve_path(wiki_data.get("raw_dir"), wiki_root / "raw", base),
        sources_dir=_resolve_path(
            wiki_data.get("sources_dir"), wiki_root / "wiki" / "sources", base
        ),
        topics_dir=_resolve_path(
            wiki_data.get("topics_dir"), wiki_root / "wiki" / "topics", base
        ),
        entities_dir=_resolve_path(
            wiki_data.get("entities_dir"), wiki_root / "wiki" / "entities", base
        ),
        queries_dir=_resolve_path(
            wiki_data.get("queries_dir"), wiki_root / "wiki" / "queries", base
        ),
        schema_dir=_resolve_path(wiki_data.get("schema_dir"), wiki_root / "schema", base),
        log_path=_resolve_path(wiki_data.get("log_path"), wiki_root / "wiki" / "log.md", base),
        index_path=_resolve_path(
            wiki_data.get("index_path"), wiki_root / "wiki" / "index.md", base
        ),
    )

    ingest_data = data.get("wiki_ingest", {})
    ingest = WikiIngestConfig(
        source_dirs=_resolve_paths(
            ingest_data.get("source_dirs"),
            cfg.wiki_ingest.source_dirs,
            base,
        )
    )

    runtime_data = data.get("wiki_runtime", {})
    runtime = WikiRuntimeConfig(
        settings_path=_resolve_path(
            runtime_data.get("settings_path"), wiki_root / ".wiki_runtime.json", base
        ),
        state_path=_resolve_path(
            runtime_data.get("state_path"), wiki_root / ".wiki_state.json", base
        ),
        auto_sync_on_ingest=bool(
            runtime_data.get("auto_sync_on_ingest", cfg.wiki_runtime.auto_sync_on_ingest)
        ),
        search_limit=int(runtime_data.get("search_limit", cfg.wiki_runtime.search_limit)),
        serve_host=str(runtime_data.get("serve_host", cfg.wiki_runtime.serve_host)),
        serve_port=int(runtime_data.get("serve_port", cfg.wiki_runtime.serve_port)),
    )

    return AppConfig(wiki=wiki, wiki_ingest=ingest, wiki_runtime=runtime)


def _resolve_path(value: object, default: Path, base: Path) -> Path:
    if value is None or str(value).strip() == "":
        return default
    path = Path(str(value))
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _resolve_paths(values: object, default: Iterable[Path], base: Path) -> tuple[Path, ...]:
    if values is None:
        return tuple(default)
    if not isinstance(values, list):
        raise ValueError("source_dirs must be a list")
    return tuple(_resolve_path(value, Path(str(value)), base) for value in values)
