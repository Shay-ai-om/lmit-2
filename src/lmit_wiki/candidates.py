from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import json
import os
import re

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import ensure_within_root, safe_write_text
from lmit_wiki.builder import append_log, init_wiki
from lmit_wiki.text import portable_markdown_filename


RESTRICTED_DOMAINS = {
    "facebook.com",
    "m.facebook.com",
    "mbasic.facebook.com",
    "www.facebook.com",
    "chatgpt.com",
}

TOPIC_TERMS = {
    "AI Agent": ["ai agent", "agent browser"],
    "OpenClaw": ["openclaw"],
    "Claude Code": ["claude code"],
    "Claude Proxy": ["claude proxy", "claude --print"],
    "MiniMax Coding Plan": ["minimax coding plan", "minimax"],
    "Nextcloud": ["nextcloud"],
    "Knowledge Base": ["knowledge base"],
    "RAG": ["rag", "vector search"],
    "Docker": ["docker", "unraid"],
    "Playwright Session": ["playwright", "session", "cookie"],
    "MarkItDown": ["markitdown"],
    "Telegram Bot": ["telegram"],
    "Google Drive Workflow": ["google drive"],
    "SearXNG": ["searxng"],
    "SecureClaw": ["secureclaw"],
    "Cloudflare Verification": ["cloudflare"],
    "OCR": ["ocr"],
    "FFmpeg": ["ffmpeg"],
}

ENTITY_TERMS = {
    "OpenClaw": ["openclaw"],
    "Claude Code": ["claude code"],
    "Claude Opus": ["claude opus", "opus"],
    "Claude Pro": ["claude pro"],
    "MiniMax": ["minimax"],
    "NotebookLM": ["notebooklm"],
    "MarkItDown": ["markitdown"],
    "Playwright": ["playwright"],
    "Nextcloud": ["nextcloud"],
    "Unraid": ["unraid"],
    "Docker": ["docker"],
    "SearXNG": ["searxng"],
    "SecureClaw": ["secureclaw"],
    "LanceDB": ["lancedb"],
    "SQLite": ["sqlite"],
    "npm": ["npm", "npmjs"],
    "Cloudflare": ["cloudflare"],
    "GitHub": ["github"],
    "Telegram": ["telegram"],
    "Google Drive": ["google drive"],
    "Facebook": ["facebook"],
}


@dataclass
class CandidateEvidence:
    title: str
    source_note_path: Path
    raw_path: Path
    visibility: str
    excerpt: str


@dataclass
class Candidate:
    name: str
    kind: str
    aliases: set[str] = field(default_factory=set)
    score: int = 0
    evidence: list[CandidateEvidence] = field(default_factory=list)

    @property
    def source_count(self) -> int:
        return len({item.source_note_path for item in self.evidence})

    @property
    def llm_policy(self) -> str:
        visibilities = {item.visibility for item in self.evidence}
        if visibilities == {"public_web"}:
            return "external_llm_allowed"
        if "public_web" in visibilities:
            return "mixed_review_required"
        return "local_only"


def generate_candidates(cfg: AppConfig) -> tuple[list[Candidate], list[Candidate]]:
    init_wiki(cfg)
    manifest_path = cfg.wiki.root_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"wiki manifest not found: {manifest_path}. Run `lmit wiki ingest` first."
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("sources", [])
    topics = _collect_candidates(records, TOPIC_TERMS, "topic")
    entities = _collect_candidates(records, ENTITY_TERMS, "entity")

    topic_path = ensure_within_root(cfg.wiki.topics_dir / "_candidates.md", cfg.wiki.topics_dir)
    entity_path = ensure_within_root(
        cfg.wiki.entities_dir / "_candidates.md", cfg.wiki.entities_dir
    )
    safe_write_text(topic_path, cfg.wiki.topics_dir, render_candidates(topics, cfg, "topic"))
    safe_write_text(entity_path, cfg.wiki.entities_dir, render_candidates(entities, cfg, "entity"))
    _write_candidates_manifest(cfg, topics, entities)
    append_log(cfg, f"Generated {len(topics)} topic candidates and {len(entities)} entity candidates")
    return topics, entities


def render_candidates(candidates: list[Candidate], cfg: AppConfig, kind: str) -> str:
    title = "Topic Candidates" if kind == "topic" else "Entity Candidates"
    lines = [
        f"# {title}",
        "",
        f"Generated at UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Review these candidates before promoting them to durable wiki pages.",
        "",
        "Legend:",
        "",
        "- `external_llm_allowed`: all supporting sources look like public web content.",
        "- `mixed_review_required`: public and private/restricted sources are mixed.",
        "- `local_only`: supporting sources include login-gated or local/private material.",
        "",
    ]

    if not candidates:
        lines.append("_No candidates found._")
        return "\n".join(lines) + "\n"

    for candidate in candidates:
        page_dir = cfg.wiki.topics_dir if kind == "topic" else cfg.wiki.entities_dir
        suggested_path = page_dir / portable_markdown_filename(
            candidate.name,
            fallback=kind,
        )
        suggested_rel = suggested_path.relative_to(cfg.wiki.root_dir).as_posix()
        lines.extend(
            [
                f"## {candidate.name}",
                "",
                "- [ ] Accept",
                f"- Suggested path: `{suggested_rel}`",
                f"- Score: {candidate.score}",
                f"- Source count: {candidate.source_count}",
                f"- LLM policy: `{candidate.llm_policy}`",
            ]
        )
        aliases = sorted(alias for alias in candidate.aliases if alias.lower() != candidate.name.lower())
        if aliases:
            lines.append(f"- Aliases: {', '.join(aliases)}")
        lines.extend(["", "### Evidence", ""])
        for item in candidate.evidence[:8]:
            note_rel = _relative_link(page_dir, item.source_note_path)
            raw_rel = _relative_link(page_dir, item.raw_path)
            lines.extend(
                [
                    f"- [{item.title}]({note_rel})",
                    f"  - Raw: [{item.raw_path.name}]({raw_rel})",
                    f"  - Visibility: `{item.visibility}`",
                    f"  - Excerpt: {item.excerpt}",
                ]
            )
        lines.append("")

    return "\n".join(lines)


def _collect_candidates(
    records: list[dict],
    terms: dict[str, list[str]],
    kind: str,
) -> list[Candidate]:
    candidates: dict[str, Candidate] = {}
    for record in records:
        haystack = _record_haystack(record)
        haystack_lower = haystack.lower()
        visibility = _source_visibility(record)
        for name, aliases in terms.items():
            matches = [alias for alias in aliases if alias.lower() in haystack_lower]
            if not matches:
                continue
            candidate = candidates.setdefault(name, Candidate(name=name, kind=kind))
            candidate.aliases.update(matches)
            candidate.score += len(matches) * 3 + 1
            candidate.evidence.append(_record_evidence(record, visibility))

    return sorted(
        candidates.values(),
        key=lambda item: (item.source_count, item.score, item.name.lower()),
        reverse=True,
    )


def _record_haystack(record: dict) -> str:
    return "\n".join(
        [
            str(record.get("title", "")),
            str(record.get("excerpt", "")),
            "\n".join(record.get("urls", [])),
        ]
    )


def _record_evidence(record: dict, visibility: str) -> CandidateEvidence:
    excerpt = str(record.get("excerpt", "")).replace("\n", " ")
    if len(excerpt) > 220:
        excerpt = excerpt[:217].rstrip() + "..."
    return CandidateEvidence(
        title=str(record.get("title") or record.get("relative_path") or "Untitled"),
        source_note_path=Path(record["source_note_path"]),
        raw_path=Path(record["raw_path"]),
        visibility=visibility,
        excerpt=excerpt,
    )


def _source_visibility(record: dict) -> str:
    urls = record.get("urls", [])
    if not urls:
        return "local_private"
    hosts = {_host(url) for url in urls}
    if any(_restricted_host(host) for host in hosts):
        return "restricted_or_login"
    return "public_web"


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().split(":")[0]


def _restricted_host(host: str) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in RESTRICTED_DOMAINS)


def _write_candidates_manifest(
    cfg: AppConfig,
    topics: list[Candidate],
    entities: list[Candidate],
) -> None:
    manifest_path = ensure_within_root(cfg.wiki.root_dir / "candidates.json", cfg.wiki.root_dir)
    payload = {
        "version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "topics": [_candidate_payload(item, cfg) for item in topics],
        "entities": [_candidate_payload(item, cfg) for item in entities],
    }
    safe_write_text(
        manifest_path,
        cfg.wiki.root_dir,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _candidate_payload(candidate: Candidate, cfg: AppConfig) -> dict:
    return {
        "name": candidate.name,
        "kind": candidate.kind,
        "aliases": sorted(candidate.aliases),
        "score": candidate.score,
        "source_count": candidate.source_count,
        "llm_policy": candidate.llm_policy,
        "sources": [
            {
                "title": item.title,
                "source_note_path": _relative_link(cfg.wiki.root_dir, item.source_note_path),
                "raw_path": _relative_link(cfg.wiki.root_dir, item.raw_path),
                "visibility": item.visibility,
            }
            for item in candidate.evidence
        ],
    }


def _relative_link(from_dir: Path, to_path: Path) -> str:
    return Path(os.path.relpath(to_path, from_dir)).as_posix()

