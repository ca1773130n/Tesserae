"""Tests for the per-transition ``Event`` memory layer.

Covers the deterministic invariants of ``tesserae.session_event.extract_events``:

* significant tool / assistant turns mint exactly one ``Event`` node each with
  a content-derived (stable) id;
* consecutive events are chained with ``precedes`` edges in turn order;
* session-finding nodes link to events at their ``turn_ids`` via
  ``derived_from`` edges;
* a stub ``json_client`` enriches the one-line description (and a failing /
  absent client degrades to the deterministic template, never raising);
* a RERUN is byte-identical (the byte-idempotence blind spot).
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from tesserae.harness_sessions import HarnessSession
from tesserae.research_graph import (
    ResearchEdge,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.session_event import (
    DERIVED_FROM_EDGE,
    PRECEDES_EDGE,
    extract_events,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _session(turns: List[dict], *, session_id: str = "sess-evt") -> HarnessSession:
    return HarnessSession(
        id=session_id,
        slug=session_id,
        harness="claude-code",
        agent_label="Claude Code",
        project_name="demo",
        project_root=str(Path("/tmp/demo").resolve()),
        started_at="2026-06-13T10:00:00Z",
        ended_at="2026-06-13T11:00:00Z",
        metadata={"turns": turns},
    )


def _default_turns() -> List[dict]:
    # turn_id 0 user chatter (skipped), 1 assistant action, 2 tool call,
    # 3 trivial assistant ack (skipped), 4 tool call.
    return [
        {"role": "user", "timestamp": "2026-06-13T10:00:01Z", "text": "please fix the bug"},
        {
            "role": "assistant",
            "timestamp": "2026-06-13T10:00:02Z",
            "text": "I'll inspect the failing module and patch the off-by-one.",
        },
        {
            "role": "tool",
            "name": "Edit",
            "timestamp": "2026-06-13T10:00:03Z",
            "text": "patched tesserae/foo.py line 42",
        },
        {"role": "assistant", "timestamp": "2026-06-13T10:00:04Z", "text": "done"},
        {
            "role": "tool",
            "name": "Bash",
            "timestamp": "2026-06-13T10:00:05Z",
            "text": "pytest -q -> 12 passed",
        },
    ]


class _StubClient:
    """Deterministic stub ``LLMJsonClient`` — enriches via a fixed transform."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def complete_text(self, *, system: str, user: str, max_retries: int = 2) -> str:
        self.calls.append(user)
        return f"ENRICHED: {user}"


class _BoomClient:
    """Stub whose enrichment always raises — exercises degrade-never-raise."""

    def complete_text(self, *, system: str, user: str, max_retries: int = 2) -> str:
        raise RuntimeError("llm unavailable")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_or_none_session_returns_empty():
    assert extract_events(None) == ([], [])
    assert extract_events(_session([])) == ([], [])
    # Only chatter -> no significant turns.
    chatter = _session([{"role": "user", "text": "hi"}, {"role": "assistant", "text": "ok"}])
    assert extract_events(chatter) == ([], [])


def test_significant_turns_mint_events_with_stable_ids():
    session = _session(_default_turns())
    nodes, edges = extract_events(session)

    assert [n.type for n in nodes] == [ResearchNodeType.EVENT] * 3
    # turn_ids 1 (assistant action), 2 (Edit), 4 (Bash). turns 0 and 3 skipped.
    assert [n.metadata["turn_id"] for n in nodes] == [1, 2, 4]
    assert [n.metadata["actor"] for n in nodes] == ["assistant", "tool", "tool"]
    assert nodes[1].metadata["tool"] == "Edit"
    assert nodes[2].metadata["tool"] == "Bash"

    # Ids are content-derived: stable id scheme, all distinct, no RNG.
    ids = [n.id for n in nodes]
    assert len(set(ids)) == 3
    for n in nodes:
        assert n.id.startswith("Event:")
        # first_seen_at derives from the turn timestamp, never wall-clock.
        assert n.metadata["first_seen_at"].startswith("2026-06-13T10:00:")


def test_precedes_chain_in_turn_order():
    session = _session(_default_turns())
    nodes, edges = extract_events(session)

    precedes = [e for e in edges if e.type == PRECEDES_EDGE]
    assert len(precedes) == len(nodes) - 1
    # Chain links node[i] -> node[i+1] in turn order.
    for i, edge in enumerate(precedes):
        assert edge.source == nodes[i].id
        assert edge.target == nodes[i + 1].id


def test_findings_derive_from_events_by_turn_id():
    session = _session(_default_turns())
    finding = ResearchNode(
        id="SessionDecision:patch:abcd",
        name="Patched the off-by-one in foo.py",
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "sess-evt", "turn_ids": [1, 2]},
    )
    nodes, edges = extract_events(session, findings=[finding])

    by_turn = {n.metadata["turn_id"]: n.id for n in nodes}
    derived = [e for e in edges if e.type == DERIVED_FROM_EDGE]
    # turn 1 and turn 2 both minted events -> two derived_from edges.
    assert len(derived) == 2
    assert {(e.source, e.target) for e in derived} == {
        (finding.id, by_turn[1]),
        (finding.id, by_turn[2]),
    }
    for e in derived:
        assert e.source == finding.id  # finding --derived_from--> event

    # A turn id with no minted event (0, chatter) yields no edge.
    finding_chatter = ResearchNode(
        id="SessionTODO:x:0000",
        name="x",
        type=ResearchNodeType.SESSION_TODO,
        metadata={"turn_ids": [0]},
    )
    _, edges2 = extract_events(session, findings=[finding_chatter])
    assert [e for e in edges2 if e.type == DERIVED_FROM_EDGE] == []


def test_same_named_events_from_two_sessions_survive_merge():
    """Aggressive same-name dedup must NOT collapse Event nodes from different
    sessions: identical turns in two sessions yield same display names but
    distinct (session-scoped) ids — both must survive merge_graphs()."""
    from tesserae.project import merge_graphs
    from tesserae.research_graph import ResearchGraph

    a_nodes, _ = extract_events(_session(_default_turns(), session_id="sA"))
    b_nodes, _ = extract_events(_session(_default_turns(), session_id="sB"))
    assert a_nodes and b_nodes
    # Same display names across sessions, different ids.
    assert {n.name for n in a_nodes} == {n.name for n in b_nodes}
    assert {n.id for n in a_nodes}.isdisjoint({n.id for n in b_nodes})

    merged = merge_graphs([
        ResearchGraph(nodes=a_nodes, edges=[]),
        ResearchGraph(nodes=b_nodes, edges=[]),
    ])
    surviving = {n.id for n in merged.nodes if n.type == ResearchNodeType.EVENT}
    expected = {n.id for n in a_nodes} | {n.id for n in b_nodes}
    assert surviving == expected, "events from different sessions must all survive merge"


def test_event_description_is_deterministic_regardless_of_client():
    """Event descriptions are serialized into graph.json, so they must be fully
    deterministic — a json_client must NOT change the bytes (it is accepted for
    API symmetry but unused). This is the byte-idempotence guard for the Event
    layer: an uncached LLM enrichment here would diverge graph.json across two
    identical compiles."""
    session = _session(_default_turns())
    with_client, _ = extract_events(session, json_client=_StubClient())
    without_client, _ = extract_events(session)

    assert [n.description for n in with_client] == [
        n.description for n in without_client
    ], "a client must not alter Event descriptions"
    assert all(not n.description.startswith("ENRICHED: ") for n in with_client)


def test_failing_or_absent_client_degrades_to_template():
    session = _session(_default_turns())
    # Absent client: deterministic template, mentions the tool/actor.
    base_nodes, _ = extract_events(session)
    assert any("invoked Edit" in n.description for n in base_nodes)

    # Failing client: never raises, falls back to the SAME template bytes.
    boom_nodes, _ = extract_events(session, json_client=_BoomClient())
    assert [n.description for n in boom_nodes] == [n.description for n in base_nodes]


def _dump(nodes: List[ResearchNode], edges: List[ResearchEdge]):
    return (
        [n.model_dump() for n in nodes],
        [e.model_dump() for e in edges],
    )


def test_rerun_is_byte_identical():
    session = _session(_default_turns())
    finding = ResearchNode(
        id="SessionInsight:k:1111",
        name="insight",
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"turn_ids": [2, 4]},
    )

    first = extract_events(session, findings=[finding])
    second = extract_events(session, findings=[finding])

    # Node ids / edges identical across runs (determinism).
    assert [n.id for n in first[0]] == [n.id for n in second[0]]
    assert _dump(*first) == _dump(*second)

    # And identical even when an LLM client enriched (description is the only
    # LLM-touched field; ids/edges never depend on it).
    enriched = extract_events(session, findings=[finding], json_client=_StubClient())
    assert [n.id for n in enriched[0]] == [n.id for n in first[0]]
    assert _dump(enriched[0], enriched[1])[1] == _dump(*first)[1]  # edges identical


# --------------------------------------------------------------------------
# Gate (roadmap step 4): the Event pass owns its own switch
# --------------------------------------------------------------------------


def test_event_pass_gate_is_default_on_with_config_and_env_opt_out():
    """Default-on, opt-out by config or env, env wins.

    Separate from ``distillation_enabled`` on purpose: that gate guards an LLM
    pass and is default-OFF, while this one guards a deterministic template
    pass. Sharing it meant the LLM-free layer could not be had on its own.
    """
    from tesserae.memory.distill import distillation_enabled
    from tesserae.session_event import event_pass_enabled

    assert event_pass_enabled(cfg=None, env={}) is True
    assert event_pass_enabled(cfg={"session_events": {"enabled": False}}, env={}) is False
    assert event_pass_enabled(cfg={"session_events": {"enabled": "off"}}, env={}) is False
    # env overrides config, both directions
    assert event_pass_enabled(
        cfg={"session_events": {"enabled": False}},
        env={"TESSERAE_SESSION_EVENT_PASS": "1"},
    ) is True
    assert event_pass_enabled(
        cfg=None, env={"TESSERAE_SESSION_EVENT_PASS": "no"}
    ) is False

    # The two gates are genuinely independent: the flag that runs the LLM
    # distillation must not be what decides whether Events get minted.
    assert distillation_enabled(cfg=None, env={}) is False
    assert event_pass_enabled(cfg=None, env={}) is True


# --------------------------------------------------------------------------
# Default-on means whatever this pass writes ships in every graph.json
# --------------------------------------------------------------------------


def test_minted_events_never_publish_an_absolute_home_path():
    """No ``/Users/<name>/`` in anything this pass writes.

    The Event pass is default-on for every session-bearing project, and what it
    mints is serialized into ``graph.json``, projected into the vault markdown
    and exported to any static site. Transcript text is full of absolute paths,
    so a template built straight from the turn ships the operator's home
    directory — and their account name — into all three.

    ``tesserae.okf`` already refuses to emit a raw ``/Users/...`` for exactly
    this reason (§6.2, via ``temporal.relative_source_path``). This pass has to
    agree with that rule rather than be the one producer exempt from it.
    """
    home_path = "/Users/somebody/Developer/Projects/Demo/tesserae/lint.py"
    session = _session(
        [
            {
                "role": "assistant",
                "timestamp": "2026-06-13T10:00:02Z",
                "text": f"Patching {home_path} to fix the ordering bug.",
            },
            {
                "role": "tool",
                "name": "Edit",
                "timestamp": "2026-06-13T10:00:03Z",
                "text": f"applied 2 edits to {home_path}",
            },
            {
                "role": "tool",
                "name": "Bash",
                "timestamp": "2026-06-13T10:00:04Z",
                "text": "ran /home/ci-runner/work/build.sh",
            },
        ]
    )

    nodes, _edges = extract_events(session)
    assert nodes, "fixture must mint events"

    leaked = [
        (node.id, field, value)
        for node in nodes
        for field, value in (
            ("name", node.name),
            ("description", node.description),
            ("metadata.action", str((node.metadata or {}).get("action") or "")),
        )
        if "/Users/" in value or "/home/" in value
    ]
    assert not leaked, f"absolute home paths published into graph.json: {leaked}"

    # Redaction must not silence the event: the path is replaced, not dropped.
    assert any("lint.py" in node.description for node in nodes), (
        "the file under discussion should still be identifiable"
    )


def test_home_path_redaction_does_not_depend_on_who_runs_the_compile():
    """Redacted, and redacted the same way on every machine.

    Two assertions, and both are load-bearing. The redaction has to happen —
    that is the leak. And it has to be a pure function of the text: the obvious
    implementation, ``os.path.expanduser`` or stripping ``$HOME``, makes the
    minted bytes depend on whose machine ran the compile, which would break the
    byte-idempotence this pass's default-on posture rests on and would leave
    another operator's home directory in the graph untouched.
    """
    import os

    session = _session(
        [
            {
                "role": "tool",
                "name": "Read",
                "timestamp": "2026-06-13T10:00:03Z",
                "text": "read /Users/someone-else/notes/plan.md",
            }
        ]
    )
    first = extract_events(session)
    assert first[0], "fixture must mint an event"
    assert "~/notes/plan.md" in first[0][0].description, (
        f"the home prefix must be replaced, not kept: {first[0][0].description!r}"
    )

    old_home = os.environ.get("HOME")
    try:
        # Under a HOME that MATCHES the path, an expanduser-based redaction
        # would produce different bytes than the run above.
        os.environ["HOME"] = "/Users/someone-else"
        second = extract_events(session)
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home

    assert [(n.id, n.name, n.description) for n in first[0]] == [
        (n.id, n.name, n.description) for n in second[0]
    ]


# ---------------------------------------------------------------------------
# Roadmap step 5 — the outcome of a tool call, stamped deterministically.
#
# Without this an Event says only that a tool was INVOKED. verify.py's ceiling
# ("this tool can say a document says so, never this ran and passed") is set
# here: a finding can only be promoted on a zero exit code if a zero exit code
# reached the graph.
# ---------------------------------------------------------------------------


def _tool_pair(result: dict) -> List[dict]:
    return [
        {"role": "user", "timestamp": "2026-08-09T10:00:00Z", "text": "run the suite"},
        {
            "role": "tool",
            "timestamp": "2026-08-09T10:00:01Z",
            "name": "Bash",
            "text": '{"command": "pytest"}',
        },
        {
            "role": "tool_result",
            "timestamp": "2026-08-09T10:00:09Z",
            "name": "Bash",
            "text": "1 failed, 4 passed",
            **result,
        },
    ]


def _events(turns: List[dict]) -> List[ResearchNode]:
    nodes, _ = extract_events(_session(turns))
    return nodes


def test_a_tool_result_turn_becomes_an_event_of_its_own():
    nodes = _events(_tool_pair({"exit_code": 1}))
    actors = [n.metadata.get("actor") for n in nodes]
    assert "tool_result" in actors, "the outcome of a tool call is a transition"


def test_a_failing_exit_code_is_stamped_onto_event_metadata():
    nodes = _events(_tool_pair({"exit_code": 1}))
    result = next(n for n in nodes if n.metadata.get("actor") == "tool_result")
    assert result.metadata["exit_code"] == 1
    assert result.metadata["status"] == "error"


def test_a_zero_exit_code_is_stamped_as_a_pass():
    """The one thing verify.py says it cannot currently do."""
    nodes = _events(_tool_pair({"exit_code": 0, "text": "5 passed"}))
    result = next(n for n in nodes if n.metadata.get("actor") == "tool_result")
    assert result.metadata["exit_code"] == 0
    assert result.metadata["status"] == "ok"


def test_an_is_error_flag_stamps_status_without_inventing_an_exit_code():
    """Claude carries no exit code anywhere — not in the tool_result block and
    not in the row-level toolUseResult sibling. Only Codex can supply one."""
    nodes = _events(_tool_pair({"is_error": True}))
    result = next(n for n in nodes if n.metadata.get("actor") == "tool_result")
    assert result.metadata["status"] == "error"
    assert "exit_code" not in result.metadata


def test_an_outcome_with_no_signal_says_so_rather_than_saying_nothing():
    """The tri-state, stated. ``is_error`` is omitted for every non-Bash tool
    (measured: present on 431 of 1,044 Claude results, 41.3%) and 54 of the
    1,286 Codex results carry no exit line, so "no outcome" is a large, real
    state. Leaving the key out lets the reader supply a default, and the default
    a reader supplies is "fine"."""
    nodes = _events(_tool_pair({}))
    result = next(n for n in nodes if n.metadata.get("actor") == "tool_result")
    assert result.metadata["status"] == "unreported"
    assert result.metadata["status"] != "ok"
    assert "exit_code" not in result.metadata


def test_a_boolean_is_not_read_as_an_exit_code():
    """``True`` is an ``int`` in Python, so an unguarded check reads a flag as
    "exited 1" — an outcome manufactured out of a type confusion."""
    nodes = _events(_tool_pair({"exit_code": True}))
    result = next(n for n in nodes if n.metadata.get("actor") == "tool_result")
    assert "exit_code" not in result.metadata
    assert result.metadata["status"] == "unreported"


def test_a_non_boolean_is_error_is_not_read_as_a_failure():
    """A harness that one day sends the STRING "false" must record no signal,
    not a failure — and certainly not a success."""
    nodes = _events(_tool_pair({"is_error": "false"}))
    result = next(n for n in nodes if n.metadata.get("actor") == "tool_result")
    assert result.metadata["status"] == "unreported"


def test_a_tool_result_event_does_not_collide_with_its_invocation_event():
    """Sharing an id would make events_by_turn map one turn to two events and
    double every finding's derived_from fan-out."""
    nodes = _events(_tool_pair({"exit_code": 0}))
    ids = [n.id for n in nodes]
    assert len(ids) == len(set(ids))
    invoke = next(n for n in nodes if n.metadata.get("actor") == "tool")
    result = next(n for n in nodes if n.metadata.get("actor") == "tool_result")
    assert invoke.id != result.id
    assert invoke.metadata["turn_id"] != result.metadata["turn_id"]


def test_an_ordinary_turn_stamps_exactly_what_it_stamped_before():
    """A turn carrying no outcome must produce byte-identical metadata, so the
    new stamps cannot silently move any existing Event's serialized bytes."""
    nodes = _events(
        [
            {"role": "user", "timestamp": "2026-08-09T10:00:00Z", "text": "go"},
            {
                "role": "tool",
                "timestamp": "2026-08-09T10:00:01Z",
                "name": "Bash",
                "text": '{"command": "pytest"}',
            },
        ]
    )
    (only,) = nodes
    assert set(only.metadata) == {
        "session_id",
        "turn_id",
        "actor",
        "action",
        "extractor",
        "tool",
        "first_seen_at",
    }


def test_a_tool_result_description_says_the_outcome_not_just_the_tool():
    nodes = _events(_tool_pair({"exit_code": 2, "text": "E   assert 1 == 2"}))
    result = next(n for n in nodes if n.metadata.get("actor") == "tool_result")
    assert "2" in result.description
    assert "assert 1 == 2" in result.description


def test_a_tool_result_event_is_home_path_redacted_like_every_other_event():
    nodes = _events(
        _tool_pair({"exit_code": 1, "text": "FAILED /Users/rivka/proj/tests/test_x.py"})
    )
    result = next(n for n in nodes if n.metadata.get("actor") == "tool_result")
    assert "/Users/rivka" not in result.description
    assert "~/proj/tests/test_x.py" in result.description


# ---------------------------------------------------------------------------
# The finding <-> Event index space
#
# ``derived_from`` is the edge this whole layer exists to create, and it is
# resolved by ONE number: the position of a turn in ``metadata["turns"]``. Two
# modules count that list independently, so these tests assert they agree.
# ---------------------------------------------------------------------------


def _mixed_turns() -> List[dict]:
    """A conversation whose fourth turn is a tool_result with NO text.

    Image-only Claude results flatten to ``""`` — 13 such turns across the 103
    readable transcripts in the ingest corpus, and 0 before tool results were
    captured at all. This is the first producer that can mint one.
    """
    return [
        {"role": "user", "timestamp": "2026-08-09T10:00:00Z", "text": "fix the parser"},
        {
            "role": "assistant",
            "timestamp": "2026-08-09T10:00:01Z",
            "text": "I will read the parser and find the trailing-comma bug.",
        },
        {
            "role": "tool",
            "timestamp": "2026-08-09T10:00:02Z",
            "name": "Read",
            "text": '{"file_path": "parser.py"}',
        },
        {
            "role": "tool_result",
            "timestamp": "2026-08-09T10:00:03Z",
            "name": "Read",
            "text": "",
        },
        {
            "role": "assistant",
            "timestamp": "2026-08-09T10:00:04Z",
            "text": "The parser drops a trailing comma in the header row.",
        },
    ]


def _finding(turn_ids: List[int]) -> ResearchNode:
    return ResearchNode(
        id="finding-1",
        name="the parser drops a trailing comma",
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"turn_ids": turn_ids},
    )


def test_a_finding_is_wired_to_the_event_for_the_turn_it_actually_cites():
    """The BEHAVIOUR: a finding that cites the closing assistant turn must be
    linked to that turn's Event, not to the text-less tool_result before it.

    The cited index is taken from ``_normalised_turns`` — the sequence the
    extracting model is shown and numbers its ``turn_ids`` against — so the test
    asks the question the graph asks, rather than hard-coding an index that
    would be true of only one of the two spaces.
    """
    from tesserae.session_graph import _normalised_turns

    session = _session(_mixed_turns())
    payload = _normalised_turns(session)
    cited = next(
        i for i, t in enumerate(payload) if t["text"].startswith("The parser drops")
    )
    nodes, edges = extract_events(session, findings=[_finding([cited])])
    by_id = {n.id: n for n in nodes}
    targets = [by_id[e.target] for e in edges if e.type == DERIVED_FROM_EDGE]
    assert [t.metadata["action"] for t in targets] == [
        "The parser drops a trailing comma in the header row."
    ]


def test_every_event_turn_id_indexes_the_turn_the_model_was_shown():
    """Totality, not one example: EVERY Event's ``turn_id`` must address the
    same turn in the model's view of the transcript. One drifting entry silently
    re-points every finding after it."""
    from tesserae.session_graph import _normalised_turns

    session = _session(_mixed_turns())
    payload = _normalised_turns(session)
    nodes, _ = extract_events(session)
    assert nodes
    for node in nodes:
        turn_id = node.metadata["turn_id"]
        assert 0 <= turn_id < len(payload), f"turn_id {turn_id} is off the end"
        assert payload[turn_id]["role"] == node.metadata["actor"]


def test_capturing_tool_results_does_not_renumber_the_events_already_minted():
    """Event ids are positional, so inserting a turn renumbers everything after
    it. Measured on the ingest corpus, seeding the id on the raw turn index made
    1,741 of 3,213 existing Event ids (54.2%) change — every ``derived_from``,
    citation and pinned reference to them broken, silently."""
    conversation = [t for t in _mixed_turns() if t["role"] != "tool_result"]
    before = {n.id for n in _events(conversation)}
    after = {n.id for n in _events(_mixed_turns())}
    assert before, "the conversation alone must still mint events"
    assert before <= after, (
        "capturing tool results renumbered events that already existed: "
        f"{sorted(before - after)}"
    )


def test_two_results_from_one_parallel_tool_call_get_distinct_ids():
    """The shape that forces the result key to carry its own counter.

    A parallel tool call — routine in Claude transcripts — emits two invocations
    and then two results back to back, all naming the same tool. The seed's
    positional half is the count of CONVERSATION turns, which does not advance
    across a run of results, and the other half is the action, which is the tool
    name for both. Without the per-run counter the two results are one node, so
    one Event stands for two turns and every finding citing either lands on it.
    """
    turns = [
        {"role": "user", "timestamp": "2026-08-09T10:00:00Z", "text": "read both"},
        {"role": "tool", "timestamp": "2026-08-09T10:00:01Z", "name": "Read", "text": '{"a": 1}'},
        {"role": "tool", "timestamp": "2026-08-09T10:00:01Z", "name": "Read", "text": '{"b": 2}'},
        {"role": "tool_result", "timestamp": "2026-08-09T10:00:02Z", "name": "Read", "text": "alpha"},
        {"role": "tool_result", "timestamp": "2026-08-09T10:00:02Z", "name": "Read", "text": "beta"},
    ]
    nodes = _events(turns)
    results = [n for n in nodes if n.metadata["actor"] == "tool_result"]
    assert len(results) == 2
    assert results[0].id != results[1].id
    assert len({n.id for n in nodes}) == len(nodes)
