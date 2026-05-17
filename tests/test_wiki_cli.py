from __future__ import annotations


def test_lmit_wiki_parser_exposes_wiki_subcommands_without_env_gate():
    from lmit_wiki.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["init", "--config", "config/wiki-only.windows.example.toml"])

    assert args.config.name == "wiki-only.windows.example.toml"
    assert callable(args.func)


def test_lmit_wiki_parser_exposes_stop_command():
    from lmit_wiki.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["stop", "--config", "config/wiki-only.windows.example.toml"])

    assert args.config.name == "wiki-only.windows.example.toml"
    assert callable(args.func)


def test_lmit_wiki_parser_exposes_sync_stop_and_resume_commands():
    from lmit_wiki.cli import build_parser

    parser = build_parser()

    stop_args = parser.parse_args(["sync-stop", "--config", "config/wiki-only.windows.example.toml"])
    resume_args = parser.parse_args(["sync-resume", "--config", "config/wiki-only.windows.example.toml"])

    assert stop_args.config.name == "wiki-only.windows.example.toml"
    assert resume_args.config.name == "wiki-only.windows.example.toml"
    assert callable(stop_args.func)
    assert callable(resume_args.func)

