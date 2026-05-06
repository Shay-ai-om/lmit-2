from __future__ import annotations


def test_lmit_wiki_parser_exposes_wiki_subcommands_without_env_gate():
    from lmit_wiki.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["init", "--config", "config/wiki-only.example.toml"])

    assert args.config.name == "wiki-only.example.toml"
    assert callable(args.func)


def test_lmit_wiki_parser_exposes_stop_command():
    from lmit_wiki.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["stop", "--config", "config/wiki-only.example.toml"])

    assert args.config.name == "wiki-only.example.toml"
    assert callable(args.func)

