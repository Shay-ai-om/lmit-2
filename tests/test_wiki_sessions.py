from __future__ import annotations

from lmit_wiki.config import default_config
from lmit_wiki.query import QueryAnswer
from lmit_wiki.search import search_wiki
from lmit_wiki.sessions import (
    append_query_session_turn,
    archive_query_session,
    compact_query_session,
    create_query_session,
    list_query_sessions,
    load_query_session,
    save_query_session_turn,
)


def test_query_session_persists_json_and_markdown_transcript(tmp_path):
    cfg = default_config(tmp_path)

    session = create_query_session(cfg, title="Research thread")
    answer = QueryAnswer(
        question="What changed?",
        title="Change summary",
        answer_markdown="The homepage links were fixed. [S1]",
        follow_up_questions=("What should we verify?",),
        search_results=(),
        completion=None,
        saved_path=None,
    )

    updated = append_query_session_turn(cfg, session.session_id, answer)
    loaded = load_query_session(cfg, session.session_id)
    sessions = list_query_sessions(cfg)
    transcript = updated.markdown_path.read_text(encoding="utf-8")

    assert updated.json_path.exists()
    assert updated.markdown_path.exists()
    assert loaded.session_id == session.session_id
    assert loaded.title == "Research thread"
    assert len(loaded.turns) == 1
    assert loaded.turns[0].question == "What changed?"
    assert loaded.turns[0].saved_query_path is None
    assert sessions[0].session_id == session.session_id
    assert "# Research thread" in transcript
    assert "## Turn 1" in transcript
    assert "The homepage links were fixed. [S1]" in transcript
    assert any(item.kind == "session" for item in search_wiki(cfg, "homepage links", include_raw=False))


def test_archive_query_session_hides_from_recent_list_and_persists_flag(tmp_path):
    cfg = default_config(tmp_path)
    session = create_query_session(cfg, title="Old research")
    append_query_session_turn(
        cfg,
        session.session_id,
        QueryAnswer(
            question="Old question?",
            title="Old answer",
            answer_markdown="Old answer body.",
            follow_up_questions=(),
            search_results=(),
            completion=None,
            saved_path=None,
        ),
    )

    archived = archive_query_session(cfg, session.session_id)
    loaded = load_query_session(cfg, session.session_id)
    disk_payload = archived.json_path.read_text(encoding="utf-8")
    transcript = archived.markdown_path.read_text(encoding="utf-8")

    assert archived.archived_at_utc
    assert loaded.archived_at_utc == archived.archived_at_utc
    assert "archived_at_utc" in disk_payload
    assert "archived_at_utc" in transcript
    assert list_query_sessions(cfg) == []
    assert list_query_sessions(cfg, include_archived=True)[0].session_id == session.session_id


def test_compact_query_session_preserves_recent_turns_and_summary(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    session = create_query_session(cfg, title="Long thread")
    for index in range(5):
        append_query_session_turn(
            cfg,
            session.session_id,
            QueryAnswer(
                question=f"Question {index}",
                title=f"Answer {index}",
                answer_markdown=f"Answer body {index}",
                follow_up_questions=(),
                search_results=(),
                completion=None,
                saved_path=None,
            ),
        )

    def fake_completion(cfg_arg, messages, *, purpose, llm_policy):
        assert cfg_arg == cfg
        assert purpose == "wiki query session compact"
        assert "Question 0" in messages[-1]["content"]
        return "Earlier questions discussed the first three answers."

    monkeypatch.setattr("lmit_wiki.sessions.invoke_text_completion", fake_completion)

    compacted = compact_query_session(cfg, session.session_id, keep_recent_turns=2)
    transcript = compacted.markdown_path.read_text(encoding="utf-8")

    assert compacted.compact_summary == "Earlier questions discussed the first three answers."
    assert [turn.question for turn in compacted.turns] == ["Question 3", "Question 4"]
    assert "## Compact Summary" in transcript
    assert "Earlier questions discussed" in transcript


def test_save_query_session_turn_files_existing_answer_and_updates_session(tmp_path):
    cfg = default_config(tmp_path)
    session = create_query_session(cfg, title="Save later")
    append_query_session_turn(
        cfg,
        session.session_id,
        QueryAnswer(
            question="Can I save later?",
            title="Save Later Answer",
            answer_markdown="Yes, save this exact answer later.",
            follow_up_questions=("What next?",),
            search_results=(),
            completion=None,
            saved_path=None,
        ),
    )

    updated, saved_path = save_query_session_turn(cfg, session.session_id, 0)
    saved_text = saved_path.read_text(encoding="utf-8")
    transcript = updated.markdown_path.read_text(encoding="utf-8")

    assert saved_path.exists()
    assert "Can I save later?" in saved_text
    assert "Yes, save this exact answer later." in saved_text
    assert updated.turns[0].saved_query_path == saved_path.relative_to(
        cfg.wiki.root_dir
    ).as_posix()
    assert "](../queries/" in transcript
