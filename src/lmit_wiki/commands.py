from __future__ import annotations

import argparse
from pathlib import Path

from lmit_wiki.config import load_config
from lmit_wiki.builder import ingest_wiki, init_wiki, lint_wiki
from lmit_wiki.auto import auto_sync_wiki
from lmit_wiki.candidates import generate_candidates
from lmit_wiki.organize import organize_promoted_pages
from lmit_wiki.promote import promote_checked_candidates
from lmit_wiki.query import answer_wiki_query
from lmit_wiki.runtime import load_runtime_settings
from lmit_wiki.search import search_wiki
from lmit_wiki.server import serve_wiki_ui


def add_wiki_subcommands(subparsers: argparse._SubParsersAction) -> None:
    wiki = subparsers.add_parser("wiki", help="manage the experimental knowledge base")
    wiki_sub = wiki.add_subparsers(required=True)
    add_wiki_command_parsers(wiki_sub)


def add_wiki_command_parsers(wiki_sub: argparse._SubParsersAction) -> None:
    init = wiki_sub.add_parser("init", help="create knowledge-base directories")
    init.add_argument("--config", type=Path)
    init.set_defaults(func=wiki_init_command)

    ingest = wiki_sub.add_parser("ingest", help="ingest converted markdown into the wiki")
    ingest.add_argument("--config", type=Path)
    ingest.add_argument(
        "--source-dir",
        dest="source_dirs",
        type=Path,
        action="append",
        help="source markdown directory; repeat to ingest multiple roots",
    )
    ingest.set_defaults(func=wiki_ingest_command)

    lint = wiki_sub.add_parser("lint", help="validate knowledge-base structure")
    lint.add_argument("--config", type=Path)
    lint.set_defaults(func=wiki_lint_command)

    candidates = wiki_sub.add_parser(
        "candidates",
        help="generate reviewable topic and entity candidates",
    )
    candidates.add_argument("--config", type=Path)
    candidates.set_defaults(func=wiki_candidates_command)

    promote = wiki_sub.add_parser(
        "promote",
        help="create draft topic/entity pages from checked candidates",
    )
    promote.add_argument("--config", type=Path)
    promote.add_argument("--kind", choices=["all", "topics", "entities"], default="all")
    promote.add_argument("--overwrite", action="store_true")
    promote.set_defaults(func=wiki_promote_command)

    organize = wiki_sub.add_parser(
        "organize",
        help="fill promoted draft pages with source-grounded structure",
    )
    organize.add_argument("--config", type=Path)
    organize.add_argument("--kind", choices=["all", "topics", "entities"], default="all")
    organize.add_argument("--no-overwrite", action="store_true")
    organize.set_defaults(func=wiki_organize_command)

    search = wiki_sub.add_parser("search", help="search wiki pages and source notes")
    search.add_argument("--config", type=Path)
    search.add_argument("query")
    search.add_argument("--limit", type=int)
    search.set_defaults(func=wiki_search_command)

    query = wiki_sub.add_parser("query", help="ask a grounded question against the wiki")
    query.add_argument("--config", type=Path)
    query.add_argument("question")
    query.add_argument("--no-save", action="store_true")
    query.set_defaults(func=wiki_query_command)

    sync = wiki_sub.add_parser(
        "sync",
        help="use an LLM to auto-select and update topic/entity pages",
    )
    sync.add_argument("--config", type=Path)
    sync.add_argument("--limit", type=int)
    sync.set_defaults(func=wiki_sync_command)

    serve = wiki_sub.add_parser("serve", help="run the wiki web UI")
    serve.add_argument("--config", type=Path)
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.set_defaults(func=wiki_serve_command)


def wiki_init_command(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    init_wiki(cfg)
    print(f"Wiki initialized: {cfg.wiki.root_dir}")
    return 0


def wiki_ingest_command(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    result = ingest_wiki(cfg, source_dirs=args.source_dirs)
    sync_result = None
    sync_error = None
    sync_skipped = False
    if cfg.wiki_runtime.auto_sync_on_ingest:
        settings = load_runtime_settings(cfg)
        if settings.ordered_profiles():
            try:
                sync_result = auto_sync_wiki(cfg)
            except Exception as exc:
                sync_error = exc
        else:
            sync_skipped = True
    print(f"Wiki ingested sources: {result.source_count}")
    print(f"Raw copies: {result.copied_raw_count}")
    print(f"Source notes: {result.source_note_count}")
    print(f"Index: {result.index_path}")
    print(f"Log: {result.log_path}")
    if sync_result is not None:
        print(
            "LLM auto sync: "
            f"processed {sync_result.processed_sources}, "
            f"created {sync_result.created_pages}, "
            f"updated {sync_result.updated_pages}"
        )
    elif sync_skipped:
        print("LLM auto sync skipped: no enabled runtime profiles are configured.")
    if sync_error is not None:
        print(f"LLM auto sync failed: {sync_error}")
        return 1
    return 0


def wiki_lint_command(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    warnings = lint_wiki(cfg)
    if not warnings:
        print("Wiki lint passed.")
        return 0
    print("Wiki lint warnings:")
    for warning in warnings:
        print(f"- {warning}")
    return 1


def wiki_candidates_command(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    topics, entities = generate_candidates(cfg)
    print(f"Topic candidates: {len(topics)}")
    print(f"Entity candidates: {len(entities)}")
    print(f"Topics file: {cfg.wiki.topics_dir / '_candidates.md'}")
    print(f"Entities file: {cfg.wiki.entities_dir / '_candidates.md'}")
    return 0


def wiki_promote_command(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    promoted = promote_checked_candidates(
        cfg,
        kind=args.kind,
        overwrite=args.overwrite,
    )
    print(f"Promoted pages: {len(promoted)}")
    for page in promoted:
        print(f"- {page.kind}: {page.path}")
    return 0


def wiki_organize_command(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    organized = organize_promoted_pages(
        cfg,
        kind=args.kind,
        overwrite=not args.no_overwrite,
    )
    print(f"Organized pages: {len(organized)}")
    for page in organized:
        print(f"- {page.kind}: {page.path}")
    return 0


def wiki_search_command(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    results = search_wiki(cfg, args.query, limit=args.limit, include_raw=True)
    print(f"Results: {len(results)}")
    for result in results:
        print(f"- [{result.kind}] {result.title} ({result.rel_path})")
        print(f"  {result.snippet}")
    return 0


def wiki_query_command(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    answer = answer_wiki_query(cfg, args.question, save=not args.no_save)
    print(f"Title: {answer.title}")
    if answer.saved_path is not None:
        print(f"Saved: {answer.saved_path}")
    if answer.completion is not None:
        print(
            "LLM: "
            f"{answer.completion.profile_id} / "
            f"{answer.completion.provider} / "
            f"{answer.completion.model}"
        )
    print("")
    print(answer.answer_markdown)
    if answer.follow_up_questions:
        print("")
        print("Follow-up questions:")
        for item in answer.follow_up_questions:
            print(f"- {item}")
    return 0


def wiki_sync_command(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    result = auto_sync_wiki(cfg, limit=args.limit)
    print(f"Processed sources: {result.processed_sources}")
    print(f"Created pages: {result.created_pages}")
    print(f"Updated pages: {result.updated_pages}")
    for page in result.pages:
        print(f"- {page.action}: {page.kind} {page.path}")
    return 0


def wiki_serve_command(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    serve_wiki_ui(cfg, host=args.host, port=args.port)
    return 0

