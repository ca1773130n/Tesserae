"""The opt-in read audit: who produced the reads that ``bump_access`` counts.

``node_memory.access_count`` is the demand signal ``agent_distill``'s
forgetting-by-disuse consumes, and it cannot name a reader — so one chatty
agent polling a node and a human reading it once are the same input to what
gets absorbed or demoted. ``TESSERAE_READ_AUDIT`` attaches the actor.

Two properties matter more than the feature itself and are asserted here
directly, because both are the kind that regress quietly:

* **Default off.** An always-on ledger across ~32 MCP tools makes every read a
  write. With the flag unset nothing is recorded and no audit table is touched.
* **Never in ``graph.json``.** Every column here is query history; a copy in
  the artifact would make the compiled graph a function of how it has been
  read. ``tests/test_byte_idempotence_phase5.py`` names the keys; this file
  pins the bytes across an audited read.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.memory.store import (
    READ_AUDIT_ENV,
    READ_AUDIT_SCHEMA_VERSION,
    current_reader,
    read_audit_actors,
    read_audit_enabled,
    read_audit_rows,
    read_memory,
    reading_as,
    record_read,
)
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

PAPER_ID = "Paper:foo"
INSIGHT_ID = "SessionInsight:sess-A:insight:abc12345abcd"
DECISION_ID = "SessionDecision:sess-A:decision:def67890dead"


def _fixture_graph() -> ResearchGraph:
    """A Paper discussed in a Session with two findings referencing it.

    Deliberately the same in-memory shape ``test_read_surface_lru_bump`` uses:
    the audit attributes exactly the reads that suite proves get counted.
    """
    paper = ResearchNode(
        id=PAPER_ID,
        name="Foo Paper on atomic writes",
        type=ResearchNodeType.PAPER,
        description="A paper about atomic writes and durability.",
        source_path="docs/foo.md",
    )
    session = ResearchNode(
        id="Session:sess-A",
        name="2026-05-19 — paper deep dive",
        type=ResearchNodeType.SESSION,
        metadata={"session_id": "sess-A", "started_at": "2026-05-19T10:00:00Z"},
    )
    insight = ResearchNode(
        id=INSIGHT_ID,
        name="Foo Paper assumes atomic writes everywhere",
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": "sess-A", "turn_ids": [3], "extractor": "session-llm"},
    )
    decision = ResearchNode(
        id=DECISION_ID,
        name="Use atomic writes everywhere for durability",
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "sess-A", "turn_ids": [7], "extractor": "session-llm"},
    )
    edges = [
        ResearchEdge(source=PAPER_ID, target="Session:sess-A", type="discussed_in"),
        ResearchEdge(source=INSIGHT_ID, target=PAPER_ID, type="references"),
        ResearchEdge(source=DECISION_ID, target=PAPER_ID, type="references"),
        ResearchEdge(source=INSIGHT_ID, target="Session:sess-A", type="derived_from_session"),
        ResearchEdge(source=DECISION_ID, target="Session:sess-A", type="derived_from_session"),
    ]
    return ResearchGraph(nodes=[paper, session, insight, decision], edges=edges)


def _project(tmp_path: Path) -> tuple[LLMWikiMCPServer, Path, Path]:
    tess = tmp_path / ".tesserae"
    tess.mkdir(parents=True, exist_ok=True)
    graph_path = tess / "graph.json"
    graph_path.write_text(_fixture_graph().to_json(indent=2), encoding="utf-8")
    return LLMWikiMCPServer(default_graph_path=graph_path), tmp_path, graph_path


def _db(root: Path) -> Path:
    return root / ".tesserae" / "sqlite.db"


def _raw_audit_count(root: Path) -> int:
    """Row count read WITHOUT the accessor, so an accessor bug cannot hide one."""
    db = _db(root)
    if not db.exists():
        return 0
    con = sqlite3.connect(db)
    try:
        try:
            return int(con.execute("select count(*) from read_audit").fetchone()[0])
        except sqlite3.OperationalError:
            return 0  # table never created
    finally:
        con.close()


@pytest.fixture
def audit_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(READ_AUDIT_ENV, "1")


# --------------------------------------------------------------------------- #
# Default off                                                                  #
# --------------------------------------------------------------------------- #


def test_reads_are_counted_but_not_audited_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unset flag must leave the read path exactly as it was."""
    monkeypatch.delenv(READ_AUDIT_ENV, raising=False)
    server, root, _ = _project(tmp_path)

    server.call_tool("find_session_findings", {"node_id": PAPER_ID})

    assert read_memory(_db(root)), "the access bump must still happen — only the audit is opt-in"
    assert _raw_audit_count(root) == 0
    assert read_audit_rows(_db(root)) == []


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_only_the_documented_truthy_values_enable_the_audit(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(READ_AUDIT_ENV, value)
    assert read_audit_enabled() is False


def test_flag_vocabulary_matches_the_repo_wide_env_convention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``read_audit_enabled`` must accept exactly what ``_env_truthy`` accepts.

    ``memory.store`` cannot import ``project._env_truthy`` — that would drag
    the compile pipeline onto the MCP read path — so the vocabulary is spelled
    twice. This is what keeps the second spelling honest: a flag that answers
    to ``on`` in one pass and not in another is the kind of drift nobody
    notices until an audit is silently off.
    """
    from tesserae.project import _env_truthy

    for token in ("1", "true", "TRUE", "yes", "on", " on ", "0", "false", "no", "off", "x", ""):
        monkeypatch.setenv(READ_AUDIT_ENV, token)
        assert read_audit_enabled() == _env_truthy(READ_AUDIT_ENV), token


# --------------------------------------------------------------------------- #
# What an enabled audit records                                                #
# --------------------------------------------------------------------------- #


def test_an_audited_read_names_the_tool_the_actor_and_every_bumped_node(
    tmp_path: Path, audit_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESSERAE_ACTOR", "claude-code")
    server, root, _ = _project(tmp_path)

    result = server.call_tool("find_session_findings", {"node_id": PAPER_ID})
    surfaced = {f["node_id"] for f in result["findings"]}
    assert surfaced == {INSIGHT_ID, DECISION_ID}

    rows = read_audit_rows(_db(root))
    assert len(rows) == 1, "one read event is one row, however many nodes it surfaced"
    row = rows[0]
    assert row.tool == "find_session_findings"
    assert row.actor == "claude-code"
    assert set(row.node_ids) == surfaced
    assert row.schema_version == READ_AUDIT_SCHEMA_VERSION
    assert row.tesserae_version, "every audit row carries the release that wrote it"

    # The row explains the access counts it produced: same instant, same nodes.
    memory = read_memory(_db(root))
    for node_id in surfaced:
        assert memory[node_id].last_accessed_at == row.at


def test_an_agent_scoped_read_is_attributed_to_that_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call that resolves an agent view IS that agent reading, and it wins
    over the process-wide env identity — the env names the client, ``agent``
    names the reader inside it."""
    monkeypatch.setenv("TESSERAE_ACTOR", "some-daemon")
    resolve = LLMWikiMCPServer._resolve_read_actor

    assert resolve({"agent": "claude-code:me:reviewer"}) == "claude-code:me:reviewer"
    assert resolve({}) == "some-daemon"
    # ``read_audit``'s own ``actor`` is a filter over recorded rows, not the
    # identity of whoever is calling it, and must never be mistaken for one.
    assert resolve({"actor": "rows-about-this-actor"}) == "some-daemon"
    monkeypatch.delenv("TESSERAE_ACTOR")
    assert resolve({}) == ""


def test_an_unidentified_reader_is_anonymous_not_invented(
    tmp_path: Path, audit_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TESSERAE_ACTOR", raising=False)
    server, root, _ = _project(tmp_path)

    server.call_tool("graph_ppr", {"seed_node_id": PAPER_ID})

    rows = read_audit_rows(_db(root))
    assert rows and rows[0].actor == ""
    assert rows[0].tool == "graph_ppr"


def test_the_audit_never_touches_graph_json(tmp_path: Path, audit_on: None) -> None:
    server, root, graph_path = _project(tmp_path)
    before = graph_path.read_bytes()

    server.call_tool("find_session_findings", {"node_id": PAPER_ID})
    server.call_tool("graph_ppr", {"seed_node_id": PAPER_ID})

    assert graph_path.read_bytes() == before
    assert _raw_audit_count(root) >= 2
    # And nothing the audit records leaks into the artifact's text.
    text = graph_path.read_text(encoding="utf-8")
    for key in ("read_audit", "tesserae_version", "node_ids_json"):
        assert key not in text


# --------------------------------------------------------------------------- #
# The accessor's own contract                                                  #
# --------------------------------------------------------------------------- #


def test_record_read_writes_nothing_when_the_audit_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is in the accessor, ahead of the connection.

    Opening the store CREATEs its tables, so a gate anywhere later would make a
    disabled audit still write to disk on every read.
    """
    monkeypatch.delenv(READ_AUDIT_ENV, raising=False)
    db = tmp_path / ".tesserae" / "sqlite.db"

    assert record_read(db, ["Paper:foo"], "2026-08-14T00:00:00+00:00") is False
    assert not db.exists(), "a disabled audit must not even create the sidecar"


def test_record_read_ignores_an_event_that_touched_no_node(
    tmp_path: Path, audit_on: None
) -> None:
    db = tmp_path / ".tesserae" / "sqlite.db"
    assert record_read(db, [], "2026-08-14T00:00:00+00:00") is False
    assert record_read(db, ["", None], "2026-08-14T00:00:00+00:00") is False  # type: ignore[list-item]


def test_record_read_deduplicates_ids_preserving_rank_order(
    tmp_path: Path, audit_on: None
) -> None:
    db = tmp_path / ".tesserae" / "sqlite.db"
    assert record_read(db, ["b", "a", "b", "c"], "2026-08-14T00:00:00+00:00") is True

    rows = read_audit_rows(db)
    assert rows[0].node_ids == ("b", "a", "c")


def test_reading_as_restores_the_previous_reader(tmp_path: Path) -> None:
    assert current_reader() == ("", "")
    with reading_as("search_nodes", "alice"):
        assert current_reader() == ("search_nodes", "alice")
        with reading_as("node_context", "bob"):
            assert current_reader() == ("node_context", "bob")
        assert current_reader() == ("search_nodes", "alice")
    assert current_reader() == ("", "")


def test_rows_stay_readable_after_the_audit_is_switched_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turning the flag off stops recording; it does not erase the record."""
    db = tmp_path / ".tesserae" / "sqlite.db"
    monkeypatch.setenv(READ_AUDIT_ENV, "1")
    record_read(db, ["Paper:foo"], "2026-08-14T00:00:00+00:00", tool="query", actor="alice")

    monkeypatch.setenv(READ_AUDIT_ENV, "0")
    assert record_read(db, ["Paper:bar"], "2026-08-14T00:00:01+00:00") is False
    rows = read_audit_rows(db)
    assert [row.node_ids for row in rows] == [("Paper:foo",)]


def test_filters_narrow_by_actor_tool_and_node(tmp_path: Path, audit_on: None) -> None:
    db = tmp_path / ".tesserae" / "sqlite.db"
    record_read(db, ["Paper:foo"], "2026-08-14T00:00:00+00:00", tool="query", actor="alice")
    record_read(db, ["Paper:bar"], "2026-08-14T00:00:01+00:00", tool="ask", actor="bob")
    record_read(db, ["Paper:foo"], "2026-08-14T00:00:02+00:00", tool="ask", actor="alice")

    assert len(read_audit_rows(db, actor="alice")) == 2
    assert len(read_audit_rows(db, tool="ask")) == 2
    assert len(read_audit_rows(db, actor="alice", tool="ask")) == 1
    assert len(read_audit_rows(db, node_id="Paper:foo")) == 2
    # Newest first.
    assert read_audit_rows(db)[0].at.endswith(":02+00:00")


def test_a_node_id_filter_is_membership_not_a_substring_match(
    tmp_path: Path, audit_on: None
) -> None:
    """The store's ``like`` only narrows the scan; the answer is the parsed list.

    ``Paper:foo`` is a prefix of ``Paper:foo-2``, so a row that touched only the
    longer id matches the LIKE prefilter and must still be rejected.
    """
    db = tmp_path / ".tesserae" / "sqlite.db"
    record_read(db, ["Paper:foo-2"], "2026-08-14T00:00:00+00:00", tool="query", actor="alice")

    assert read_audit_rows(db, node_id="Paper:foo") == []
    assert len(read_audit_rows(db, node_id="Paper:foo-2")) == 1


def test_the_actor_tally_answers_whose_demand_it_was(tmp_path: Path, audit_on: None) -> None:
    db = tmp_path / ".tesserae" / "sqlite.db"
    record_read(db, ["Paper:foo", "Paper:bar"], "2026-08-14T00:00:00+00:00", tool="query", actor="chatty")
    record_read(db, ["Paper:foo"], "2026-08-14T00:00:01+00:00", tool="ask", actor="chatty")
    record_read(db, ["Paper:foo"], "2026-08-14T00:00:02+00:00", tool="ask", actor="human")

    tally = read_audit_actors(read_audit_rows(db))
    assert tally == [
        {"actor": "chatty", "reads": 2, "nodes": 2, "tools": ["ask", "query"]},
        {"actor": "human", "reads": 1, "nodes": 1, "tools": ["ask"]},
    ]


# --------------------------------------------------------------------------- #
# The read surface — the ledger must be consumed, not merely written           #
# --------------------------------------------------------------------------- #


def test_read_audit_tool_reports_rows_and_actors(
    tmp_path: Path, audit_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESSERAE_ACTOR", "alice")
    server, _root, graph_path = _project(tmp_path)
    server.call_tool("find_session_findings", {"node_id": PAPER_ID})

    payload = server.call_tool("read_audit", {"graph_path": str(graph_path)})

    assert payload["enabled"] is True
    assert [read["tool"] for read in payload["reads"]] == ["find_session_findings"]
    assert payload["reads"][0]["tesserae_version"]
    assert payload["actors"] == [
        {"actor": "alice", "reads": 1, "nodes": 2, "tools": ["find_session_findings"]}
    ]


def test_read_audit_tool_reports_the_flag_even_with_nothing_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty answer while enabled means "nothing read yet", not "switched off"."""
    monkeypatch.delenv(READ_AUDIT_ENV, raising=False)
    server, _root, graph_path = _project(tmp_path)

    payload = server.call_tool("read_audit", {"graph_path": str(graph_path)})

    assert payload == {
        "enabled": False,
        "audit_db": str(tmp_path / ".tesserae" / "sqlite.db"),
        "reads": [],
        "actors": [],
    }


def test_read_audit_is_listed_as_a_tool() -> None:
    tools = {tool["name"]: tool for tool in LLMWikiMCPServer(default_graph_path=None).list_tools()}
    assert "read_audit" in tools
    # The opt-in posture belongs in the description: an agent that cannot see
    # why the ledger is empty will read the emptiness as "nobody reads this".
    assert "TESSERAE_READ_AUDIT" in tools["read_audit"]["description"]


# --------------------------------------------------------------------------- #
# The version stamp, on every audit row in the system                          #
# --------------------------------------------------------------------------- #


def test_drill_down_audit_rows_carry_the_writing_release(tmp_path: Path) -> None:
    """The drill-down ledger is the shape this audit generalizes; it gets the
    same stamp, so a bad release is attributable wherever it wrote."""
    from tesserae.agent_distill import DistillStateStore, _state_db_path
    from tesserae.agent_view import DRILL_DOWN_AUDIT_SCOPE, drill_down

    graph = _fixture_graph()
    result = drill_down(tmp_path, graph, PAPER_ID, agent="agent-a")
    assert result["audited"] is True

    rows = DistillStateStore(_state_db_path(tmp_path)).rows(DRILL_DOWN_AUDIT_SCOPE, "agent-a")
    entry = json.loads(rows[0][3])
    assert entry["tesserae_version"], "drill-down audit row lost its release stamp"


def test_the_merge_ledger_names_the_release_that_published_it(tmp_path: Path) -> None:
    from tesserae.merge_ledger import (
        BASIS_EXACT_KEY,
        MERGE_LEDGER_SCHEMA_VERSION,
        MergeRecord,
        load_merge_ledger,
        merge_ledger_path,
        publish_merge_ledger,
    )

    path = merge_ledger_path(tmp_path)
    publish_merge_ledger(path, [MergeRecord("loser", "survivor", BASIS_EXACT_KEY)], ["survivor"])

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tesserae_version"]
    # The stamp is metadata about the writer, not part of the record shape: the
    # reader still keys on schema_version and still resolves the redirect.
    assert payload["schema_version"] == MERGE_LEDGER_SCHEMA_VERSION
    assert load_merge_ledger(tmp_path).resolve("loser") == "survivor"
