from __future__ import annotations

import argparse
from collections.abc import Sequence

from lmit_wiki.commands import add_wiki_command_parsers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lmit-wiki",
        description="Manage the LMIT wiki-only knowledge base.",
    )
    subparsers = parser.add_subparsers(required=True)
    add_wiki_command_parsers(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

