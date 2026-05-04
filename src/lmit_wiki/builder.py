from __future__ import annotations

from dataclasses import asdict
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
from lmit_wiki.text import excerpt, extract_urls, first_heading, source_note_name, strip_frontmatter


def init_wiki(cfg: AppConfig) -> None:
    for path in [
        cfg.wiki.root_dir,
        cfg.wiki.raw_dir,
        cfg.wiki.sources_dir,
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
    refresh_index(cfg, docs=docs)
    return IngestResult(
        source_count=len(docs),
        copied_raw_count=copied,
        source_note_count=source_notes,
        index_path=cfg.wiki.index_path,
        log_path=cfg.wiki.log_path,
    )


def lint_wiki(cfg: AppConfig) -> list[str]:
    warnings: list[str] = []
    required = [
        cfg.wiki.raw_dir,
        cfg.wiki.sources_dir,
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


def refresh_index(cfg: AppConfig, *, docs: list[SourceDocument] | None = None) -> None:
    if docs is None:
        docs = load_manifest_sources(cfg)
    safe_write_text(cfg.wiki.index_path, cfg.wiki.root_dir, render_index(docs, cfg))


def render_index(docs: list[SourceDocument], cfg: AppConfig) -> str:
    topic_entries = _page_entries(cfg.wiki.topics_dir, cfg.wiki.root_dir)
    entity_entries = _page_entries(cfg.wiki.entities_dir, cfg.wiki.root_dir)
    query_entries = _page_entries(cfg.wiki.queries_dir, cfg.wiki.root_dir)
    lines = [
        "# LMIT Wiki Index",
        "",
        f"Generated at UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Sources",
        "",
        f"Total sources: {len(docs)}",
        "",
    ]
    for doc in sorted(docs, key=lambda item: item.relative_path.as_posix().lower()):
        source_rel = doc.source_note_path.relative_to(cfg.wiki.root_dir).as_posix()
        raw_rel = doc.raw_path.relative_to(cfg.wiki.root_dir).as_posix()
        lines.append(
            f"- [{doc.title}]({source_rel}) - raw: [{doc.relative_path.as_posix()}]({raw_rel})"
        )
    _append_index_section(lines, "Topic Pages", topic_entries)
    _append_index_section(lines, "Entity Pages", entity_entries)
    _append_index_section(lines, "Query Pages", query_entries)
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


def _page_entries(root: Path, wiki_root: Path) -> list[tuple[str, str]]:
    if not root.exists():
        return []
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        title = first_heading(strip_frontmatter(text), path.stem)
        entries.append((title, path.relative_to(wiki_root).as_posix()))
    return entries


def _append_page_section(lines: list[str], entries: list[tuple[str, str]]) -> None:
    if not entries:
        lines.append("- _No pages yet._")
        lines.append("")
        return
    for title, rel_path in entries:
        lines.append(f"- [{title}]({rel_path})")
    lines.append("")


def _append_index_section(
    lines: list[str],
    title: str,
    entries: list[tuple[str, str]],
) -> None:
    lines.extend(
        [
            "",
            f"## {title}",
            "",
            f"Total {title.lower()}: {len(entries)}",
            "",
        ]
    )
    _append_page_section(lines, entries)


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

