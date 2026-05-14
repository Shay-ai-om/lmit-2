from __future__ import annotations

from pathlib import Path

from lmit_wiki.config import default_config
from lmit_wiki.query import answer_wiki_query
from lmit_wiki.search import SearchResult


def test_saved_query_sources_include_only_cited_results_with_original_labels(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    search_results = _search_results(cfg.wiki.root_dir, count=3)

    def fake_search(cfg_arg, query, *, limit=None, include_raw=True):
        return list(search_results)

    def fake_completion(cfg_arg, messages, *, purpose, llm_policy):
        return {
            "title": "Grounded Answer",
            "answer_markdown": "Only the first and third sources are used. [S1] [s3]",
            "follow_up_questions": [],
        }, None

    monkeypatch.setattr("lmit_wiki.query.search_wiki", fake_search)
    monkeypatch.setattr("lmit_wiki.query.invoke_json_completion", fake_completion)

    answer = answer_wiki_query(cfg, "what matters?", save=True)

    body = Path(answer.saved_path).read_text(encoding="utf-8")
    assert "- [S1] [Source 1]" in body
    assert "- [S2] [Source 2]" not in body
    assert "- [S3] [Source 3]" in body


def _search_results(root: Path, *, count: int) -> tuple[SearchResult, ...]:
    results: list[SearchResult] = []
    for index in range(1, count + 1):
        path = root / "wiki" / "sources" / f"source-{index}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Source {index}\n\nBody {index}", encoding="utf-8")
        results.append(
            SearchResult(
                title=f"Source {index}",
                kind="source",
                path=path,
                rel_path=f"wiki/sources/source-{index}.md",
                score=10 - index,
                snippet=f"Snippet {index}",
            )
        )
    return tuple(results)
