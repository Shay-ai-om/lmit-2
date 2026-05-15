from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import shutil
from collections.abc import Sequence

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import ensure_within_root, safe_write_text
from lmit_wiki.models import IngestResult, SourceDocument
from lmit_wiki.policy import llm_policy_for_sources, source_visibility
from lmit_wiki.runtime import ensure_runtime_settings_file
from lmit_wiki.schema import SCHEMA_MARKDOWN
from lmit_wiki.state import homepage_state, set_homepage_mode
from lmit_wiki.text import excerpt, extract_urls, first_heading, source_note_name, strip_frontmatter


@dataclass(frozen=True)
class PageEntry:
    title: str
    rel_path: str
    path: Path
    text: str


def init_wiki(cfg: AppConfig) -> None:
    for path in [
        cfg.wiki.root_dir,
        cfg.wiki.raw_dir,
        cfg.wiki.sources_dir,
        _system_dir(cfg),
        _hubs_dir(cfg),
        cfg.wiki.topics_dir,
        cfg.wiki.entities_dir,
        cfg.wiki.queries_dir,
        cfg.wiki.schema_dir,
        cfg.wiki.index_path.parent,
        cfg.wiki.log_path.parent,
        cfg.wiki_runtime.settings_path.parent,
        cfg.wiki_runtime.state_path.parent,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    schema_path = cfg.wiki.schema_dir / "wiki.md"
    if not schema_path.exists():
        safe_write_text(schema_path, cfg.wiki.schema_dir, SCHEMA_MARKDOWN)

    if not cfg.wiki.index_path.exists():
        safe_write_text(cfg.wiki.index_path, cfg.wiki.root_dir, _empty_index())

    if not cfg.wiki.log_path.exists():
        safe_write_text(cfg.wiki.log_path, cfg.wiki.root_dir, "# Wiki Log\n\n")

    ensure_runtime_settings_file(cfg)


def ingest_wiki(
    cfg: AppConfig,
    *,
    source_dir: Path | None = None,
    source_dirs: Sequence[Path] | None = None,
    ingest_mode: str = "standard",
) -> IngestResult:
    init_wiki(cfg)
    source_roots = _source_roots(cfg, source_dir=source_dir, source_dirs=source_dirs)
    source_ids = _source_ids_for_roots(source_roots)
    for source_root in source_roots:
        if not source_root.exists() or not source_root.is_dir():
            raise FileNotFoundError(f"source markdown directory not found: {source_root}")

    docs: list[SourceDocument] = []
    copied = 0
    source_notes = 0
    for source_root, source_id in zip(source_roots, source_ids, strict=True):
        for source_path in sorted(source_root.rglob("*.md")):
            source_relative_path = source_path.resolve().relative_to(source_root)
            relative_path = Path(source_id) / source_relative_path
            text = source_path.read_text(encoding="utf-8", errors="ignore")
            content_hash = sha256(text.encode("utf-8")).hexdigest()
            storage_key = _source_storage_key(
                source_id=source_id,
                source_relative_path=source_relative_path,
                content_hash=content_hash,
            )
            stored_raw_relative_path = _stored_raw_relative_path(
                source_id=source_id,
                source_relative_path=source_relative_path,
                storage_key=storage_key,
            )
            raw_path = ensure_within_root(
                cfg.wiki.raw_dir / stored_raw_relative_path,
                cfg.wiki.raw_dir,
            )
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, raw_path)
            copied += 1

            note_name = source_note_name(storage_key)
            source_note_path = ensure_within_root(
                cfg.wiki.sources_dir / note_name,
                cfg.wiki.sources_dir,
            )
            doc = SourceDocument(
                source_path=source_path.resolve(),
                relative_path=relative_path,
                raw_path=raw_path,
                source_note_path=source_note_path,
                title=first_heading(text, relative_path.stem),
                content_hash=content_hash,
                size=len(text.encode("utf-8")),
                urls=extract_urls(text),
                excerpt=excerpt(text),
                source_id=source_id,
                storage_key=storage_key,
            )
            safe_write_text(source_note_path, cfg.wiki.sources_dir, render_source_note(doc, cfg))
            source_notes += 1
            docs.append(doc)

    roots_label = ", ".join(str(path) for path in source_roots)
    append_log(cfg, f"Ingested {len(docs)} sources from {roots_label}")
    write_manifest(cfg, docs)
    source_catalog_path = refresh_index(cfg, docs=docs, ingest_mode=ingest_mode)
    return IngestResult(
        source_count=len(docs),
        copied_raw_count=copied,
        source_note_count=source_notes,
        index_path=cfg.wiki.index_path,
        source_catalog_path=source_catalog_path,
        log_path=cfg.wiki.log_path,
        ingest_mode=ingest_mode,
    )


def lint_wiki(cfg: AppConfig) -> list[str]:
    warnings: list[str] = []
    required = [
        cfg.wiki.raw_dir,
        cfg.wiki.sources_dir,
        _hubs_dir(cfg),
        cfg.wiki.topics_dir,
        cfg.wiki.entities_dir,
        cfg.wiki.queries_dir,
        cfg.wiki.schema_dir,
        cfg.wiki.index_path,
        cfg.wiki.log_path,
    ]
    for path in required:
        if not path.exists():
            warnings.append(f"missing required path: {path}")

    source_notes = list(cfg.wiki.sources_dir.glob("*.md")) if cfg.wiki.sources_dir.exists() else []
    raw_docs = list(cfg.wiki.raw_dir.rglob("*.md")) if cfg.wiki.raw_dir.exists() else []
    if raw_docs and not source_notes:
        warnings.append("raw documents exist but no source notes were found")

    return warnings


def render_source_note(doc: SourceDocument, cfg: AppConfig) -> str:
    raw_rel = doc.raw_path.relative_to(cfg.wiki.root_dir).as_posix()
    source_note_rel = doc.source_note_path.relative_to(cfg.wiki.root_dir).as_posix()
    raw_link = _relative_link(doc.source_note_path.parent, doc.raw_path)
    urls = "\n".join(f"  - {url}" for url in doc.urls) if doc.urls else "  - none"
    return (
        "---\n"
        f"title: {json.dumps(doc.title, ensure_ascii=False)}\n"
        f"source_id: {json.dumps(doc.source_id, ensure_ascii=False)}\n"
        f"storage_key: {json.dumps(doc.storage_key, ensure_ascii=False)}\n"
        f"source_path: {json.dumps(str(doc.source_path), ensure_ascii=False)}\n"
        f"original_source_path: {json.dumps(str(doc.source_path), ensure_ascii=False)}\n"
        f"original_relative_path: {json.dumps(doc.relative_path.as_posix(), ensure_ascii=False)}\n"
        f"raw_path: {json.dumps(raw_rel, ensure_ascii=False)}\n"
        f"stored_raw_path: {json.dumps(raw_rel, ensure_ascii=False)}\n"
        f"source_note: {json.dumps(source_note_rel, ensure_ascii=False)}\n"
        f"content_hash: {doc.content_hash}\n"
        f"size_bytes: {doc.size}\n"
        "---\n\n"
        f"# {doc.title}\n\n"
        "## Source\n\n"
        f"- Original: `{doc.source_path}`\n"
        f"- Original relative path: `{doc.relative_path.as_posix()}`\n"
        f"- Raw copy: [{raw_rel}]({raw_link})\n"
        f"- Storage key: `{doc.storage_key}`\n"
        f"- Content hash: `{doc.content_hash}`\n\n"
        "## URLs\n\n"
        f"{urls}\n\n"
        "## Excerpt\n\n"
        f"{doc.excerpt}\n"
    )


def refresh_index(
    cfg: AppConfig,
    *,
    docs: list[SourceDocument] | None = None,
    ingest_mode: str | None = None,
) -> Path:
    if docs is None:
        docs = load_manifest_sources(cfg)
    if ingest_mode is not None:
        set_homepage_mode(
            cfg,
            "fallback_pending_llm" if ingest_mode == "fallback" else "sync_pending",
        )
    catalog_path = _source_catalog_path(cfg)
    topic_catalog_path = _topic_catalog_path(cfg)
    entity_catalog_path = _entity_catalog_path(cfg)
    topic_entries = _page_entries(cfg.wiki.topics_dir, cfg.wiki.root_dir)
    entity_entries = _page_entries(cfg.wiki.entities_dir, cfg.wiki.root_dir)
    safe_write_text(catalog_path, cfg.wiki.root_dir, render_source_catalog(docs, cfg, catalog_path))
    safe_write_text(
        topic_catalog_path,
        cfg.wiki.root_dir,
        render_page_catalog(
            title="Topic Catalog",
            item_label="topics",
            entries=topic_entries,
            catalog_path=topic_catalog_path,
        ),
    )
    safe_write_text(
        entity_catalog_path,
        cfg.wiki.root_dir,
        render_page_catalog(
            title="Entity Catalog",
            item_label="entities",
            entries=entity_entries,
            catalog_path=entity_catalog_path,
        ),
    )
    _write_core_hub_pages(
        cfg,
        docs=docs,
        catalog_path=catalog_path,
        topic_catalog_path=topic_catalog_path,
        entity_catalog_path=entity_catalog_path,
    )
    safe_write_text(
        cfg.wiki.index_path,
        cfg.wiki.root_dir,
        render_index(
            docs,
            cfg,
            catalog_path=catalog_path,
            topic_catalog_path=topic_catalog_path,
            entity_catalog_path=entity_catalog_path,
        ),
    )
    return catalog_path


def render_index(
    docs: list[SourceDocument],
    cfg: AppConfig,
    *,
    catalog_path: Path | None = None,
    topic_catalog_path: Path | None = None,
    entity_catalog_path: Path | None = None,
) -> str:
    topic_entries = _page_entries(cfg.wiki.topics_dir, cfg.wiki.root_dir)
    entity_entries = _page_entries(cfg.wiki.entities_dir, cfg.wiki.root_dir)
    query_entries = _page_entries(
        cfg.wiki.queries_dir,
        cfg.wiki.root_dir,
        sort_mode="newest_first",
    )
    hub_entries = _page_entries(_hubs_dir(cfg), cfg.wiki.root_dir)
    home = homepage_state(cfg)
    mode = str(home.get("mode") or "sync_pending")
    recent_sync_summary = home.get("recent_sync_summary")
    recent_sync_pages = home.get("recent_sync_pages")
    catalog_path = catalog_path or _source_catalog_path(cfg)
    topic_catalog_path = topic_catalog_path or _topic_catalog_path(cfg)
    entity_catalog_path = entity_catalog_path or _entity_catalog_path(cfg)
    catalog_rel = _relative_link(cfg.wiki.index_path.parent, catalog_path)
    topic_catalog_rel = _relative_link(cfg.wiki.index_path.parent, topic_catalog_path)
    entity_catalog_rel = _relative_link(cfg.wiki.index_path.parent, entity_catalog_path)
    featured_topic_entries = _featured_entries(topic_entries, home, limit=10)
    featured_entity_entries = _featured_entries(entity_entries, home, limit=10)
    lines = [
        "# LMIT Wiki",
        "",
        f"Generated at UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Knowledge Overview",
        "",
        f"- Sources imported: {len(docs)}",
        f"- Topic pages: {len(topic_entries)}",
        f"- Entity pages: {len(entity_entries)}",
        f"- Query pages: {len(query_entries)}",
        "",
    ]
    if mode == "fallback_pending_llm":
        lines.extend(
            [
                "## Current Mode",
                "",
                "Sources imported, but no LLM curation has been configured yet.",
                "Configure an LLM and run Sync to promote imported material into topic/entity pages.",
                "",
            ]
        )
    elif topic_entries or entity_entries:
        lines.extend(
            [
                "## Current Mode",
                "",
                "LLM curation is active. Review the featured pages and recent sync changes below.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Next Step",
                "",
                "Run Sync to promote imported material into topic/entity pages.",
                "",
            ]
        )
    lines.extend(
        [
            "## Catalogs",
            "",
            f"- [Source Catalog]({catalog_rel})",
            f"- [Topic Catalog]({topic_catalog_rel})",
            f"- [Entity Catalog]({entity_catalog_rel})",
            "",
        ]
    )
    index_dir = cfg.wiki.index_path.parent
    _append_index_section(lines, "Hub Pages", hub_entries, from_dir=index_dir, limit=10)
    _append_recent_sync_section(
        lines,
        recent_sync_summary,
        recent_sync_pages,
        from_dir=index_dir,
        wiki_root=cfg.wiki.root_dir,
    )
    _append_index_section(
        lines,
        "Featured Topics",
        featured_topic_entries,
        from_dir=index_dir,
        total_count=len(topic_entries),
    )
    _append_index_section(
        lines,
        "Featured Entities",
        featured_entity_entries,
        from_dir=index_dir,
        total_count=len(entity_entries),
    )
    _append_index_section(
        lines,
        "Recent Query Pages",
        query_entries,
        from_dir=index_dir,
        limit=10,
    )
    return "\n".join(lines)


def render_source_catalog(docs: list[SourceDocument], cfg: AppConfig, catalog_path: Path) -> str:
    lines = [
        "# Source Catalog",
        "",
        f"Generated at UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This page contains the full imported source inventory. The wiki homepage stays compact on purpose.",
        "",
        f"Total sources: {len(docs)}",
        "",
    ]
    for doc in sorted(docs, key=lambda item: item.relative_path.as_posix().lower()):
        source_rel = _relative_link(catalog_path.parent, doc.source_note_path)
        raw_rel = _relative_link(catalog_path.parent, doc.raw_path)
        lines.append(
            f"- [{doc.title}]({source_rel}) - raw: [{doc.relative_path.as_posix()}]({raw_rel})"
        )
    return "\n".join(lines)


def render_page_catalog(
    *,
    title: str,
    item_label: str,
    entries: list[PageEntry],
    catalog_path: Path,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"Generated at UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Total {item_label}: {len(entries)}",
        "",
    ]
    if not entries:
        lines.append("- _No pages yet._")
        return "\n".join(lines)
    for entry in sorted(entries, key=lambda item: (item.title.lower(), item.rel_path.lower())):
        link = _relative_link(catalog_path.parent, entry.path)
        lines.append(f"- [{entry.title}]({link})")
    return "\n".join(lines)


def render_knowledge_map_hub(
    cfg: AppConfig,
    *,
    docs: list[SourceDocument],
    topic_entries: list[PageEntry],
    entity_entries: list[PageEntry],
    query_entries: list[PageEntry],
    catalog_path: Path,
    topic_catalog_path: Path,
    entity_catalog_path: Path,
    hub_path: Path,
) -> str:
    homepage_link = _relative_link(hub_path.parent, cfg.wiki.index_path)
    catalog_link = _relative_link(hub_path.parent, catalog_path)
    topic_catalog_link = _relative_link(hub_path.parent, topic_catalog_path)
    entity_catalog_link = _relative_link(hub_path.parent, entity_catalog_path)
    home = homepage_state(cfg)
    featured_topic_entries = _featured_entries(topic_entries, home, limit=12)
    featured_entity_entries = _featured_entries(entity_entries, home, limit=12)
    lines = [
        "# Knowledge Map",
        "",
        "This hub is auto-maintained. Use it as a compact navigation layer across the current wiki.",
        "",
        "## Overview",
        "",
        f"- Homepage: [LMIT Wiki]({homepage_link})",
        f"- Source Catalog: [Imported Sources]({catalog_link})",
        f"- Topic Catalog: [All Topics]({topic_catalog_link})",
        f"- Entity Catalog: [All Entities]({entity_catalog_link})",
        f"- Total sources: {len(docs)}",
        f"- Topic pages: {len(topic_entries)}",
        f"- Entity pages: {len(entity_entries)}",
        f"- Query pages: {len(query_entries)}",
        "",
    ]
    _append_hub_link_section(lines, "Featured Topics", featured_topic_entries, hub_path, limit=12)
    _append_hub_link_section(lines, "Featured Entities", featured_entity_entries, hub_path, limit=12)
    _append_hub_link_section(lines, "Recent Query Pages", query_entries, hub_path, limit=8)
    return "\n".join(lines)


def render_recent_work_hub(
    cfg: AppConfig,
    *,
    query_entries: list[PageEntry],
    hub_path: Path,
) -> str:
    home = homepage_state(cfg)
    summary = home.get("recent_sync_summary")
    pages = home.get("recent_sync_pages")
    lines = [
        "# Recent Work",
        "",
        "This hub tracks the most recent sync activity and query pages.",
        "",
        "## Recent Sync",
        "",
    ]
    if isinstance(summary, dict):
        lines.append(
            (
                f"- Status: {summary.get('status', 'unknown')} / processed {summary.get('processed_sources', 0)} "
                f"/ created {summary.get('created_pages', 0)} / updated {summary.get('updated_pages', 0)}"
            )
        )
        updated_at = str(summary.get("updated_at_utc") or "").strip()
        if updated_at:
            lines.append(f"- Updated at UTC: {updated_at}")
    else:
        lines.append("- _No sync activity recorded yet._")
    lines.append("")
    lines.append("## Changed Pages")
    lines.append("")
    if isinstance(pages, list) and pages:
        for item in pages[:12]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "Untitled").strip() or "Untitled"
            rel_path = str(item.get("path") or "").strip()
            action = str(item.get("action") or "updated").strip()
            link = _relative_link(hub_path.parent, cfg.wiki.root_dir / rel_path) if rel_path else ""
            if link:
                lines.append(f"- {action}: [{name}]({link})")
            else:
                lines.append(f"- {action}: {name}")
    else:
        lines.append("- _No changed pages recorded yet._")
    lines.append("")
    _append_hub_link_section(lines, "Recent Query Pages", query_entries, hub_path, limit=12)
    return "\n".join(lines)


def render_open_questions_hub(
    cfg: AppConfig,
    *,
    hub_path: Path,
) -> str:
    questions = _collect_open_questions(cfg)
    lines = [
        "# Open Questions",
        "",
        "This hub rolls up open questions from topic and entity pages.",
        "",
    ]
    if not questions:
        lines.extend(
            [
                "## Open Questions",
                "",
                "- _No open questions have been captured yet._",
            ]
        )
        return "\n".join(lines)

    lines.extend(["## Open Questions", ""])
    for item in questions:
        link = _relative_link(hub_path.parent, item["path"])
        lines.append(f"- {item['question']} ({item['kind']}: [{item['title']}]({link}))")
    return "\n".join(lines)


def append_log(cfg: AppConfig, message: str) -> None:
    cfg.wiki.log_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg.wiki.log_path.exists():
        cfg.wiki.log_path.write_text("# Wiki Log\n\n", encoding="utf-8")
    timestamp = datetime.now(timezone.utc).isoformat()
    with cfg.wiki.log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"- {timestamp}: {message}\n")


def write_manifest(cfg: AppConfig, docs: list[SourceDocument]) -> None:
    manifest_path = cfg.wiki.root_dir / "manifest.json"
    payload = {
        "version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": [
            {
                **asdict(doc),
                "source_id": doc.source_id,
                "storage_key": doc.storage_key,
                "source_path": str(doc.source_path),
                "original_source_path": str(doc.source_path),
                "relative_path": doc.relative_path.as_posix(),
                "original_relative_path": doc.relative_path.as_posix(),
                "raw_path": str(doc.raw_path),
                "stored_raw_path": doc.raw_path.relative_to(cfg.wiki.root_dir).as_posix(),
                "source_note_path": str(doc.source_note_path),
                "stored_source_note_path": doc.source_note_path.relative_to(
                    cfg.wiki.root_dir
                ).as_posix(),
                "visibility": source_visibility({"urls": doc.urls}),
                "llm_policy": llm_policy_for_sources(
                    [{"visibility": source_visibility({"urls": doc.urls})}]
                ),
            }
            for doc in docs
        ],
    }
    safe_write_text(
        manifest_path,
        cfg.wiki.root_dir,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _empty_index() -> str:
    return "# LMIT Wiki Index\n\nNo sources ingested yet.\n"


def _relative_link(from_dir: Path, to_path: Path) -> str:
    return Path(os.path.relpath(to_path, from_dir)).as_posix()


def load_manifest_sources(cfg: AppConfig) -> list[SourceDocument]:
    manifest_path = cfg.wiki.root_dir / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    docs: list[SourceDocument] = []
    for record in manifest.get("sources", []):
        relative_path = Path(
            str(
                record.get("original_relative_path")
                or record.get("relative_path")
                or record.get("raw_path")
                or "source.md"
            )
        )
        raw_path = _manifest_record_path(
            record,
            absolute_key="raw_path",
            stored_key="stored_raw_path",
            root=cfg.wiki.root_dir,
            default=cfg.wiki.raw_dir / relative_path.name,
        )
        source_note_path = _manifest_record_path(
            record,
            absolute_key="source_note_path",
            stored_key="stored_source_note_path",
            root=cfg.wiki.root_dir,
            default=cfg.wiki.sources_dir / f"{relative_path.stem}.md",
        )
        source_path = Path(
            str(record.get("original_source_path") or record.get("source_path") or raw_path)
        )
        docs.append(
            SourceDocument(
                source_path=source_path,
                relative_path=relative_path,
                raw_path=raw_path,
                source_note_path=source_note_path,
                title=str(record.get("title") or relative_path.stem),
                content_hash=str(record.get("content_hash") or ""),
                size=int(record.get("size") or 0),
                urls=[str(item) for item in record.get("urls", [])],
                excerpt=str(record.get("excerpt") or ""),
                source_id=str(record.get("source_id") or relative_path.parts[0] or "raw"),
                storage_key=str(record.get("storage_key") or ""),
            )
        )
    return docs


def _page_entries(
    root: Path,
    wiki_root: Path,
    *,
    sort_mode: str = "alphabetical",
) -> list[PageEntry]:
    if not root.exists():
        return []
    entries: list[PageEntry] = []
    paths = list(root.rglob("*.md"))
    if sort_mode == "newest_first":
        paths = sorted(paths, key=lambda item: item.name.lower(), reverse=True)
    else:
        paths = sorted(paths)
    for path in paths:
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        title = first_heading(strip_frontmatter(text), path.stem)
        entries.append(
            PageEntry(
                title=title,
                rel_path=path.relative_to(wiki_root).as_posix(),
                path=path,
                text=text,
            )
        )
    return entries


def _featured_entries(
    entries: list[PageEntry],
    home: dict[str, object],
    *,
    limit: int,
) -> list[PageEntry]:
    recent_rank = _recent_sync_page_rank(home)
    return sorted(
        entries,
        key=lambda entry: _featured_sort_key(entry, recent_rank),
    )[:limit]


def _featured_sort_key(entry: PageEntry, recent_rank: dict[str, int]) -> tuple[int, int, int, int, str, str]:
    rank = recent_rank.get(entry.rel_path)
    return (
        0 if _frontmatter_featured(entry.text) else 1,
        0 if rank is not None else 1,
        rank if rank is not None else 999_999,
        -_source_hash_count(entry.text),
        entry.title.lower(),
        entry.rel_path.lower(),
    )


def _recent_sync_page_rank(home: dict[str, object]) -> dict[str, int]:
    pages = home.get("recent_sync_pages")
    if not isinstance(pages, list):
        return {}
    ranks: dict[str, int] = {}
    for index, item in enumerate(pages):
        if not isinstance(item, dict):
            continue
        rel_path = str(item.get("path") or "").strip()
        if rel_path and rel_path not in ranks:
            ranks[rel_path] = index
    return ranks


def _frontmatter_featured(text: str) -> bool:
    if not text.startswith("---"):
        return False
    match = re.match(r"\A---\s*\n(?P<body>[\s\S]*?)\n---\s*(?:\n|\Z)", text)
    if not match:
        return False
    for line in match.group("body").splitlines():
        key, sep, value = line.partition(":")
        if not sep or key.strip().lower() != "featured":
            continue
        normalized = value.strip().strip("'\"").lower()
        return normalized in {"true", "yes", "1", "on"}
    return False


def _source_hash_count(text: str) -> int:
    return len(re.findall(r"\bsource-hash\s*:", text, flags=re.IGNORECASE))


def _append_page_section(lines: list[str], entries: list[PageEntry], from_dir: Path) -> None:
    if not entries:
        lines.append("- _No pages yet._")
        lines.append("")
        return
    for entry in entries:
        link = _relative_link(from_dir, entry.path)
        lines.append(f"- [{entry.title}]({link})")
    lines.append("")


def _append_index_section(
    lines: list[str],
    title: str,
    entries: list[PageEntry],
    *,
    from_dir: Path,
    limit: int | None = None,
    total_count: int | None = None,
) -> None:
    display_entries = entries[:limit] if limit is not None else entries
    count = len(entries) if total_count is None else total_count
    lines.extend(
        [
            "",
            f"## {title}",
            "",
            f"Total {title.lower()}: {count}",
            "",
        ]
    )
    _append_page_section(lines, display_entries, from_dir)


def _append_recent_sync_section(
    lines: list[str],
    summary: object,
    pages: object,
    *,
    from_dir: Path,
    wiki_root: Path,
) -> None:
    if not isinstance(summary, dict):
        return
    lines.extend(
        [
            "## Recent Sync Changes",
            "",
            (
                f"- Status: {summary.get('status', 'unknown')} / processed {summary.get('processed_sources', 0)} "
                f"/ created {summary.get('created_pages', 0)} / updated {summary.get('updated_pages', 0)}"
            ),
        ]
    )
    updated_at = str(summary.get("updated_at_utc") or "").strip()
    if updated_at:
        lines.append(f"- Updated at UTC: {updated_at}")
    if isinstance(pages, list) and pages:
        for item in pages[:10]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "Untitled").strip() or "Untitled"
            rel_path = str(item.get("path") or "").strip()
            action = str(item.get("action") or "updated").strip()
            if rel_path:
                link = _relative_link(from_dir, wiki_root / rel_path)
                lines.append(f"- {action}: [{name}]({link})")
            else:
                lines.append(f"- {action}: {name}")
    lines.append("")


def _append_hub_link_section(
    lines: list[str],
    title: str,
    entries: list[PageEntry],
    hub_path: Path,
    *,
    limit: int,
) -> None:
    lines.extend([f"## {title}", ""])
    if not entries:
        lines.append("- _No pages yet._")
        lines.append("")
        return
    for entry in entries[:limit]:
        link = _relative_link(hub_path.parent, entry.path)
        lines.append(f"- [{entry.title}]({link})")
    lines.append("")


def _write_core_hub_pages(
    cfg: AppConfig,
    *,
    docs: list[SourceDocument],
    catalog_path: Path,
    topic_catalog_path: Path,
    entity_catalog_path: Path,
) -> None:
    hub_dir = _hubs_dir(cfg)
    topic_entries = _page_entries(cfg.wiki.topics_dir, cfg.wiki.root_dir)
    entity_entries = _page_entries(cfg.wiki.entities_dir, cfg.wiki.root_dir)
    query_entries = _page_entries(
        cfg.wiki.queries_dir,
        cfg.wiki.root_dir,
        sort_mode="newest_first",
    )
    hub_dir.mkdir(parents=True, exist_ok=True)
    safe_write_text(
        hub_dir / "knowledge-map.md",
        cfg.wiki.root_dir,
        render_knowledge_map_hub(
            cfg,
            docs=docs,
            topic_entries=topic_entries,
            entity_entries=entity_entries,
            query_entries=query_entries,
            catalog_path=catalog_path,
            topic_catalog_path=topic_catalog_path,
            entity_catalog_path=entity_catalog_path,
            hub_path=hub_dir / "knowledge-map.md",
        ),
    )
    safe_write_text(
        hub_dir / "recent-work.md",
        cfg.wiki.root_dir,
        render_recent_work_hub(
            cfg,
            query_entries=query_entries,
            hub_path=hub_dir / "recent-work.md",
        ),
    )
    safe_write_text(
        hub_dir / "open-questions.md",
        cfg.wiki.root_dir,
        render_open_questions_hub(
            cfg,
            hub_path=hub_dir / "open-questions.md",
        ),
    )


def _collect_open_questions(cfg: AppConfig) -> list[dict[str, object]]:
    questions: list[dict[str, object]] = []
    for kind, root in (("topic", cfg.wiki.topics_dir), ("entity", cfg.wiki.entities_dir)):
        for path in sorted(root.rglob("*.md")):
            if path.name.startswith("_"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            body = strip_frontmatter(text)
            title = first_heading(body, path.stem)
            for question in _section_bullets(body, "Open Questions"):
                questions.append(
                    {
                        "kind": kind,
                        "title": title,
                        "question": question,
                        "path": path,
                    }
                )
    return questions


def _section_bullets(body: str, heading: str) -> list[str]:
    lines = body.splitlines()
    capture = False
    items: list[str] = []
    target_heading = f"## {heading}".strip().lower()
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.lower() == target_heading:
            capture = True
            continue
        if capture and stripped.startswith("## "):
            break
        if capture and stripped.startswith("- "):
            value = stripped[2:].strip()
            if value:
                items.append(value)
    return items


def _system_dir(cfg: AppConfig) -> Path:
    return cfg.wiki.root_dir / "wiki" / "system"


def _hubs_dir(cfg: AppConfig) -> Path:
    return cfg.wiki.root_dir / "wiki" / "hubs"


def _source_catalog_path(cfg: AppConfig) -> Path:
    return _system_dir(cfg) / "sources.md"


def _topic_catalog_path(cfg: AppConfig) -> Path:
    return _system_dir(cfg) / "topics.md"


def _entity_catalog_path(cfg: AppConfig) -> Path:
    return _system_dir(cfg) / "entities.md"


def _source_roots(
    cfg: AppConfig,
    *,
    source_dir: Path | None,
    source_dirs: Sequence[Path] | None,
) -> tuple[Path, ...]:
    if source_dirs:
        return tuple(path.resolve() for path in source_dirs)
    if source_dir is not None:
        return (source_dir.resolve(),)
    return tuple(path.resolve() for path in cfg.wiki_ingest.source_dirs)


def _source_ids_for_roots(roots: tuple[Path, ...]) -> list[str]:
    used: dict[str, int] = {}
    source_ids: list[str] = []
    for root in roots:
        base = _safe_source_id(root.name or "raw")
        count = used.get(base, 0) + 1
        used[base] = count
        source_ids.append(base if count == 1 else f"{base}-{count}")
    return source_ids


def _safe_source_id(value: str, *, max_chars: int = 32) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-_")
    normalized = normalized[:max_chars].strip(".-_")
    return normalized or "raw"


def _source_storage_key(
    *,
    source_id: str,
    source_relative_path: Path,
    content_hash: str,
) -> str:
    raw = "\n".join([source_id, source_relative_path.as_posix(), content_hash])
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def _stored_raw_relative_path(
    *,
    source_id: str,
    source_relative_path: Path,
    storage_key: str,
) -> Path:
    slug = _safe_source_id(source_relative_path.with_suffix("").name, max_chars=48)
    return Path(source_id) / f"{storage_key}-{slug}.md"


def _manifest_record_path(
    record: dict,
    *,
    absolute_key: str,
    stored_key: str,
    root: Path,
    default: Path,
) -> Path:
    value = record.get(absolute_key)
    if value:
        path = Path(str(value))
        return path if path.is_absolute() else root / path

    stored = record.get(stored_key)
    if stored:
        path = Path(str(stored))
        return path if path.is_absolute() else root / path

    return default

