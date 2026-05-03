from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import ensure_within_root, safe_write_text
from lmit_wiki.builder import append_log, init_wiki, refresh_index
from lmit_wiki.policy import llm_policy_for_sources
from lmit_wiki.runtime import invoke_json_completion
from lmit_wiki.text import slugify, strip_frontmatter


@dataclass(frozen=True)
class SyncedPage:
    name: str
    kind: str
    path: Path
    action: str


@dataclass(frozen=True)
class AutoSyncResult:
    processed_sources: int
    created_pages: int
    updated_pages: int
    pages: tuple[SyncedPage, ...]


def auto_sync_wiki(cfg: AppConfig, *, limit: int | None = None) -> AutoSyncResult:
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
        if state["processed_sources"].get(str(record["relative_path"])) != str(record.get("content_hash", ""))
    ]
    if limit is not None:
        pending = pending[:limit]

    pages: list[SyncedPage] = []
    catalog = _page_catalog(cfg)
    kind_keys = {
        "topic": "topics",
        "entity": "entities",
    }
    for record in pending:
        extracted = _extract_source_updates(cfg, record, catalog)
        for kind, key in kind_keys.items():
            for item in extracted.get(key, []):
                synced = _upsert_page(cfg, record, kind, item)
                pages.append(synced)
                catalog[kind].append(item["name"])
        state["processed_sources"][str(record["relative_path"])] = str(record.get("content_hash", ""))

    _save_state(cfg, state)
    if pages:
        append_log(
            cfg,
            f"LLM auto-synced {len(pending)} source(s) into {len(pages)} topic/entity page updates",
        )
        refresh_index(cfg)

    created = sum(1 for page in pages if page.action == "created")
    updated = sum(1 for page in pages if page.action == "updated")
    return AutoSyncResult(
        processed_sources=len(pending),
        created_pages=created,
        updated_pages=updated,
        pages=tuple(pages),
    )


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
    return payload


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
    path = ensure_within_root(page_root / f"{slugify(title)}.md", page_root)
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


def _load_state(cfg: AppConfig) -> dict[str, object]:
    if not cfg.wiki_runtime.state_path.exists():
        return {
            "version": 1,
            "processed_sources": {},
        }
    return json.loads(cfg.wiki_runtime.state_path.read_text(encoding="utf-8"))


def _save_state(cfg: AppConfig, state: dict[str, object]) -> None:
    safe_write_text(
        cfg.wiki_runtime.state_path,
        cfg.wiki.root_dir,
        json.dumps(state, ensure_ascii=False, indent=2),
    )


def _relative_link(from_dir: Path, to_path: Path) -> str:
    return Path(os.path.relpath(to_path, from_dir)).as_posix()

