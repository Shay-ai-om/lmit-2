from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import safe_write_text


def load_wiki_state(cfg: AppConfig) -> dict[str, object]:
    if not cfg.wiki_runtime.state_path.exists():
        return _default_state()
    payload = json.loads(cfg.wiki_runtime.state_path.read_text(encoding="utf-8"))
    merged = _default_state()
    merged.update(payload)
    processed = payload.get("processed_sources")
    if isinstance(processed, dict):
        merged["processed_sources"] = processed
    failed = payload.get("failed_sources")
    if isinstance(failed, dict):
        merged["failed_sources"] = failed
    homepage = payload.get("homepage")
    if isinstance(homepage, dict):
        merged_homepage = dict(merged["homepage"])
        merged_homepage.update(homepage)
        merged["homepage"] = merged_homepage
    return merged


def save_wiki_state(cfg: AppConfig, state: dict[str, object]) -> None:
    safe_write_text(
        cfg.wiki_runtime.state_path,
        cfg.wiki.root_dir,
        json.dumps(state, ensure_ascii=False, indent=2),
    )


def set_homepage_mode(cfg: AppConfig, mode: str) -> None:
    state = load_wiki_state(cfg)
    homepage = _homepage_dict(state)
    homepage["mode"] = mode
    homepage["updated_at_utc"] = _utc_now()
    save_wiki_state(cfg, state)


def record_recent_sync(
    cfg: AppConfig,
    *,
    status: str,
    processed_sources: int,
    created_pages: int,
    updated_pages: int,
    pages: list[dict[str, str]],
) -> None:
    state = load_wiki_state(cfg)
    homepage = _homepage_dict(state)
    homepage["recent_sync_summary"] = {
        "status": status,
        "processed_sources": processed_sources,
        "created_pages": created_pages,
        "updated_pages": updated_pages,
        "updated_at_utc": _utc_now(),
    }
    homepage["recent_sync_pages"] = pages[:10]
    if created_pages or updated_pages:
        homepage["mode"] = "curated"
    homepage["updated_at_utc"] = _utc_now()
    save_wiki_state(cfg, state)


def homepage_state(cfg: AppConfig) -> dict[str, object]:
    return dict(_homepage_dict(load_wiki_state(cfg)))


def _default_state() -> dict[str, object]:
    return {
        "version": 1,
        "processed_sources": {},
        "failed_sources": {},
        "homepage": {
            "mode": "sync_pending",
            "updated_at_utc": None,
            "recent_sync_summary": None,
            "recent_sync_pages": [],
        },
    }


def _homepage_dict(state: dict[str, object]) -> dict[str, object]:
    homepage = state.get("homepage")
    if not isinstance(homepage, dict):
        homepage = dict(_default_state()["homepage"])
        state["homepage"] = homepage
    return homepage


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
