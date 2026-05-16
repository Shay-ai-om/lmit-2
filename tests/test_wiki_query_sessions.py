from __future__ import annotations

from lmit_wiki.builder import ingest_wiki
from lmit_wiki.config import default_config
from lmit_wiki.query import stream_wiki_query_answer
from lmit_wiki.runtime import LLMCompletion, default_runtime_settings_payload, save_runtime_settings
from lmit_wiki.sessions import append_query_session_turn, create_query_session, load_query_session


def test_stream_query_uses_session_context_and_appends_turn(tmp_path, monkeypatch):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    (source_dir / "alpha.md").write_text("# Alpha\n\nAlpha supports session mode.", encoding="utf-8")
    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[source_dir])
    session = create_query_session(cfg, title="Alpha research")
    append_query_session_turn(
        cfg,
        session.session_id,
        _answer("What is Alpha?", "Alpha is a wiki topic."),
    )
    seen_messages: list[list[dict[str, str]]] = []

    def fake_completion(cfg_arg, messages, *, purpose, llm_policy, on_chunk=None):
        seen_messages.append(messages)
        if on_chunk is not None:
            on_chunk("# Session follow up\n\nAlpha session mode is supported. [S1]")
        return LLMCompletion(
            profile_id="local",
            provider="ollama",
            model="test-model",
            content="# Session follow up\n\nAlpha session mode is supported. [S1]",
            attempts=(),
        )

    monkeypatch.setattr("lmit_wiki.query.invoke_text_completion", fake_completion)

    answer = stream_wiki_query_answer(
        cfg,
        "Can it remember follow-ups?",
        save=False,
        session_id=session.session_id,
    )
    loaded = load_query_session(cfg, session.session_id)
    user_prompt = seen_messages[0][-1]["content"]

    assert "Previous conversation:" in user_prompt
    assert "What is Alpha?" in user_prompt
    assert "Alpha is a wiki topic." in user_prompt
    assert "Use prior conversation only to interpret the current question" in user_prompt
    assert "Context:" in user_prompt
    assert "[S1]" in user_prompt
    assert answer.saved_path is None
    assert [turn.question for turn in loaded.turns] == [
        "What is Alpha?",
        "Can it remember follow-ups?",
    ]
    assert loaded.turns[-1].saved_query_path is None


def test_session_query_save_creates_query_page_and_links_turn(tmp_path, monkeypatch):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    (source_dir / "alpha.md").write_text("# Alpha\n\nAlpha can save query pages.", encoding="utf-8")
    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[source_dir])
    session = create_query_session(cfg, title="Save research")

    def fake_completion(cfg_arg, messages, *, purpose, llm_policy, on_chunk=None):
        return LLMCompletion(
            profile_id="local",
            provider="ollama",
            model="test-model",
            content="# Saved answer\n\nSaved answers keep query pages. [S1]",
            attempts=(),
        )

    monkeypatch.setattr("lmit_wiki.query.invoke_text_completion", fake_completion)

    answer = stream_wiki_query_answer(
        cfg,
        "Should this be filed?",
        save=True,
        session_id=session.session_id,
    )
    loaded = load_query_session(cfg, session.session_id)
    transcript = loaded.markdown_path.read_text(encoding="utf-8")

    assert answer.saved_path is not None
    assert answer.saved_path.exists()
    assert loaded.turns[-1].saved_query_path == answer.saved_path.relative_to(
        cfg.wiki.root_dir
    ).as_posix()
    assert f"](../queries/{answer.saved_path.name})" in transcript


def test_stream_query_context_includes_matching_deep_raw_passage(tmp_path, monkeypatch):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    deep_fact = "Deep marker fact: source notes need enough context for late evidence."
    (source_dir / "alpha.md").write_text(
        "# Alpha\n\n" + ("front matter filler " * 140) + "\n\n" + deep_fact,
        encoding="utf-8",
    )
    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[source_dir])
    seen_messages: list[list[dict[str, str]]] = []

    def fake_completion(cfg_arg, messages, *, purpose, llm_policy, on_chunk=None):
        seen_messages.append(messages)
        return LLMCompletion(
            profile_id="local",
            provider="ollama",
            model="test-model",
            content="# Deep fact\n\nThe deep marker fact is available. [S1]",
            attempts=(),
        )

    monkeypatch.setattr("lmit_wiki.query.invoke_text_completion", fake_completion)

    stream_wiki_query_answer(cfg, "Deep marker fact", save=False)

    user_prompt = seen_messages[0][-1]["content"]
    assert deep_fact in user_prompt


def test_stream_query_uses_runtime_search_limit_and_context_capacity(tmp_path, monkeypatch):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    (source_dir / "alpha.md").write_text("# Alpha\n\nShared needle " * 40, encoding="utf-8")
    (source_dir / "beta.md").write_text("# Beta\n\nShared needle " * 40, encoding="utf-8")
    cfg = default_config(tmp_path)
    ingest_wiki(cfg, source_dirs=[source_dir])
    settings = default_runtime_settings_payload()
    settings["search_limit"] = 1
    settings["query_context_char_limit"] = 200
    save_runtime_settings(cfg, settings)
    seen_messages: list[list[dict[str, str]]] = []

    def fake_completion(cfg_arg, messages, *, purpose, llm_policy, on_chunk=None):
        seen_messages.append(messages)
        return LLMCompletion(
            profile_id="local",
            provider="ollama",
            model="test-model",
            content="# Limited\n\nOnly one source is used. [S1]",
            attempts=(),
        )

    monkeypatch.setattr("lmit_wiki.query.invoke_text_completion", fake_completion)

    answer = stream_wiki_query_answer(cfg, "Shared needle", save=False)

    user_prompt = seen_messages[0][-1]["content"]
    assert len(answer.search_results) == 1
    assert "[S1]" in user_prompt
    assert "[S2]" not in user_prompt
    assert "Document clipped" in user_prompt


def _answer(question: str, answer_markdown: str):
    from lmit_wiki.query import QueryAnswer

    return QueryAnswer(
        question=question,
        title=question,
        answer_markdown=answer_markdown,
        follow_up_questions=(),
        search_results=(),
        completion=None,
        saved_path=None,
    )
