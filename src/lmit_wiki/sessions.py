from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import os

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import ensure_within_root, safe_write_text
from lmit_wiki.policy import LOCAL_ONLY
from lmit_wiki.runtime import invoke_text_completion
from lmit_wiki.text import hashed_slug


DEFAULT_KEEP_RECENT_TURNS = 2


@dataclass(frozen=True)
class QuerySessionTurn:
    question: str
    title: str
    answer_markdown: str
    follow_up_questions: tuple[str, ...]
    search_results: tuple[str, ...]
    created_at_utc: str
    saved_query_path: str | None = None


@dataclass(frozen=True)
class QuerySession:
    session_id: str
    title: str
    created_at_utc: str
    updated_at_utc: str
    compact_summary: str
    turns: tuple[QuerySessionTurn, ...]
    json_path: Path
    markdown_path: Path


def create_query_session(cfg: AppConfig, *, title: str | None = None) -> QuerySession:
    sessions_dir = _sessions_dir(cfg)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    cleaned_title = (title or "Ask The Wiki Session").strip() or "Ask The Wiki Session"
    session_id = _new_session_id(cleaned_title)
    session = QuerySession(
        session_id=session_id,
        title=cleaned_title,
        created_at_utc=now,
        updated_at_utc=now,
        compact_summary="",
        turns=(),
        json_path=_json_path(cfg, session_id),
        markdown_path=_markdown_path(cfg, session_id),
    )
    _write_session(cfg, session)
    return session


def list_query_sessions(cfg: AppConfig, *, limit: int = 20) -> list[QuerySession]:
    sessions_dir = _sessions_dir(cfg)
    if not sessions_dir.exists():
        return []
    sessions = [
        load_query_session(cfg, path.stem)
        for path in sessions_dir.glob("*.json")
        if not path.name.startswith("_")
    ]
    return sorted(sessions, key=lambda item: item.updated_at_utc, reverse=True)[:limit]


def load_query_session(cfg: AppConfig, session_id: str) -> QuerySession:
    cleaned = _safe_session_id(session_id)
    path = _json_path(cfg, cleaned)
    if not path.exists():
        raise FileNotFoundError(f"query session not found: {cleaned}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    turns = tuple(_turn_from_payload(item) for item in payload.get("turns", []))
    return QuerySession(
        session_id=str(payload.get("session_id") or cleaned),
        title=str(payload.get("title") or "Ask The Wiki Session"),
        created_at_utc=str(payload.get("created_at_utc") or ""),
        updated_at_utc=str(payload.get("updated_at_utc") or ""),
        compact_summary=str(payload.get("compact_summary") or ""),
        turns=turns,
        json_path=path,
        markdown_path=_markdown_path(cfg, cleaned),
    )


def append_query_session_turn(
    cfg: AppConfig,
    session_id: str,
    answer: Any,
) -> QuerySession:
    session = load_query_session(cfg, session_id)
    now = _utc_now()
    saved_path = getattr(answer, "saved_path", None)
    saved_query_path = (
        saved_path.relative_to(cfg.wiki.root_dir).as_posix()
        if isinstance(saved_path, Path) and saved_path.exists()
        else None
    )
    search_results = tuple(
        str(getattr(item, "rel_path", "")).strip()
        for item in getattr(answer, "search_results", ())
        if str(getattr(item, "rel_path", "")).strip()
    )
    turn = QuerySessionTurn(
        question=str(getattr(answer, "question", "")).strip(),
        title=str(getattr(answer, "title", "")).strip() or "Answer",
        answer_markdown=str(getattr(answer, "answer_markdown", "")).strip(),
        follow_up_questions=tuple(
            str(item).strip()
            for item in getattr(answer, "follow_up_questions", ())
            if str(item).strip()
        ),
        search_results=search_results,
        created_at_utc=now,
        saved_query_path=saved_query_path,
    )
    updated = QuerySession(
        session_id=session.session_id,
        title=session.title,
        created_at_utc=session.created_at_utc,
        updated_at_utc=now,
        compact_summary=session.compact_summary,
        turns=(*session.turns, turn),
        json_path=session.json_path,
        markdown_path=session.markdown_path,
    )
    _write_session(cfg, updated)
    return updated


def compact_query_session(
    cfg: AppConfig,
    session_id: str,
    *,
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
) -> QuerySession:
    session = load_query_session(cfg, session_id)
    if len(session.turns) <= keep_recent_turns:
        return session
    older_turns = session.turns[:-keep_recent_turns]
    recent_turns = session.turns[-keep_recent_turns:]
    completion = invoke_text_completion(
        cfg,
        _compact_messages(session, older_turns),
        purpose="wiki query session compact",
        llm_policy=LOCAL_ONLY,
    )
    summary = str(getattr(completion, "content", completion)).strip()
    now = _utc_now()
    updated = QuerySession(
        session_id=session.session_id,
        title=session.title,
        created_at_utc=session.created_at_utc,
        updated_at_utc=now,
        compact_summary=summary,
        turns=tuple(recent_turns),
        json_path=session.json_path,
        markdown_path=session.markdown_path,
    )
    _write_session(cfg, updated)
    return updated


def save_query_session_turn(
    cfg: AppConfig,
    session_id: str,
    turn_index: int,
) -> tuple[QuerySession, Path]:
    from lmit_wiki.query import QueryAnswer, _save_query_page

    session = load_query_session(cfg, session_id)
    if turn_index == -1:
        turn_index = len(session.turns) - 1
    if turn_index < 0 or turn_index >= len(session.turns):
        raise IndexError(f"session turn not found: {turn_index}")
    turn = session.turns[turn_index]
    if turn.saved_query_path:
        return session, cfg.wiki.root_dir / turn.saved_query_path
    answer = QueryAnswer(
        question=turn.question,
        title=turn.title,
        answer_markdown=turn.answer_markdown,
        follow_up_questions=turn.follow_up_questions,
        search_results=(),
        completion=None,
        saved_path=None,
    )
    saved_path = _save_query_page(cfg, answer)
    saved_rel = saved_path.relative_to(cfg.wiki.root_dir).as_posix()
    updated_turn = QuerySessionTurn(
        question=turn.question,
        title=turn.title,
        answer_markdown=turn.answer_markdown,
        follow_up_questions=turn.follow_up_questions,
        search_results=turn.search_results,
        created_at_utc=turn.created_at_utc,
        saved_query_path=saved_rel,
    )
    turns = list(session.turns)
    turns[turn_index] = updated_turn
    updated = QuerySession(
        session_id=session.session_id,
        title=session.title,
        created_at_utc=session.created_at_utc,
        updated_at_utc=_utc_now(),
        compact_summary=session.compact_summary,
        turns=tuple(turns),
        json_path=session.json_path,
        markdown_path=session.markdown_path,
    )
    _write_session(cfg, updated)
    return updated, saved_path


def session_context_for_prompt(session: QuerySession, *, recent_turns: int = 4) -> str:
    sections: list[str] = []
    if session.compact_summary.strip():
        sections.extend(["Compact summary:", session.compact_summary.strip()])
    turns = session.turns[-recent_turns:]
    if turns:
        if sections:
            sections.append("")
        sections.append("Recent turns:")
        for index, turn in enumerate(turns, start=1):
            sections.append(f"Turn {index} question: {turn.question}")
            sections.append(f"Turn {index} answer: {turn.answer_markdown}")
    return "\n".join(sections).strip()


def session_payload(session: QuerySession, *, include_turns: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": session.session_id,
        "title": session.title,
        "created_at_utc": session.created_at_utc,
        "updated_at_utc": session.updated_at_utc,
        "compact_summary": session.compact_summary,
        "turn_count": len(session.turns),
        "markdown_path": str(session.markdown_path),
    }
    if include_turns:
        payload["turns"] = [_turn_payload(turn) for turn in session.turns]
    return payload


def _write_session(cfg: AppConfig, session: QuerySession) -> None:
    safe_write_text(
        session.json_path,
        _sessions_dir(cfg),
        json.dumps(_session_payload_for_disk(session), ensure_ascii=False, indent=2),
    )
    safe_write_text(session.markdown_path, _sessions_dir(cfg), _render_markdown(session))


def _session_payload_for_disk(session: QuerySession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "title": session.title,
        "created_at_utc": session.created_at_utc,
        "updated_at_utc": session.updated_at_utc,
        "compact_summary": session.compact_summary,
        "turns": [_turn_payload(turn) for turn in session.turns],
    }


def _turn_payload(turn: QuerySessionTurn) -> dict[str, Any]:
    return {
        "question": turn.question,
        "title": turn.title,
        "answer_markdown": turn.answer_markdown,
        "follow_up_questions": list(turn.follow_up_questions),
        "search_results": list(turn.search_results),
        "created_at_utc": turn.created_at_utc,
        "saved_query_path": turn.saved_query_path,
    }


def _turn_from_payload(payload: dict[str, Any]) -> QuerySessionTurn:
    return QuerySessionTurn(
        question=str(payload.get("question") or ""),
        title=str(payload.get("title") or "Answer"),
        answer_markdown=str(payload.get("answer_markdown") or ""),
        follow_up_questions=tuple(
            str(item) for item in payload.get("follow_up_questions", []) if str(item).strip()
        ),
        search_results=tuple(
            str(item) for item in payload.get("search_results", []) if str(item).strip()
        ),
        created_at_utc=str(payload.get("created_at_utc") or ""),
        saved_query_path=(
            str(payload.get("saved_query_path")).strip()
            if payload.get("saved_query_path")
            else None
        ),
    )


def _render_markdown(session: QuerySession) -> str:
    lines = [
        "---",
        f"title: {json.dumps(session.title, ensure_ascii=False)}",
        'type: "query-session"',
        f"session_id: {json.dumps(session.session_id)}",
        f"created_at_utc: {session.created_at_utc}",
        f"updated_at_utc: {session.updated_at_utc}",
        "---",
        "",
        f"# {session.title}",
        "",
    ]
    if session.compact_summary.strip():
        lines.extend(["## Compact Summary", "", session.compact_summary.strip(), ""])
    if not session.turns:
        lines.extend(["_No turns yet._", ""])
    for index, turn in enumerate(session.turns, start=1):
        lines.extend(
            [
                f"## Turn {index}",
                "",
                "### Question",
                "",
                turn.question,
                "",
                "### Answer",
                "",
                turn.answer_markdown,
                "",
            ]
        )
        if turn.saved_query_path:
            query_link = Path(
                os.path.relpath(Path(turn.saved_query_path), Path("wiki") / "sessions")
            ).as_posix()
            lines.extend(["### Filed Query", "", f"- [{turn.title}]({query_link})", ""])
        if turn.follow_up_questions:
            lines.extend(["### Follow-up Questions", ""])
            lines.extend(f"- {item}" for item in turn.follow_up_questions)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _compact_messages(
    session: QuerySession,
    older_turns: tuple[QuerySessionTurn, ...],
) -> list[dict[str, str]]:
    transcript = []
    if session.compact_summary.strip():
        transcript.append(f"Existing compact summary:\n{session.compact_summary.strip()}")
    for index, turn in enumerate(older_turns, start=1):
        transcript.append(
            "\n".join(
                [
                    f"Turn {index}",
                    f"Question: {turn.question}",
                    f"Answer: {turn.answer_markdown}",
                ]
            )
        )
    return [
        {
            "role": "system",
            "content": (
                "Compact an Ask The Wiki conversation into a concise markdown summary. "
                "Preserve user goals, established facts, unresolved questions, and useful terms. "
                "Do not introduce new facts."
            ),
        },
        {
            "role": "user",
            "content": "\n\n".join(transcript),
        },
    ]


def _sessions_dir(cfg: AppConfig) -> Path:
    return cfg.wiki.root_dir / "wiki" / "sessions"


def _json_path(cfg: AppConfig, session_id: str) -> Path:
    return ensure_within_root(
        _sessions_dir(cfg) / f"{_safe_session_id(session_id)}.json",
        _sessions_dir(cfg),
    )


def _markdown_path(cfg: AppConfig, session_id: str) -> Path:
    return ensure_within_root(
        _sessions_dir(cfg) / f"{_safe_session_id(session_id)}.md",
        _sessions_dir(cfg),
    )


def _new_session_id(title: str) -> str:
    slug = hashed_slug(title, fallback="session", max_chars=40).removesuffix(".md")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{slug}-{uuid4().hex[:8]}"


def _safe_session_id(value: str) -> str:
    cleaned = "".join(char for char in value.strip() if char.isalnum() or char in {"-", "_"})
    if not cleaned:
        raise ValueError("session_id is required")
    return cleaned


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
