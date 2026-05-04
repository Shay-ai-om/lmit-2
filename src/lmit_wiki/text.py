from __future__ import annotations

from hashlib import sha256
import re
import unicodedata


MAX_PORTABLE_FILENAME_CHARS = 120
DEFAULT_SLUG_CHARS = 72
URL_PATTERN = re.compile(r"https?://[^\s<>\"'\]\)}]+")
GENERIC_HEADINGS = {
    "txt source",
    "conversion result",
    "image conversion result",
    "url fetched content",
    "original text",
    "extracted urls",
}
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
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


def slugify(
    value: str,
    *,
    fallback: str = "note",
    max_chars: int = DEFAULT_SLUG_CHARS,
) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    slug = slug or fallback
    slug = slug[: max(8, max_chars)].strip("-._")
    if not slug:
        slug = fallback
    if slug.lower() in WINDOWS_RESERVED_NAMES:
        slug = f"{slug}-page"
    return slug


def hashed_slug(
    value: str,
    *,
    fallback: str = "page",
    max_chars: int = DEFAULT_SLUG_CHARS,
    digest_chars: int = 10,
) -> str:
    digest = sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:digest_chars]
    available = max(8, max_chars - digest_chars - 1)
    slug = slugify(value, fallback=fallback, max_chars=available)
    return f"{slug}-{digest}"


def portable_markdown_filename(
    value: str,
    *,
    fallback: str = "page",
    max_chars: int = MAX_PORTABLE_FILENAME_CHARS,
) -> str:
    suffix = ".md"
    stem_chars = max(16, max_chars - len(suffix))
    return f"{hashed_slug(value, fallback=fallback, max_chars=stem_chars)}{suffix}"


def source_note_name(storage_key: str) -> str:
    safe_key = re.sub(r"[^a-fA-F0-9]+", "", storage_key)[:16].lower()
    return f"source-{safe_key or 'unknown'}.md"


def strip_frontmatter(markdown: str) -> str:
    stripped = markdown.lstrip()
    if not stripped.startswith("---"):
        return markdown
    match = re.match(r"^---\s*\n.*?\n---\s*\n?", stripped, flags=re.DOTALL)
    if not match:
        return markdown
    return stripped[match.end() :]

