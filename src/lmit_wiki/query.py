from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import ensure_within_root, safe_write_text
from lmit_wiki.builder import append_log, init_wiki, refresh_index
from lmit_wiki.policy import EXTERNAL_LLM_ALLOWED, llm_policy_for_sources
from lmit_wiki.runtime import LLMCompletion, invoke_json_completion, invoke_text_completion
from lmit_wiki.search import SearchResult, search_wiki
from lmit_wiki.text import hashed_slug, strip_frontmatter


@dataclass(frozen=True)
class QueryAnswer:
    question: str
    title: str
    answer_markdown: str
    follow_up_questions: tuple[str, ...]
    search_results: tuple[SearchResult, ...]
    completion: LLMCompletion | None
    saved_path: Path | None


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
            limit=cfg.wiki_runtime.search_limit,
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
        _query_messages(question, search_results),
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
    on_chunk: Callable[[str], None] | None = None,
) -> QueryAnswer:
    init_wiki(cfg)
    search_results = tuple(
        search_wiki(
            cfg,
            question,
            limit=cfg.wiki_runtime.search_limit,
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
        if not save:
            return answer
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

    completion = invoke_text_completion(
        cfg,
        _stream_query_messages(question, search_results),
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


def _query_messages(question: str, search_results: tuple[SearchResult, ...]) -> list[dict[str, str]]:
    context_blocks: list[str] = []
    for index, result in enumerate(search_results, start=1):
        text = result.path.read_text(encoding="utf-8", errors="ignore")
        body = strip_frontmatter(text).strip()
        clipped = body[:1600].strip()
        context_blocks.append(
            "\n".join(
                [
                    f"[S{index}] {result.kind} | {result.title} | {result.rel_path}",
                    clipped,
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


def _stream_query_messages(question: str, search_results: tuple[SearchResult, ...]) -> list[dict[str, str]]:
    context_blocks: list[str] = []
    for index, result in enumerate(search_results, start=1):
        text = result.path.read_text(encoding="utf-8", errors="ignore")
        body = strip_frontmatter(text).strip()
        clipped = body[:1600].strip()
        context_blocks.append(
            "\n".join(
                [
                    f"[S{index}] {result.kind} | {result.title} | {result.rel_path}",
                    clipped,
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
    if not answer.search_results:
        lines.append("- _No matching sources were found._")
    else:
        for index, result in enumerate(answer.search_results, start=1):
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

