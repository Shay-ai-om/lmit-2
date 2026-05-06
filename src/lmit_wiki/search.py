from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from lmit_wiki.config import AppConfig
from lmit_wiki.text import excerpt, first_heading, strip_frontmatter


WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


@dataclass(frozen=True)
class WikiDocument:
    title: str
    kind: str
    path: Path
    rel_path: str
    body: str


@dataclass(frozen=True)
class SearchResult:
    title: str
    kind: str
    path: Path
    rel_path: str
    score: float
    snippet: str


def load_wiki_documents(cfg: AppConfig, *, include_raw: bool = True) -> list[WikiDocument]:
    docs: list[WikiDocument] = []
    docs.extend(_load_dir(cfg.wiki.sources_dir, cfg.wiki.root_dir, "source"))
    docs.extend(_load_dir(cfg.wiki.root_dir / "wiki" / "system", cfg.wiki.root_dir, "system"))
    docs.extend(_load_dir(cfg.wiki.root_dir / "wiki" / "hubs", cfg.wiki.root_dir, "hub"))
    docs.extend(_load_dir(cfg.wiki.topics_dir, cfg.wiki.root_dir, "topic"))
    docs.extend(_load_dir(cfg.wiki.entities_dir, cfg.wiki.root_dir, "entity"))
    docs.extend(_load_dir(cfg.wiki.queries_dir, cfg.wiki.root_dir, "query"))
    if include_raw:
        docs.extend(_load_dir(cfg.wiki.raw_dir, cfg.wiki.root_dir, "raw"))
    for path, kind in (
        (cfg.wiki.index_path, "index"),
        (cfg.wiki.log_path, "log"),
    ):
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="ignore")
            docs.append(
                WikiDocument(
                    title=first_heading(text, path.stem),
                    kind=kind,
                    path=path,
                    rel_path=path.relative_to(cfg.wiki.root_dir).as_posix(),
                    body=strip_frontmatter(text),
                )
            )
    return docs


def search_wiki(
    cfg: AppConfig,
    query: str,
    *,
    limit: int | None = None,
    include_raw: bool = True,
) -> list[SearchResult]:
    cleaned_query = query.strip()
    if not cleaned_query:
        return []

    docs = load_wiki_documents(cfg, include_raw=include_raw)
    units = _search_units(cleaned_query)
    results: list[SearchResult] = []
    for doc in docs:
        score = _score_document(doc, cleaned_query, units)
        if score <= 0:
            continue
        results.append(
            SearchResult(
                title=doc.title,
                kind=doc.kind,
                path=doc.path,
                rel_path=doc.rel_path,
                score=score,
                snippet=_snippet(doc.body, cleaned_query, units),
            )
        )
    limit = limit or max(1, cfg.wiki_runtime.search_limit)
    return sorted(results, key=lambda item: (-item.score, item.rel_path.lower()))[:limit]


def search_result_payload(result: SearchResult) -> dict[str, str | float]:
    return {
        "title": result.title,
        "kind": result.kind,
        "path": str(result.path),
        "rel_path": result.rel_path,
        "score": round(result.score, 2),
        "snippet": result.snippet,
    }


def _load_dir(root: Path, wiki_root: Path, kind: str) -> list[WikiDocument]:
    if not root.exists():
        return []
    docs: list[WikiDocument] = []
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        body = strip_frontmatter(text)
        docs.append(
            WikiDocument(
                title=first_heading(body, path.stem),
                kind=kind,
                path=path,
                rel_path=path.relative_to(wiki_root).as_posix(),
                body=body,
            )
        )
    return docs


def _search_units(query: str) -> list[str]:
    lowered = query.lower().strip()
    units: list[str] = []
    seen: set[str] = set()
    for match in WORD_PATTERN.finditer(lowered):
        token = match.group(0)
        if token not in seen:
            units.append(token)
            seen.add(token)
    for match in CJK_PATTERN.finditer(lowered):
        chunk = match.group(0)
        tokens = [chunk] if len(chunk) <= 2 else [chunk[i : i + 2] for i in range(len(chunk) - 1)]
        for token in tokens:
            if token not in seen:
                units.append(token)
                seen.add(token)
    if not units and lowered:
        units.append(lowered)
    return units


def _score_document(doc: WikiDocument, query: str, units: list[str]) -> float:
    title = doc.title.lower()
    body = doc.body.lower()
    query_lower = query.lower()
    score = 0.0
    if query_lower in title:
        score += 40.0
    if query_lower in body:
        score += 18.0
    for unit in units:
        if unit in title:
            score += 8.0
        count = body.count(unit)
        if count:
            score += min(count, 6) * 2.2
    if doc.kind in {"hub", "topic", "entity", "query"}:
        score += 1.2
    return score


def _snippet(body: str, query: str, units: list[str]) -> str:
    cleaned = " ".join(line.strip() for line in body.splitlines() if line.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""

    lowered = cleaned.lower()
    query_lower = query.lower()
    match_index = lowered.find(query_lower)
    if match_index < 0:
        for unit in units:
            match_index = lowered.find(unit)
            if match_index >= 0:
                break
    if match_index < 0:
        return excerpt(cleaned, max_chars=220)

    start = max(0, match_index - 90)
    end = min(len(cleaned), match_index + 180)
    snippet = cleaned[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(cleaned):
        snippet = snippet + "..."
    return snippet

