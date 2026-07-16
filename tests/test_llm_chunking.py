"""Tests for tesserae.llm_chunking and its wired consumers.

The owner directive under test: session conversation history fed to an LLM
must be CHUNKED so the model reads ALL of it regardless of total length —
never truncated to fit, never one unbounded prompt. Covers the packing
primitives, the map/reduce driver, and the three consumers (activity-summary
narrative, agent-decision mining, session-graph turn chunking) against the
user-reported drop scenarios.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from tesserae.llm_chunking import (
    CHUNK_CHAR_BUDGET,
    MIN_CHUNK_CHARS,
    chunk_char_budget,
    map_reduce_text,
    pack_blocks,
    split_text,
)


# --------------------------------------------------------------------------- #
# pack_blocks
# --------------------------------------------------------------------------- #
def test_pack_blocks_packs_greedily_and_preserves_order():
    blocks = ["A" * 40, "B" * 40, "C" * 40, "D" * 40, "E" * 40]
    chunks = pack_blocks(blocks, budget=100)
    # 40 + 2 (separator) + 40 = 82 fits; a third block would exceed 100.
    assert chunks == [
        "A" * 40 + "\n\n" + "B" * 40,
        "C" * 40 + "\n\n" + "D" * 40,
        "E" * 40,
    ]
    assert all(len(c) <= 100 for c in chunks)


def test_pack_blocks_splits_oversized_block_on_line_boundaries():
    lines = [f"line{i:02d}" for i in range(20)]  # 6 chars each
    block = "\n".join(lines)
    chunks = pack_blocks([block], budget=30)
    assert len(chunks) > 1
    assert all(len(c) <= 30 for c in chunks)
    # Never mid-line: every line survives intact in exactly the chunk stream.
    joined = "\n".join(chunks)
    for line in lines:
        assert line in joined


def test_pack_blocks_hard_splits_single_line_over_budget():
    block = "x" * 100  # one line, no newlines anywhere
    chunks = pack_blocks([block], budget=30)
    assert all(len(c) <= 30 for c in chunks)
    assert "".join(chunks).replace("\n", "") == "x" * 100


def test_pack_blocks_is_deterministic_and_lossless():
    blocks = [
        "alpha\nbeta\ngamma",
        "d" * 95,  # will be hard-split at budget 40
        "\n".join(f"row-{i}" for i in range(30)),
        "tail",
    ]
    a = pack_blocks(blocks, budget=40)
    b = pack_blocks(blocks, budget=40)
    assert a == b  # deterministic
    # Nothing lost: concat of chunks == concat of blocks modulo newline seps.
    assert "".join(a).replace("\n", "") == "".join(blocks).replace("\n", "")


def test_split_text_only_hard_splits_when_a_line_exceeds_budget():
    parts = split_text("short\n" + "y" * 50 + "\nend", 20)
    assert all(len(p) <= 20 for p in parts)
    assert "".join(parts).replace("\n", "") == ("short" + "y" * 50 + "end")


def test_chunk_char_budget_env_override_and_floor(monkeypatch):
    monkeypatch.delenv("TESSERAE_LLM_CHUNK_CHARS", raising=False)
    assert chunk_char_budget() == CHUNK_CHAR_BUDGET
    monkeypatch.setenv("TESSERAE_LLM_CHUNK_CHARS", "5000")
    assert chunk_char_budget() == 5000
    monkeypatch.setenv("TESSERAE_LLM_CHUNK_CHARS", "10")  # below the floor
    assert chunk_char_budget() == MIN_CHUNK_CHARS
    monkeypatch.setenv("TESSERAE_LLM_CHUNK_CHARS", "not-an-int")
    assert chunk_char_budget() == CHUNK_CHAR_BUDGET


# --------------------------------------------------------------------------- #
# map_reduce_text
# --------------------------------------------------------------------------- #
class _Recorder:
    """complete_text stub recording every (system, user) call."""

    def __init__(self, reply="ok", fail_map_parts=(), raise_map_parts=()):
        self.calls: List[tuple] = []
        self.reply = reply
        self.fail_map_parts = set(fail_map_parts)
        self.raise_map_parts = set(raise_map_parts)

    def complete_text(self, system, user):
        self.calls.append((system, user))
        for p in self.raise_map_parts:
            if user.startswith(f"PART {p}/"):
                raise RuntimeError("backend down")
        for p in self.fail_map_parts:
            if user.startswith(f"PART {p}/"):
                return None
        if callable(self.reply):
            return self.reply(system, user)
        return self.reply

    @property
    def users(self):
        return [u for _s, u in self.calls]


def test_map_reduce_single_chunk_is_one_passthrough_call():
    client = _Recorder(reply="answer")
    out = map_reduce_text(
        client, map_system="MAP", reduce_system="REDUCE",
        chunks=["only chunk"], budget=1000,
    )
    assert out == "answer"
    assert client.calls == [("MAP", "only chunk")]  # exactly 1 call, no label


def test_map_reduce_multi_chunk_maps_then_reduces():
    client = _Recorder(reply=lambda s, u: "partial" if s == "MAP" else "final")
    out = map_reduce_text(
        client, map_system="MAP", reduce_system="REDUCE",
        chunks=["c1", "c2", "c3"], budget=10_000,
    )
    assert out == "final"
    systems = [s for s, _u in client.calls]
    assert systems == ["MAP", "MAP", "MAP", "REDUCE"]
    assert client.users[0].startswith("PART 1/3\n\n") and client.users[0].endswith("c1")
    assert client.users[1].startswith("PART 2/3\n\n")
    assert client.users[2].startswith("PART 3/3\n\n")
    # The reduce sees all the map partials.
    assert client.users[3].count("partial") == 3


def test_map_reduce_reduces_hierarchically_when_partials_exceed_budget():
    # 4 map partials of 100 chars each -> joined 406 chars > budget 300, but
    # two partials (202 chars) fit one 236-char reduce group -> 2 groups ->
    # a second (final) reduce round.
    client = _Recorder(reply=lambda s, u: ("M" * 100) if s == "MAP" else "R")
    out = map_reduce_text(
        client, map_system="MAP", reduce_system="REDUCE",
        chunks=["c1", "c2", "c3", "c4"], budget=300,
    )
    assert out == "R"
    systems = [s for s, _u in client.calls]
    assert systems == ["MAP"] * 4 + ["REDUCE", "REDUCE", "REDUCE"]
    # Group reduces are labeled; the final reduce is over the joined partials.
    assert client.users[4].startswith("PART 1/2\n\n")
    assert client.users[5].startswith("PART 2/2\n\n")
    assert client.users[6] == "R\n\nR"


def test_map_reduce_failed_part_becomes_marker_not_exception():
    client = _Recorder(
        reply=lambda s, u: "partial" if s == "MAP" else "final",
        fail_map_parts={2},
    )
    out = map_reduce_text(
        client, map_system="MAP", reduce_system="REDUCE",
        chunks=["c1", "c2", "c3"], budget=10_000,
    )
    assert out == "final"
    assert "[part 2 unavailable]" in client.users[-1]


def test_map_reduce_raising_part_becomes_marker_not_exception():
    client = _Recorder(
        reply=lambda s, u: "partial" if s == "MAP" else "final",
        raise_map_parts={1},
    )
    out = map_reduce_text(
        client, map_system="MAP", reduce_system="REDUCE",
        chunks=["c1", "c2"], budget=10_000,
    )
    assert out == "final"
    assert "[part 1 unavailable]" in client.users[-1]


def test_map_reduce_all_parts_failed_returns_empty_without_reduce():
    client = _Recorder(fail_map_parts={1, 2})
    out = map_reduce_text(
        client, map_system="MAP", reduce_system="REDUCE",
        chunks=["c1", "c2"], budget=10_000,
    )
    assert out == ""
    assert len(client.calls) == 2  # both maps, no reduce over garbage


# --------------------------------------------------------------------------- #
# The user scenario: a busy window with ~50 long sessions — nothing dropped
# --------------------------------------------------------------------------- #
def _mk_messages(n_sessions=50, turns_per_session=3, turn_chars=420):
    from tesserae.activity_summary import MessageItem

    base = datetime(2026, 7, 4, 9, tzinfo=timezone.utc)
    msgs = []
    for s in range(n_sessions):
        sid = f"sess-{s:02d}"
        for t in range(turns_per_session):
            msgs.append(
                MessageItem(
                    ts=base + timedelta(minutes=s * 10 + t),
                    role="user" if t % 2 == 0 else "assistant",
                    name=None,
                    text=f"MARKER-{sid} " + ("w" * turn_chars),
                    project="proj",
                    session_id=sid,
                    harness="claude-code",
                )
            )
    return msgs


def test_narrative_chunking_reads_every_session_within_budget():
    from tesserae.activity_summary import (
        render_session_excerpt_blocks,
        synthesize_narrative,
    )

    budget = 4_000
    msgs = _mk_messages()
    blocks = render_session_excerpt_blocks(msgs)
    assert len(blocks) == 50
    # The scenario is real: total excerpts dwarf the budget (>= 5x).
    assert sum(len(b) for b in blocks) >= 5 * budget

    client = _Recorder(
        reply=lambda s, u: "## proj\n- part bullet" if "PART" in u.split("\n", 1)[0]
        else "## proj\n- merged"
    )
    out = synthesize_narrative("# digest", client, conversation=blocks, budget=budget)

    # Merged narrative came back from the reduce over many map calls.
    assert out == "## proj\n- merged"
    assert len(client.calls) > 3
    # EVERY session id reached the model — nothing dropped for length.
    all_prompts = "\n".join(client.users)
    for s in range(50):
        assert f"session sess-{s:02d} " in all_prompts
    # And every prompt respected the budget.
    assert all(len(u) <= budget for u in client.users)


def test_narrative_single_small_input_stays_one_summary_call():
    from tesserae.activity_summary import _SUMMARY_SYSTEM, synthesize_narrative

    client = _Recorder(reply="prose")
    out = synthesize_narrative("## p\n- one commit", client, conversation="tiny excerpt")
    assert out == "prose"
    assert len(client.calls) == 1
    system, user = client.calls[0]
    assert system == _SUMMARY_SYSTEM  # not the PART-aware map prompt
    assert "one commit" in user and "tiny excerpt" in user
    assert "PART 1/" not in user


def test_decision_planted_beyond_old_24k_horizon_is_extracted():
    """A decision in the LAST of 50 long sessions sits far past the old
    project_chars=24000 truncation horizon — the old code dropped that whole
    session; chunked extraction must still mine it."""
    from tesserae.activity_summary import MessageItem, render_session_excerpt_blocks
    from tesserae.decisions import extract_agent_decisions

    budget = 4_000
    msgs = _mk_messages()
    msgs.append(
        MessageItem(
            ts=datetime(2026, 7, 4, 23, tzinfo=timezone.utc),
            role="user", name=None,
            text="after discussion we settled it: DECIDE-PLANT use postgres",
            project="proj", session_id="sess-49", harness="claude-code",
        )
    )
    blocks = render_session_excerpt_blocks(msgs)
    # The planted line lives beyond the old 24000-char horizon.
    planted_offset = "\n".join(blocks).index("DECIDE-PLANT")
    assert planted_offset > 24_000

    client = _Recorder(
        reply=lambda s, u: "Use postgres :: planted" if "DECIDE-PLANT" in u else ""
    )
    got = extract_agent_decisions(
        blocks, client, "proj", datetime(2026, 7, 4, tzinfo=timezone.utc),
        budget=budget,
    )
    assert [d.question for d in got] == ["Use postgres"]
    assert got[0].source == "agent" and got[0].answer == "planted"
    # Every session reached the model, every prompt within budget.
    all_prompts = "\n".join(client.users)
    for s in range(50):
        assert f"session sess-{s:02d} " in all_prompts
    assert all(len(u) <= budget for u in client.users)


def test_agent_decisions_dedupe_exact_duplicates_across_chunks():
    from tesserae.decisions import extract_agent_decisions

    client = _Recorder(reply="Ship it :: ready")
    got = extract_agent_decisions(
        ["block-one " + "a" * 50, "block-two " + "b" * 50],
        client, "proj", datetime(2026, 7, 4, tzinfo=timezone.utc),
        budget=64,  # forces two chunks -> two identical replies
    )
    assert len(client.calls) == 2
    assert len(got) == 1  # exact duplicate line deduped
    assert got[0].question == "Ship it"


def test_agent_decisions_chunk_failure_degrades_not_raises():
    from tesserae.decisions import extract_agent_decisions

    def _reply(system, user):
        if "block-one" in user:
            raise RuntimeError("rate limited")
        return "Keep sqlite :: simpler"

    client = _Recorder(reply=_reply)
    got = extract_agent_decisions(
        ["block-one " + "a" * 50, "block-two " + "b" * 50],
        client, "proj", datetime(2026, 7, 4, tzinfo=timezone.utc),
        budget=64,
    )
    assert [d.question for d in got] == ["Keep sqlite"]


# --------------------------------------------------------------------------- #
# session_graph_llm — size-aware turn chunking
# --------------------------------------------------------------------------- #
class _JsonRecorder:
    def __init__(self):
        self.users: List[str] = []

    def complete_json(self, *, system, user, schema_name, cache_key=None,
                      max_retries=2):
        self.users.append(user)
        return {"findings": []}


def _session():
    from tesserae.harness_sessions import HarnessSession

    return HarnessSession(
        id="sess-big", slug="sess-big", harness="claude-code",
        agent_label="Claude Code", project_name="test",
        project_root="/tmp/test", started_at="2026-07-04T10:00:00Z",
    )


def test_single_300kb_turn_is_split_read_fully_within_budget():
    from tesserae.session_graph_llm import extract_with_llm

    budget = 4_000
    lines = [f"line-{i:04d} " + "x" * 140 for i in range(2_000)]  # ~300 KB
    turns = [{"role": "user", "text": "\n".join(lines)}]
    client = _JsonRecorder()

    out = extract_with_llm(_session(), turns, [], client, budget=budget)
    assert out == []
    # The one huge turn became many chunks...
    assert len(client.users) > 10
    # ...each prompt within the budget...
    assert all(len(u) <= budget for u in client.users)
    # ...and ALL content was read across the prompts — nothing truncated.
    joined = "\n".join(client.users)
    missing = [i for i in range(2_000) if f"line-{i:04d}" not in joined]
    assert missing == []
    # Split parts keep the turn's original id.
    assert all("[turn_id=0]" in u for u in client.users)


def test_chunk_turns_size_budget_closes_chunk_early():
    from tesserae.session_graph_llm import _chunk_turns

    turns = [{"role": "user", "text": "t" * 1_500} for _ in range(10)]
    chunks = _chunk_turns(turns, max_turns_per_chunk=30, overlap=1, budget=4_000)
    # Count allows all 10 in one window; size (~1564/turn) allows only 2.
    assert all(len(c) <= 2 for c in chunks)
    assert len(chunks) > 1
    # Every turn is covered by some chunk.
    covered = {t["_turn_idx"] for c in chunks for t in c}
    assert covered == set(range(10))


def test_chunk_turns_small_session_unchanged_fast_path():
    from tesserae.session_graph_llm import _chunk_turns

    turns = [{"role": "user", "text": f"turn-{i}"} for i in range(10)]
    chunks = _chunk_turns(turns, max_turns_per_chunk=30, overlap=5)
    assert len(chunks) == 1
    assert chunks[0] is turns  # original sequence, no annotation copies
