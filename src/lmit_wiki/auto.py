from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import ensure_within_root, safe_write_text
from lmit_wiki.builder import append_log, init_wiki, refresh_index
from lmit_wiki.policy import llm_policy_for_sources
from lmit_wiki.runtime import LLMInvocationError, invoke_json_completion
from lmit_wiki.state import load_wiki_state, record_recent_sync, save_wiki_state
from lmit_wiki.text import portable_markdown_filename, strip_frontmatter


MAX_TOPIC_UPDATES_PER_SOURCE = 2
MAX_ENTITY_UPDATES_PER_SOURCE = 3
SYNC_SOURCE_SIGNATURE_VERSION = 2
GENERIC_PAGE_NAMES = {
    "article",
    "document",
    "general",
    "misc",
    "notes",
    "page",
    "post",
    "source",
    "summary",
    "untitled",
    "update",
    "來源",
    "文章",
    "筆記",
    "摘要",
}


@dataclass(frozen=True)
class SyncedPage:
    name: str
    kind: str
    path: Path
    action: str


@dataclass(frozen=True)
class FailedSource:
    title: str
    relative_path: str
    error: str


@dataclass(frozen=True)
class AutoSyncResult:
    processed_sources: int
    created_pages: int
    updated_pages: int
    pages: tuple[SyncedPage, ...]
    status: str = "completed"
    failed_sources: tuple[FailedSource, ...] = ()


def auto_sync_wiki(
    cfg: AppConfig,
    *,
    limit: int | None = None,
    force: bool = False,
    progress: Callable[[dict[str, object]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AutoSyncResult:
    init_wiki(cfg)
    manifest_path = cfg.wiki.root_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"wiki manifest not found: {manifest_path}. Run `lmit wiki ingest` first."
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = _load_state(cfg)
    pending = [
        record
        for record in manifest.get("sources", [])
        if force
        or state["processed_sources"].get(str(record["relative_path"]))
        != _sync_source_signature(record)
    ]
    if limit is not None:
        pending = pending[:limit]
    total_sources = len(pending)
    _report_progress(
        progress,
        stage="prepare",
        total_sources=total_sources,
        processed_sources=0,
        created_pages=0,
        updated_pages=0,
        message=(
            "No sources need sync."
            if total_sources == 0
            else f"Preparing to sync {total_sources} source(s)."
        ),
    )

    pages: list[SyncedPage] = []
    failed_sources: list[FailedSource] = []
    catalog = _page_catalog(cfg)
    kind_keys = {
        "topic": "topics",
        "entity": "entities",
    }
    for index, record in enumerate(pending, start=1):
        created_so_far = sum(1 for page in pages if page.action == "created")
        updated_so_far = sum(1 for page in pages if page.action == "updated")
        _report_progress(
            progress,
            stage="source",
            total_sources=total_sources,
            source_index=index,
            processed_sources=index - 1,
            created_pages=created_so_far,
            updated_pages=updated_so_far,
            current_source_title=str(record.get("title") or "Untitled"),
            current_relative_path=str(record.get("relative_path") or ""),
            message=f"Syncing source {index} of {total_sources}: {record.get('title', 'Untitled')}",
        )
        try:
            extracted = _extract_source_updates(cfg, record, catalog)
            for kind, key in kind_keys.items():
                for item in extracted.get(key, []):
                    synced = _upsert_page(cfg, record, kind, item)
                    pages.append(synced)
                    catalog[kind].append(item["name"])
            state["processed_sources"][str(record["relative_path"])] = _sync_source_signature(record)
            _clear_failed_source(state, record)
        except LLMInvocationError as exc:
            failed = _record_failed_source(state, record, exc)
            failed_sources.append(failed)
            _save_state(cfg, state)
            _report_progress(
                progress,
                stage="source_failed",
                total_sources=total_sources,
                source_index=index,
                processed_sources=index,
                created_pages=created_so_far,
                updated_pages=updated_so_far,
                failed_source_count=len(failed_sources),
                current_source_title=failed.title,
                current_relative_path=failed.relative_path,
                last_error=failed.error,
                message=f"Skipped source {index} of {total_sources} after LLM failure: {failed.title}",
            )
            if _stop_requested(cfg, should_stop):
                return _stopped_result(
                    cfg,
                    progress=progress,
                    processed_sources=index,
                    total_sources=total_sources,
                    pages=pages,
                    failed_sources=failed_sources,
                )
            continue
        _save_state(cfg, state)
        created_so_far = sum(1 for page in pages if page.action == "created")
        updated_so_far = sum(1 for page in pages if page.action == "updated")
        _report_progress(
            progress,
            stage="source_complete",
            total_sources=total_sources,
            source_index=index,
            processed_sources=index,
            created_pages=created_so_far,
            updated_pages=updated_so_far,
            failed_source_count=len(failed_sources),
            current_source_title=str(record.get("title") or "Untitled"),
            current_relative_path=str(record.get("relative_path") or ""),
            message=f"Finished source {index} of {total_sources}: {record.get('title', 'Untitled')}",
        )
        if _stop_requested(cfg, should_stop):
            return _stopped_result(
                cfg,
                progress=progress,
                processed_sources=index,
                total_sources=total_sources,
                pages=pages,
                failed_sources=failed_sources,
            )

    clear_sync_stop_request(cfg)
    created = sum(1 for page in pages if page.action == "created")
    updated = sum(1 for page in pages if page.action == "updated")
    status = "completed_with_errors" if failed_sources else "completed"
    _record_sync_homepage_state(
        cfg,
        status=status,
        processed_sources=len(pending),
        pages=pages,
    )
    if pages or failed_sources:
        append_log(
            cfg,
            (
                f"LLM auto-synced {len(pending)} source(s) into {len(pages)} "
                f"topic/entity page updates with {len(failed_sources)} failed source(s)"
            ),
        )
    if pages or failed_sources:
        refresh_index(cfg)

    _report_progress(
        progress,
        stage=status,
        total_sources=total_sources,
        processed_sources=len(pending),
        created_pages=created,
        updated_pages=updated,
        failed_source_count=len(failed_sources),
        message=(
            f"Sync finished with {len(failed_sources)} failed source(s)."
            if failed_sources
            else (
                "Sync finished with no wiki page changes."
                if not pages
                else f"Sync finished. Created {created} page(s) and updated {updated} page(s)."
            )
        ),
    )
    return AutoSyncResult(
        processed_sources=len(pending),
        created_pages=created,
        updated_pages=updated,
        pages=tuple(pages),
        status=status,
        failed_sources=tuple(failed_sources),
    )


def request_sync_stop(cfg: AppConfig) -> str:
    marker = sync_stop_request_path(cfg)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    return "Stop requested. If a sync is currently running, it will stop after the current source finishes."


def clear_sync_stop_request(cfg: AppConfig) -> None:
    sync_stop_request_path(cfg).unlink(missing_ok=True)


def sync_stop_request_path(cfg: AppConfig) -> Path:
    return cfg.wiki_runtime.state_path.parent / ".wiki_sync_stop"


def _extract_source_updates(
    cfg: AppConfig,
    record: dict[str, object],
    catalog: dict[str, list[str]],
) -> dict[str, object]:
    raw_path = Path(str(record["raw_path"]))
    source_note_path = Path(str(record["source_note_path"]))
    raw_text = raw_path.read_text(encoding="utf-8", errors="ignore") if raw_path.exists() else ""
    source_note_text = (
        source_note_path.read_text(encoding="utf-8", errors="ignore")
        if source_note_path.exists()
        else ""
    )
    page_catalog = "\n".join(
        [
            "Existing topic pages:",
            *[f"- {name}" for name in catalog["topic"][:250]],
            "",
            "Existing entity pages:",
            *[f"- {name}" for name in catalog["entity"][:250]],
        ]
    )
    payload, _completion = invoke_json_completion(
        cfg,
        [
            {
                "role": "system",
                "content": (
                    "You maintain a persistent markdown wiki. "
                    "Given one source, decide which durable topic and entity pages should be created or updated. "
                    "Do not ask for human review. Prefer reusing existing page titles when they already fit. "
                    "Choose only durable concepts, people, organizations, products, or projects worth revisiting. "
                    "Avoid generic pages like Notes, Article, Summary, Source, or Misc. "
                    "Return at most 2 topics and at most 3 entities; return an empty list when no durable page is justified. "
                    "Return JSON only with keys source_summary, topics, entities. "
                    "Each topic/entity item must contain name, summary, key_points, open_questions."
                ),
            },
            {
                "role": "user",
                "content": "\n\n".join(
                    [
                        f"Source title: {record.get('title', 'Untitled')}",
                        f"Relative path: {record.get('relative_path', '')}",
                        f"Source excerpt: {record.get('excerpt', '')}",
                        page_catalog,
                        "Source note markdown:",
                        source_note_text[:2200],
                        "",
                        "Raw markdown excerpt:",
                        strip_frontmatter(raw_text)[:2600],
                        "",
                        (
                            "Return JSON only in this shape:\n"
                            '{"source_summary":"...","topics":[{"name":"...","summary":"...","key_points":["..."],"open_questions":["..."]}],"entities":[{"name":"...","summary":"...","key_points":["..."],"open_questions":["..."]}]}'
                        ),
                    ]
                ),
            },
        ],
        purpose="wiki auto sync",
        llm_policy=str(record.get("llm_policy") or llm_policy_for_sources([record])),
    )
    payload.setdefault("topics", [])
    payload.setdefault("entities", [])
    return _curate_extracted_updates(payload, catalog)


def _curate_extracted_updates(
    payload: dict[str, object],
    catalog: dict[str, list[str]],
) -> dict[str, object]:
    curated = dict(payload)
    curated["topics"] = _curated_items(
        payload.get("topics", []),
        existing_titles=catalog["topic"],
        max_items=MAX_TOPIC_UPDATES_PER_SOURCE,
    )
    curated["entities"] = _curated_items(
        payload.get("entities", []),
        existing_titles=catalog["entity"],
        max_items=MAX_ENTITY_UPDATES_PER_SOURCE,
    )
    return curated


def _curated_items(
    raw_items: object,
    *,
    existing_titles: list[str],
    max_items: int,
) -> list[dict[str, object]]:
    if not isinstance(raw_items, list):
        return []
    existing_by_key = {_title_key(title): title for title in existing_titles}
    seen: set[str] = set()
    curated: list[dict[str, object]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        title = _clean_page_name(raw_item.get("name"))
        if not _is_indexable_page_name(title):
            continue
        summary = str(raw_item.get("summary") or "").strip()
        key_points = _clean_string_list(raw_item.get("key_points", []))
        if not summary and not key_points:
            continue
        key = _title_key(title)
        if key in seen:
            continue
        item = dict(raw_item)
        item["name"] = existing_by_key.get(key, title)
        item["summary"] = summary
        item["key_points"] = key_points
        item["open_questions"] = _clean_string_list(raw_item.get("open_questions", []))
        curated.append(item)
        seen.add(key)
        if len(curated) >= max_items:
            break
    return curated


def _clean_page_name(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _clean_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _title_key(value: str) -> str:
    return value.casefold().strip()


def _is_indexable_page_name(title: str) -> bool:
    key = _title_key(title)
    if len(title) < 2 or len(title) > 90:
        return False
    if key in GENERIC_PAGE_NAMES:
        return False
    if title.startswith(("http://", "https://")):
        return False
    return any(ch.isalnum() for ch in title)


def _upsert_page(
    cfg: AppConfig,
    record: dict[str, object],
    kind: str,
    item: dict[str, object],
) -> SyncedPage:
    page_root = cfg.wiki.topics_dir if kind == "topic" else cfg.wiki.entities_dir
    title = str(item.get("name") or "").strip()
    if not title:
        raise ValueError(f"missing {kind} page name in LLM output")
    path = ensure_within_root(
        page_root / portable_markdown_filename(title, fallback=kind),
        page_root,
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    summary = str(item.get("summary") or "").strip()
    key_points = [str(value).strip() for value in item.get("key_points", []) if str(value).strip()]
    open_questions = [
        str(value).strip() for value in item.get("open_questions", []) if str(value).strip()
    ]
    block = _render_update_block(cfg, path, record, summary, key_points, open_questions, timestamp)

    if not path.exists():
        body = _render_new_page(
            title=title,
            kind=kind,
            summary=summary,
            key_points=key_points,
            open_questions=open_questions,
            block=block,
            timestamp=timestamp,
        )
        safe_write_text(path, page_root, body)
        return SyncedPage(name=title, kind=kind, path=path, action="created")

    text = path.read_text(encoding="utf-8", errors="ignore")
    source_marker = f"source-hash: {str(record.get('content_hash', ''))[:12]}"
    if source_marker in text:
        return SyncedPage(name=title, kind=kind, path=path, action="updated")
    updated = text.rstrip() + "\n\n" + _ensure_auto_updates_section(text) + block
    safe_write_text(path, page_root, updated.rstrip() + "\n")
    return SyncedPage(name=title, kind=kind, path=path, action="updated")


def _render_new_page(
    *,
    title: str,
    kind: str,
    summary: str,
    key_points: list[str],
    open_questions: list[str],
    block: str,
    timestamp: str,
) -> str:
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"type: {json.dumps(kind)}",
        'status: "llm-auto"',
        f"created_at_utc: {timestamp}",
        f"updated_at_utc: {timestamp}",
        "---",
        "",
        f"# {title}",
        "",
        "## Working Summary",
        "",
        summary or "_Pending source-grounded summary._",
        "",
        "## Key Points",
        "",
    ]
    if key_points:
        for point in key_points:
            lines.append(f"- {point}")
    else:
        lines.append("- _Pending source-grounded points._")
    lines.extend(
        [
            "",
            "## Open Questions",
            "",
        ]
    )
    if open_questions:
        for item in open_questions:
            lines.append(f"- {item}")
    else:
        lines.append("- _No open questions captured yet._")
    lines.extend(
        [
            "",
            "## LMIT Auto Updates",
            "",
            block.rstrip(),
            "",
        ]
    )
    return "\n".join(lines)


def _render_update_block(
    cfg: AppConfig,
    page_path: Path,
    record: dict[str, object],
    summary: str,
    key_points: list[str],
    open_questions: list[str],
    timestamp: str,
) -> str:
    source_note_path = Path(str(record["source_note_path"]))
    raw_path = Path(str(record["raw_path"]))
    note_link = _relative_link(page_path.parent, source_note_path)
    raw_link = _relative_link(page_path.parent, raw_path)
    source_hash = str(record.get("content_hash", ""))[:12]
    lines = [
        f"### {timestamp} | {record.get('title', 'Untitled')} | source-hash: {source_hash}",
        "",
        f"- Source note: [{record.get('title', 'Source')}]({note_link})",
        f"- Raw copy: [{raw_path.name}]({raw_link})",
    ]
    if summary:
        lines.append(f"- Summary: {summary}")
    if key_points:
        lines.append("- Key points:")
        for point in key_points:
            lines.append(f"  - {point}")
    if open_questions:
        lines.append("- Open questions:")
        for item in open_questions:
            lines.append(f"  - {item}")
    lines.append("")
    return "\n".join(lines)


def _ensure_auto_updates_section(text: str) -> str:
    if "## LMIT Auto Updates" in text:
        return ""
    return "## LMIT Auto Updates\n\n"


def _page_catalog(cfg: AppConfig) -> dict[str, list[str]]:
    return {
        "topic": _titles_in_dir(cfg.wiki.topics_dir),
        "entity": _titles_in_dir(cfg.wiki.entities_dir),
    }


def _titles_in_dir(root: Path) -> list[str]:
    if not root.exists():
        return []
    titles: list[str] = []
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        body = strip_frontmatter(text)
        heading = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), "")
        titles.append(heading or path.stem)
    return titles


def _record_failed_source(
    state: dict[str, object],
    record: dict[str, object],
    exc: Exception,
) -> FailedSource:
    failed_sources = state.setdefault("failed_sources", {})
    if not isinstance(failed_sources, dict):
        failed_sources = {}
        state["failed_sources"] = failed_sources
    relative_path = str(record.get("relative_path") or "")
    failed = FailedSource(
        title=str(record.get("title") or "Untitled"),
        relative_path=relative_path,
        error=str(exc),
    )
    failed_sources[relative_path] = {
        "content_hash": str(record.get("content_hash", "")),
        "sync_signature": _sync_source_signature(record),
        "title": failed.title,
        "relative_path": failed.relative_path,
        "error": failed.error,
        "failed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    return failed


def _clear_failed_source(state: dict[str, object], record: dict[str, object]) -> None:
    failed_sources = state.get("failed_sources")
    if isinstance(failed_sources, dict):
        failed_sources.pop(str(record.get("relative_path") or ""), None)


def _load_state(cfg: AppConfig) -> dict[str, object]:
    state = load_wiki_state(cfg)
    state.setdefault("processed_sources", {})
    state.setdefault("failed_sources", {})
    return state


def _save_state(cfg: AppConfig, state: dict[str, object]) -> None:
    save_wiki_state(cfg, state)


def _sync_source_signature(record: dict[str, object]) -> str:
    source_note_text = _record_path_text(record.get("source_note_path"))
    return sha256(
        "\n".join(
            [
                f"v{SYNC_SOURCE_SIGNATURE_VERSION}",
                str(record.get("content_hash", "")),
                str(record.get("storage_key", "")),
                str(record.get("excerpt", "")),
                source_note_text,
            ]
        ).encode("utf-8", errors="ignore")
    ).hexdigest()


def _record_path_text(path_value: object) -> str:
    if not path_value:
        return ""
    path = Path(str(path_value))
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _stop_requested(cfg: AppConfig, should_stop: Callable[[], bool] | None) -> bool:
    if should_stop is not None and should_stop():
        return True
    return sync_stop_request_path(cfg).exists()


def _stopped_result(
    cfg: AppConfig,
    *,
    progress: Callable[[dict[str, object]], None] | None,
    processed_sources: int,
    total_sources: int,
    pages: list[SyncedPage],
    failed_sources: list[FailedSource],
) -> AutoSyncResult:
    clear_sync_stop_request(cfg)
    created = sum(1 for page in pages if page.action == "created")
    updated = sum(1 for page in pages if page.action == "updated")
    _record_sync_homepage_state(
        cfg,
        status="stopped",
        processed_sources=processed_sources,
        pages=pages,
    )
    append_log(
        cfg,
        f"LLM auto sync stopped after processing {processed_sources} source(s)",
    )
    if pages or failed_sources:
        refresh_index(cfg)
    _report_progress(
        progress,
        stage="stopped",
        total_sources=total_sources,
        processed_sources=processed_sources,
        created_pages=created,
        updated_pages=updated,
        failed_source_count=len(failed_sources),
        message=f"Sync stopped after processing {processed_sources} source(s).",
    )
    return AutoSyncResult(
        processed_sources=processed_sources,
        created_pages=created,
        updated_pages=updated,
        pages=tuple(pages),
        status="stopped",
        failed_sources=tuple(failed_sources),
    )


def _relative_link(from_dir: Path, to_path: Path) -> str:
    return Path(os.path.relpath(to_path, from_dir)).as_posix()


def _report_progress(
    progress: Callable[[dict[str, object]], None] | None,
    **payload: object,
) -> None:
    if progress is not None:
        progress(dict(payload))


def _record_sync_homepage_state(
    cfg: AppConfig,
    *,
    status: str,
    processed_sources: int,
    pages: list[SyncedPage],
) -> None:
    created = sum(1 for page in pages if page.action == "created")
    updated = sum(1 for page in pages if page.action == "updated")
    record_recent_sync(
        cfg,
        status=status,
        processed_sources=processed_sources,
        created_pages=created,
        updated_pages=updated,
        pages=[
            {
                "name": page.name,
                "kind": page.kind,
                "path": page.path.relative_to(cfg.wiki.root_dir).as_posix(),
                "action": page.action,
            }
            for page in pages
        ],
    )

