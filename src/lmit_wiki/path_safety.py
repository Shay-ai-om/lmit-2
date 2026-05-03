from __future__ import annotations

from pathlib import Path


class PathSafetyError(ValueError):
    """Raised when a computed output path escapes the allowed root."""


def ensure_within_root(path: Path, root: Path) -> Path:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    if resolved_path == resolved_root:
        return resolved_path
    if resolved_root not in resolved_path.parents:
        raise PathSafetyError(f"path escapes root: {resolved_path} not under {resolved_root}")
    return resolved_path


def safe_write_text(path: Path, root: Path, text: str) -> None:
    safe_path = ensure_within_root(path, root)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(text, encoding="utf-8")
