from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import safe_write_text
from lmit_wiki.builder import append_log
from lmit_wiki.text import slugify


THEMES = {
    "Architecture": ["architecture", "workflow", "docker", "unraid", "google drive"],
    "Automation": ["automation", "telegram", "bot", "skill", "plugin", "cron"],
    "Model Usage": ["claude", "minimax", "opus", "sonnet", "llm", "model"],
    "Cost And Quota": ["cost", "quota", "token", "plan", "pricing"],
    "Security": ["security", "credential", "cookie", "session", "prompt injection", "secure"],
    "Knowledge Work": ["knowledge", "rag", "search", "wiki", "source"],
}


@dataclass(frozen=True)
class OrganizedPage:
    name: str
    kind: str
    path: Path


def organize_promoted_pages(
    cfg: AppConfig,
    *,
    kind: str = "all",
    overwrite: bool = True,
) -> list[OrganizedPage]:
    manifest_path = cfg.wiki.root_dir / "candidates.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"candidate manifest not found: {manifest_path}. Run `lmit wiki candidates` first."
        )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    organized: list[OrganizedPage] = []

    if kind in {"all", "topic", "topics"}:
        organized.extend(_organize_kind(cfg, data.get("topics", []), "topic", overwrite))
    if kind in {"all", "entity", "entities"}:
        organized.extend(_organize_kind(cfg, data.get("entities", []), "entity", overwrite))

    if organized:
        append_log(cfg, f"Organized {len(organized)} promoted wiki pages")
    return organized


def _organize_kind(
    cfg: AppConfig,
    candidates: list[dict],
    kind: str,
    overwrite: bool,
) -> list[OrganizedPage]:
    root = cfg.wiki.topics_dir if kind == "topic" else cfg.wiki.entities_dir
    organized: list[OrganizedPage] = []
    for candidate in candidates:
        name = candidate["name"]
        path = root / f"{slugify(name)}.md"
        if not path.exists():
            continue
        if not overwrite and "status: organized-draft" in path.read_text(encoding="utf-8"):
            continue
        page = render_organized_page(cfg, candidate, kind)
        safe_write_text(path, root, page)
        organized.append(OrganizedPage(name=name, kind=kind, path=path))
    return organized


def render_organized_page(cfg: AppConfig, candidate: dict, kind: str) -> str:
    name = candidate["name"]
    llm_policy = candidate.get("llm_policy", "unknown")
    sources = candidate.get("sources", [])
    evidence = [_load_evidence(cfg, source) for source in sources]
    page_dir = cfg.wiki.topics_dir if kind == "topic" else cfg.wiki.entities_dir
    visibility_counts = _count_by(evidence, "visibility")
    themes = _detect_themes(name, evidence)
    snippets = _relevant_snippets(name, candidate.get("aliases", []), evidence)

    lines = [
        "---",
        f"title: {name}",
        f"type: {kind}",
        "status: organized-draft",
        f"llm_policy: {llm_policy}",
        f"organized_at_utc: {datetime.now(timezone.utc).isoformat()}",
        "---",
        "",
        f"# {name}",
        "",
        "## Working Summary",
        "",
        _summary(name, kind, sources, visibility_counts, llm_policy),
        "",
        "## Observed Themes",
        "",
    ]

    if themes:
        for theme, count in themes:
            lines.append(f"- **{theme}**: supported by {count} source(s).")
    else:
        lines.append("- _No recurring themes detected yet._")

    lines.extend(["", "## Key Points From Sources", ""])
    if snippets:
        for snippet in snippets[:8]:
            lines.append(f"- {snippet}")
    else:
        lines.append("- _No strong source snippets detected yet._")

    lines.extend(
        [
            "",
            "## Source Coverage",
            "",
            f"- Total supporting sources: {len(sources)}",
        ]
    )
    for visibility, count in sorted(visibility_counts.items()):
        lines.append(f"- `{visibility}`: {count}")

    lines.extend(["", "## Source Evidence", ""])
    for item in evidence[:12]:
        note_link = _display_link(page_dir, item["source_note_path"])
        raw_link = _display_link(page_dir, item["raw_path"])
        lines.extend(
            [
                f"- [{item['title']}]({note_link})",
                f"  - Raw: [{Path(item['raw_path']).name}]({raw_link})",
                f"  - Visibility: `{item['visibility']}`",
            ]
        )

    lines.extend(["", "## Review Notes", ""])
    if llm_policy == "external_llm_allowed":
        lines.append("- This page can be sent to an external LLM under the current policy.")
    elif llm_policy == "mixed_review_required":
        lines.append("- Review source visibility before sending any synthesis task to an external LLM.")
    else:
        lines.append("- Keep synthesis local unless the source policy changes.")
    lines.append("- Replace this organized draft with a human-reviewed synthesis when ready.")

    lines.extend(["", "## Open Questions", "", "- Which claims should be promoted to durable conclusions?"])
    return "\n".join(lines) + "\n"


def _summary(
    name: str,
    kind: str,
    sources: list[dict],
    visibility_counts: dict[str, int],
    llm_policy: str,
) -> str:
    label = "topic" if kind == "topic" else "entity"
    visibility = ", ".join(f"{key}: {value}" for key, value in sorted(visibility_counts.items()))
    return (
        f"This {label} page organizes the current source evidence for **{name}**. "
        f"It is based on {len(sources)} source(s), with visibility coverage of {visibility or 'none'}. "
        f"The current LLM policy is `{llm_policy}`, so the page should be treated as a reviewable draft."
    )


def _load_evidence(cfg: AppConfig, source: dict) -> dict:
    raw_path = cfg.wiki.root_dir / source["raw_path"]
    text = raw_path.read_text(encoding="utf-8", errors="ignore") if raw_path.exists() else ""
    return {
        "title": source.get("title", raw_path.stem),
        "source_note_path": str(cfg.wiki.root_dir / source["source_note_path"]),
        "raw_path": str(raw_path),
        "visibility": source.get("visibility", "unknown"),
        "text": text,
    }


def _detect_themes(name: str, evidence: list[dict]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for theme, terms in THEMES.items():
        count = 0
        for item in evidence:
            haystack = (item["title"] + "\n" + item["text"]).lower()
            if any(term.lower() in haystack for term in terms):
                count += 1
        if count:
            counts[theme] = count
    return sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)


def _relevant_snippets(name: str, aliases: list[str], evidence: list[dict]) -> list[str]:
    needles = [name, *aliases]
    snippets: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        text = _clean_text(item["text"])
        for needle in needles:
            if not needle:
                continue
            snippet = _snippet_around(text, needle)
            if not snippet or snippet in seen:
                continue
            seen.add(snippet)
            snippets.append(f"{snippet} ({item['title']})")
            break
    return snippets


def _clean_text(text: str) -> str:
    skip_prefixes = (
        "# TXT Source",
        "Source file:",
        "## Original Text",
        "## Extracted URLs",
        "## URL Fetched Content",
        "### URL",
        "Source URL:",
        "Fetched URL:",
        "Final URL:",
        "---",
    )
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("http://") or stripped.startswith("https://"):
            continue
        if stripped.startswith("- http://") or stripped.startswith("- https://"):
            continue
        if any(stripped.startswith(prefix) for prefix in skip_prefixes):
            continue
        kept.append(re.sub(r"^#+\s*", "", stripped))
    cleaned = " ".join(kept)
    cleaned = re.sub(r"\bClau\s+de Code\b", "Claude Code", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bMi\s+nimax\b", "Minimax", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bOpen\s+Claw\b", "OpenClaw", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _snippet_around(text: str, needle: str) -> str | None:
    match = re.search(re.escape(needle), text, flags=re.IGNORECASE)
    if not match:
        return None
    start = max(0, match.start() - 90)
    end = min(len(text), match.end() + 170)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = item.get(key, "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _display_link(from_dir: Path, absolute_path: str) -> str:
    path = Path(absolute_path)
    return Path(os.path.relpath(path, from_dir)).as_posix()

