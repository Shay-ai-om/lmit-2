from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceDocument:
    source_path: Path
    relative_path: Path
    raw_path: Path
    source_note_path: Path
    title: str
    content_hash: str
    size: int
    urls: list[str]
    excerpt: str
    source_id: str = "raw"
    storage_key: str = ""


@dataclass(frozen=True)
class IngestResult:
    source_count: int
    copied_raw_count: int
    source_note_count: int
    index_path: Path
    log_path: Path

