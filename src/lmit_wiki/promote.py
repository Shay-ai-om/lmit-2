from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import ensure_within_root, safe_write_text
from lmit_wiki.builder import append_log


@dataclass(frozen=True)
class PromotedPage:
    name: str
    path: Path
    kind: str


def promote_checked_candidates(
    cfg: AppConfig,
    *,
    kind: str = "all",
    overwrite: bool = False,
) -> list[PromotedPage]:
    promoted: list[PromotedPage] = []
    if kind in {"all", "topic", "topics"}:
        promoted.extend(
            _promote_file(
                cfg,
                candidates_path=cfg.wiki.topics_dir / "_candidates.md",
                root=cfg.wiki.topics_dir,
                kind="topic",
                overwrite=overwrite,
            )
        )
    if kind in {"all", "entity", "entities"}:
        promoted.extend(
            _promote_file(
                cfg,
                candidates_path=cfg.wiki.entities_dir / "_candidates.md",
                root=cfg.wiki.entities_dir,
                kind="entity",
                overwrite=overwrite,
            )
        )
    if promoted:
        append_log(cfg, f"Promoted {len(promoted)} checked candidates")
    return promoted


def _promote_file(
    cfg: AppConfig,
    *,
    candidates_path: Path,
    root: Path,
    kind: str,
    overwrite: bool,
) -> list[PromotedPage]:
    if not candidates_path.exists():
        return []
    sections = _parse_candidate_sections(candidates_path.read_text(encoding="utf-8"))
    promoted: list[PromotedPage] = []
    for section in sections:
        if not section.accepted or section.suggested_path is None:
            continue
        target = ensure_within_root(cfg.wiki.root_dir / section.suggested_path, root)
        if target.exists() and not overwrite:
            continue
        body = _render_page(section, kind)
        safe_write_text(target, root, body)
        promoted.append(PromotedPage(name=section.name, path=target, kind=kind))
    return promoted


@dataclass(frozen=True)
class CandidateSection:
    name: str
    body: str
    accepted: bool
    suggested_path: Path | None
    llm_policy: str | None


def _parse_candidate_sections(text: str) -> list[CandidateSection]:
    matches = list(re.finditer(r"^## (?P<name>.+)$", text, flags=re.MULTILINE))
    sections: list[CandidateSection] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        name = match.group("name").strip()
        body = text[start:end].strip()
        accepted = bool(re.search(r"^- \[[xX]\] Accept\s*$", body, flags=re.MULTILINE))
        suggested = _field(body, "Suggested path")
        llm_policy = _field(body, "LLM policy")
        if llm_policy is not None:
            llm_policy = llm_policy.strip("`")
        sections.append(
            CandidateSection(
                name=name,
                body=body,
                accepted=accepted,
                suggested_path=Path(suggested.strip("`")) if suggested else None,
                llm_policy=llm_policy,
            )
        )
    return sections


def _render_page(section: CandidateSection, kind: str) -> str:
    title = section.name
    return (
        "---\n"
        f"title: {title}\n"
        f"type: {kind}\n"
        f"status: draft\n"
        f"llm_policy: {section.llm_policy or 'unknown'}\n"
        f"created_at_utc: {datetime.now(timezone.utc).isoformat()}\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Summary\n\n"
        "_Add a short synthesis after reviewing the linked sources._\n\n"
        "## Key Points\n\n"
        "- _Pending review._\n\n"
        "## Source Evidence\n\n"
        f"{_evidence_only(section.body)}\n\n"
        "## Open Questions\n\n"
        "- _Pending review._\n"
    )


def _evidence_only(body: str) -> str:
    marker = "### Evidence"
    if marker not in body:
        return "_No evidence copied from candidate file._"
    return body.split(marker, 1)[1].strip()


def _field(body: str, name: str) -> str | None:
    match = re.search(rf"^- {re.escape(name)}: (?P<value>.+)$", body, flags=re.MULTILINE)
    if not match:
        return None
    return match.group("value").strip()

