from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import ensure_within_root, safe_write_text
from lmit_wiki.builder import append_log, init_wiki, refresh_index
from lmit_wiki.policy import EXTERNAL_LLM_ALLOWED, llm_policy_for_sources
from lmit_wiki.runtime import LLMCompletion, invoke_json_completion, invoke_text_completion
from lmit_wiki.runtime import effective_query_context_char_limit, effective_search_limit
from lmit_wiki.search import SearchResult, search_wiki
from lmit_wiki.sessions import (
    append_query_session_turn,
    compact_query_session,
    load_query_session,
    session_context_for_prompt,
)
from lmit_wiki.text import hashed_slug, strip_frontmatter


CITATION_PATTERN = re.compile(r"\[S(?P<index>\d+)\]", re.IGNORECASE)
AUTO_COMPACT_TURN_LIMIT = 12
SESSION_PROMPT_RECENT_TURNS = 4
QUERY_CONTEXT_OPENING_CHARS = 1200


@dataclass(frozen=True)
class QueryAnswer:
    question: str
    title: str
    answer_markdown: str
    follow_up_questions: tuple[str, ...]
    search_results: tuple[SearchResult, ...]
    completion: LLMCompletion | None
    saved_path: Path | None


def cited_search_results(
    answer_markdown: str,
    search_results: tuple[SearchResult, ...],
) -> tuple[tuple[int, SearchResult], ...]:
    cited: list[tuple[int, SearchResult]] = []
    seen: set[int] = set()
    for match in CITATION_PATTERN.finditer(answer_markdown):
        index = int(match.group("index"))
        if index in seen or index < 1 or index > len(search_results):
            continue
        seen.add(index)
        cited.append((index, search_results[index - 1]))
    if cited:
        return tuple(cited)
    return tuple(enumerate(search_results, start=1))


def answer_wiki_query(
    cfg: AppConfig,
    question: str,
    *,
    save: bool = True,
) -> QueryAnswer:
    init_wiki(cfg)
    search_results = tuple(
        search_wiki(
            cfg,
            question,
            limit=effective_search_limit(cfg),
            include_raw=True,
        )
    )
    if not search_results:
        answer = QueryAnswer(
            question=question,
            title=_fallback_title(question),
            answer_markdown=(
                "No matching wiki pages, source notes, or raw sources were found for this question."
            ),
            follow_up_questions=(),
            search_results=(),
            completion=None,
            saved_path=None,
        )
        if save:
            saved_path = _save_query_page(cfg, answer)
            return QueryAnswer(
                question=answer.question,
                title=answer.title,
                answer_markdown=answer.answer_markdown,
                follow_up_questions=answer.follow_up_questions,
                search_results=answer.search_results,
                completion=None,
                saved_path=saved_path,
            )
        return answer

    payload, completion = invoke_json_completion(
        cfg,
        _query_messages(
            question,
            search_results,
            context_char_limit=effective_query_context_char_limit(cfg),
        ),
        purpose="wiki query",
        llm_policy=_llm_policy_for_search_results(cfg, search_results),
    )
    title = str(payload.get("title") or _fallback_title(question)).strip()
    answer_markdown = str(payload.get("answer_markdown") or "").strip()
    if not answer_markdown:
        answer_markdown = "No grounded answer was produced."
    follow_up_questions = tuple(
        str(item).strip()
        for item in payload.get("follow_up_questions", [])
        if str(item).strip()
    )
    answer = QueryAnswer(
        question=question,
        title=title,
        answer_markdown=answer_markdown,
        follow_up_questions=follow_up_questions,
        search_results=search_results,
        completion=completion,
        saved_path=None,
    )
    if not save:
        return answer

    saved_path = _save_query_page(cfg, answer)
    return QueryAnswer(
        question=answer.question,
        title=answer.title,
        answer_markdown=answer.answer_markdown,
        follow_up_questions=answer.follow_up_questions,
        search_results=answer.search_results,
        completion=answer.completion,
        saved_path=saved_path,
    )


def stream_wiki_query_answer(
    cfg: AppConfig,
    question: str,
    *,
    save: bool = True,
    session_id: str | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> QueryAnswer:
    init_wiki(cfg)
    query_session = load_query_session(cfg, session_id) if session_id else None
    if query_session is not None and len(query_session.turns) >= AUTO_COMPACT_TURN_LIMIT:
        try:
            query_session = compact_query_session(cfg, query_session.session_id)
        except Exception:
            # Manual compact reports errors; auto-compact should not block the current answer.
            query_session = load_query_session(cfg, query_session.session_id)
    session_context = (
        session_context_for_prompt(query_session, recent_turns=SESSION_PROMPT_RECENT_TURNS)
        if query_session is not None
        else ""
    )
    search_results = tuple(
        search_wiki(
            cfg,
            question,
            limit=effective_search_limit(cfg),
            include_raw=True,
        )
    )
    if not search_results:
        answer = QueryAnswer(
            question=question,
            title=_fallback_title(question),
            answer_markdown=(
                "No matching wiki pages, source notes, or raw sources were found for this question."
            ),
            follow_up_questions=(),
            search_results=(),
            completion=None,
            saved_path=None,
        )
        if on_chunk is not None:
            on_chunk(answer.answer_markdown)
        saved_path = _save_query_page(cfg, answer) if save else None
        final_answer = QueryAnswer(
            question=answer.question,
            title=answer.title,
            answer_markdown=answer.answer_markdown,
            follow_up_questions=answer.follow_up_questions,
            search_results=answer.search_results,
            completion=None,
            saved_path=saved_path,
        )
        if query_session is not None:
            append_query_session_turn(cfg, query_session.session_id, final_answer)
        return final_answer

    completion = invoke_text_completion(
        cfg,
        _stream_query_messages(
            question,
            search_results,
            session_context=session_context,
            context_char_limit=effective_query_context_char_limit(cfg),
        ),
        purpose="wiki query",
        llm_policy=_llm_policy_for_search_results(cfg, search_results),
        on_chunk=on_chunk,
    )
    title, answer_markdown, follow_up_questions = _parse_streamed_query_response(
        question,
        completion.content,
    )
    answer = QueryAnswer(
        question=question,
        title=title,
        answer_markdown=answer_markdown,
        follow_up_questions=follow_up_questions,
        search_results=search_results,
        completion=completion,
        saved_path=None,
    )
    if not save:
        if query_session is not None:
            append_query_session_turn(cfg, query_session.session_id, answer)
        return answer

    saved_path = _save_query_page(cfg, answer)
    final_answer = QueryAnswer(
        question=answer.question,
        title=answer.title,
        answer_markdown=answer.answer_markdown,
        follow_up_questions=answer.follow_up_questions,
        search_results=answer.search_results,
        completion=answer.completion,
        saved_path=saved_path,
    )
    if query_session is not None:
        append_query_session_turn(cfg, query_session.session_id, final_answer)
    return final_answer


def _query_messages(
    question: str,
    search_results: tuple[SearchResult, ...],
    *,
    context_char_limit: int,
) -> list[dict[str, str]]:
    context_blocks: list[str] = []
    for index, result in enumerate(search_results, start=1):
        context_blocks.append(
            "\n".join(
                [
                    f"[S{index}] {result.kind} | {result.title} | {result.rel_path}",
                    _context_body_for_result(result, context_char_limit=context_char_limit),
                ]
            )
        )
    return [
        {
            "role": "system",
            "content": (
                "You maintain a persistent markdown wiki. "
                "Answer only from the provided wiki context. "
                "If the evidence is incomplete, say so explicitly. "
                "Use citations like [S1], [S2] that refer only to the provided sources. "
                "Respond in the same language as the user's question when practical. "
                "Return JSON only with keys title, answer_markdown, follow_up_questions."
            ),
        },
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    f"Question:\n{question}",
                    "Context:",
                    "\n\n".join(context_blocks),
                    (
                        "Return JSON only in this shape:\n"
                        '{"title":"short page title","answer_markdown":"markdown answer with [S#] citations","follow_up_questions":["...", "..."]}'
                    ),
                ]
            ),
        },
    ]


def _llm_policy_for_search_results(
    cfg: AppConfig,
    search_results: tuple[SearchResult, ...],
) -> str:
    manifest_path = cfg.wiki.root_dir / "manifest.json"
    if not manifest_path.exists():
        return EXTERNAL_LLM_ALLOWED

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    matched_records: list[dict[str, object]] = []
    result_paths = {result.path.resolve() for result in search_results}
    for record in manifest.get("sources", []):
        candidate_paths = {
            Path(str(record.get("raw_path", ""))).resolve(),
            Path(str(record.get("source_note_path", ""))).resolve(),
        }
        if result_paths & candidate_paths:
            matched_records.append(record)

    if not matched_records:
        return EXTERNAL_LLM_ALLOWED
    return llm_policy_for_sources(matched_records)


def _stream_query_messages(
    question: str,
    search_results: tuple[SearchResult, ...],
    *,
    session_context: str = "",
    context_char_limit: int,
) -> list[dict[str, str]]:
    context_blocks: list[str] = []
    for index, result in enumerate(search_results, start=1):
        context_blocks.append(
            "\n".join(
                [
                    f"[S{index}] {result.kind} | {result.title} | {result.rel_path}",
                    _context_body_for_result(result, context_char_limit=context_char_limit),
                ]
            )
        )
    return [
        {
            "role": "system",
            "content": (
                "You maintain a persistent markdown wiki. "
                "Answer only from the provided wiki context. "
                "If the evidence is incomplete, say so explicitly. "
                "Use citations like [S1], [S2] that refer only to the provided sources. "
                "Respond in the same language as the user's question when practical. "
                "Return markdown only. Do not wrap the answer in code fences. "
                "Start with a single H1 title, then the answer body, then a heading named "
                "'## Follow-up Questions' with zero or more bullet items."
            ),
        },
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    f"Question:\n{question}",
                    (
                        "Previous conversation:\n"
                        "Use prior conversation only to interpret the current question. "
                        "Do not cite prior conversation. Citations must refer only to [S#] sources below.\n"
                        f"{session_context}"
                    )
                    if session_context
                    else "",
                    "Context:",
                    "\n\n".join(context_blocks),
                    (
                        "Format the response exactly like this:\n"
                        "# Short title\n\n"
                        "Answer in markdown with [S#] citations.\n\n"
                        "## Follow-up Questions\n"
                        "- Optional follow-up question"
                    ),
                ]
            ),
        },
    ]


def _context_body_for_result(result: SearchResult, *, context_char_limit: int) -> str:
    text = result.path.read_text(encoding="utf-8", errors="ignore")
    body = strip_frontmatter(text).strip()
    if len(body) <= context_char_limit:
        return body

    opening_chars = min(QUERY_CONTEXT_OPENING_CHARS, max(80, context_char_limit // 2))
    opening = body[:opening_chars].strip()
    snippet = result.snippet.strip()
    if snippet and snippet not in opening:
        return "\n\n".join(
            [
                "Matched excerpt:",
                snippet,
                "Document opening:",
                opening,
                "[Document clipped; open the source for full text.]",
            ]
        )
    return "\n\n".join(
        [
            body[:context_char_limit].strip(),
            "[Document clipped; open the source for full text.]",
        ]
    )


def _save_query_page(cfg: AppConfig, answer: QueryAnswer) -> Path:
    timestamp = datetime.now(timezone.utc)
    filename = _unique_query_filename(cfg, answer.title, timestamp)
    target = ensure_within_root(cfg.wiki.queries_dir / filename, cfg.wiki.queries_dir)
    body = _render_query_page(cfg, answer, timestamp)
    safe_write_text(target, cfg.wiki.queries_dir, body)
    append_log(cfg, f"Filed query answer: {answer.title}")
    refresh_index(cfg)
    return target


def _render_query_page(
    cfg: AppConfig,
    answer: QueryAnswer,
    timestamp: datetime,
) -> str:
    lines = [
        "---",
        f"title: {json.dumps(answer.title, ensure_ascii=False)}",
        'type: "query"',
        f"question: {json.dumps(answer.question, ensure_ascii=False)}",
        f"created_at_utc: {timestamp.isoformat()}",
    ]
    if answer.completion is not None:
        lines.extend(
            [
                f"llm_profile: {json.dumps(answer.completion.profile_id)}",
                f"llm_provider: {json.dumps(answer.completion.provider)}",
                f"llm_model: {json.dumps(answer.completion.model)}",
            ]
        )
    lines.extend(
        [
            "---",
            "",
            f"# {answer.title}",
            "",
            "## Question",
            "",
            answer.question,
            "",
            "## Answer",
            "",
            answer.answer_markdown,
            "",
            "## Sources",
            "",
        ]
    )
    cited_results = cited_search_results(answer.answer_markdown, answer.search_results)
    if not cited_results:
        lines.append("- _No matching sources were found._")
    else:
        for index, result in cited_results:
            link = _relative_link(cfg.wiki.queries_dir, result.path)
            lines.append(f"- [S{index}] [{result.title}]({link}) (`{result.kind}`)")
    if answer.follow_up_questions:
        lines.extend(["", "## Follow-up Questions", ""])
        for item in answer.follow_up_questions:
            lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _unique_query_filename(cfg: AppConfig, title: str, timestamp: datetime) -> str:
    slug = hashed_slug(title or "query", fallback="query", max_chars=72)
    stem = f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{slug}"
    candidate = f"{stem}.md"
    count = 2
    while (cfg.wiki.queries_dir / candidate).exists():
        candidate = f"{stem}-{count}.md"
        count += 1
    return candidate


def _fallback_title(question: str) -> str:
    cleaned = " ".join(question.split()).strip()
    return cleaned[:80] if cleaned else "Wiki Query"


def _relative_link(from_dir: Path, to_path: Path) -> str:
    return Path(os.path.relpath(to_path, from_dir)).as_posix()


def _parse_streamed_query_response(
    question: str,
    markdown: str,
) -> tuple[str, str, tuple[str, ...]]:
    text = markdown.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("markdown"):
            text = text[8:].lstrip()
    lines = text.splitlines()

    fallback_title = _fallback_title(question)
    title = fallback_title
    answer_lines: list[str] = []
    follow_up_lines: list[str] = []
    in_follow_ups = False

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("# ") and title == fallback_title:
            title = stripped[2:].strip() or fallback_title
            continue
        if stripped.lower() == "## follow-up questions":
            in_follow_ups = True
            continue
        if in_follow_ups:
            follow_up_lines.append(line)
        else:
            answer_lines.append(line)

    answer_markdown = "\n".join(answer_lines).strip()
    if not answer_markdown:
        answer_markdown = "No grounded answer was produced."

    follow_up_questions = tuple(
        item.lstrip("-* ").strip()
        for item in follow_up_lines
        if item.lstrip("-* ").strip() and item.lstrip("-* ").strip().lower() not in {"none", "n/a"}
    )
    return title, answer_markdown, follow_up_questions

