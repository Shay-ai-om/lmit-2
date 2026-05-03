from __future__ import annotations

from pathlib import Path
import re
import unicodedata


URL_PATTERN = re.compile(r"https?://[^\s<>\"'\]\)}]+")
GENERIC_HEADINGS = {
    "txt source",
    "conversion result",
    "image conversion result",
    "url fetched content",
    "original text",
    "extracted urls",
}


def first_heading(markdown: str, fallback: str) -> str:
    headings: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            if title:
                headings.append(title)
    for title in headings:
        if title.strip().lower() not in GENERIC_HEADINGS:
            return title
    if headings:
        inferred = first_meaningful_line(markdown)
        if inferred:
            return inferred
        return headings[0]
    inferred = first_meaningful_line(markdown)
    if inferred:
        return inferred
    return fallback


def first_meaningful_line(markdown: str) -> str | None:
    skip_prefixes = (
        "#",
        "- ",
        "source file:",
        "source url:",
        "fetched url:",
        "final url:",
        "registry url:",
    )
    for line in markdown.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not stripped:
            continue
        if lowered in GENERIC_HEADINGS:
            continue
        if lowered.startswith(skip_prefixes):
            continue
        if stripped.startswith("http://") or stripped.startswith("https://"):
            continue
        if len(stripped) >= 6:
            return stripped[:100]
    return None


def excerpt(markdown: str, *, max_chars: int = 700) -> str:
    cleaned = "\n".join(
        line.strip()
        for line in strip_frontmatter(markdown).splitlines()
        if line.strip() and not line.lstrip().startswith("---")
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def extract_urls(markdown: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for url in URL_PATTERN.findall(markdown):
        cleaned = url.rstrip(".,;:!?)]}'\"")
        if cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)
    return urls


def slugify(value: str, *, fallback: str = "note") -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug or fallback


def source_note_name(relative_path: Path, content_hash: str) -> str:
    slug = slugify(relative_path.with_suffix("").as_posix(), fallback="source")
    return f"{slug}-{content_hash[:10]}.md"


def strip_frontmatter(markdown: str) -> str:
    stripped = markdown.lstrip()
    if not stripped.startswith("---"):
        return markdown
    match = re.match(r"^---\s*\n.*?\n---\s*\n?", stripped, flags=re.DOTALL)
    if not match:
        return markdown
    return stripped[match.end() :]

