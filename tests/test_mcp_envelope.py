"""The MCP response envelope: hard caps, paging, token accounting (R7a).

Two properties are fuzzed rather than spot-checked, because both fail on the
inputs nobody thinks to write down:

* **No cap violation.** Every enveloped response fits its tool's ``max_chars``
  on the wire and carries at most ``max_items`` items in its primary list.
* **Paging loses nothing.** Following ``next_cursor`` to the end yields the
  same items, in the same order, as one uncapped in-process call.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from tesserae import mcp_envelope
from tesserae.mcp_envelope import ToolCaps, apply_envelope, wire_text
from tesserae.mcp_server import LLMWikiMCPServer, MCPRequestHandler
from tesserae.memory.store import READ_AUDIT_ENV, read_audit_rows, read_memory, record_read
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

_VOCAB = (
    "splat gaussian radiance field atomic write durability lock lease "
    "compile graph session decision insight paper method claim render "
    "memory decay audit token budget cursor page"
).split()

_ENVELOPE_KEYS = ("est_tokens", "truncated", "next_cursor")


def _fixture_graph(seed: int = 7) -> ResearchGraph:
    rng = random.Random(seed)
    nodes: List[ResearchNode] = []
    for i in range(48):
        words = " ".join(rng.choice(_VOCAB) for _ in range(rng.randint(8, 60)))
        node_type = (
            ResearchNodeType.PAPER,
            ResearchNodeType.CONCEPT,
            ResearchNodeType.METHODOLOGICAL_CONCEPT,
        )[i % 3]
        nodes.append(
            ResearchNode(
                id=f"{node_type.value}:n{i:03d}",
                name=f"{rng.choice(_VOCAB)} {rng.choice(_VOCAB)} {i}",
                type=node_type,
                description=words,
                metadata={"first_seen_at": f"2026-0{1 + i % 9}-1{i % 9}T00:00:00Z"},
            )
        )
    papers = [n for n in nodes if n.type == ResearchNodeType.PAPER]
    edges: List[ResearchEdge] = []
    for i in range(120):
        a, b = rng.sample(nodes, 2)
        edges.append(
            ResearchEdge(
                source=a.id,
                target=b.id,
                type=rng.choice(("uses", "references", "shares_concept_with")),
                evidence=" ".join(rng.choice(_VOCAB) for _ in range(rng.randint(3, 25))),
                metadata={"valid_from": f"2026-0{1 + i % 9}-0{1 + i % 8}"},
            )
        )
    # Sessions with findings that reference the papers, so the session tools
    # have something to page.
    for s in range(6):
        sid = f"sess-{s}"
        nodes.append(
            ResearchNode(
                id=f"Session:{sid}",
                name=f"2026-05-1{s} session {s}",
                type=ResearchNodeType.SESSION,
                metadata={"session_id": sid, "started_at": f"2026-05-1{s}T10:00:00Z"},
            )
        )
        for f in range(8):
            fid = f"SessionInsight:{sid}:insight:{s:02d}{f:02d}abcdef00"
            nodes.append(
                ResearchNode(
                    id=fid,
                    name=f"finding {s}-{f} about {rng.choice(_VOCAB)} {rng.choice(_VOCAB)}",
                    type=ResearchNodeType.SESSION_INSIGHT,
                    metadata={
                        "session_id": sid,
                        "turn_ids": [f],
                        "extractor": "session-llm",
                        "first_seen_at": f"2026-05-1{s}T10:0{f}:00Z",
                    },
                )
            )
            edges.append(ResearchEdge(source=fid, target=papers[(s + f) % 4].id, type="references"))
            edges.append(ResearchEdge(source=fid, target=f"Session:{sid}", type="derived_from_session"))
    return ResearchGraph(nodes=nodes, edges=edges)


def _project(tmp_path: Path) -> Tuple[LLMWikiMCPServer, Path, ResearchGraph]:
    tess = tmp_path / ".tesserae"
    tess.mkdir(parents=True, exist_ok=True)
    graph = _fixture_graph()
    graph_path = tess / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return LLMWikiMCPServer(default_graph_path=graph_path), graph_path, graph


#: Fuzz caps, far below the shipped defaults so paging and the char cap both
#: bite on a small fixture.
_FUZZ_MAX_CHARS = 9_000
_FUZZ_MAX_ITEMS = 12


def _random_args(tool: str, rng: random.Random, graph: ResearchGraph) -> Dict[str, object]:
    query = " ".join(rng.sample(_VOCAB, rng.randint(1, 3)))
    budget = rng.choice((0, 1_500, 4_000))
    papers = [n.id for n in graph.nodes if n.type == ResearchNodeType.PAPER]
    plain = [n.id for n in graph.nodes if ":n" in n.id]
    if tool == "search_nodes":
        return {
            "query": query,
            "limit": rng.randint(1, 60),
            "mode": rng.choice(("bm25", "lexical")),
            "budget_chars": budget,
        }
    if tool == "search_facts":
        return {"query": query, "limit": rng.randint(1, 80), "budget_chars": budget}
    if tool == "timeline":
        return {"query": rng.choice(("", query)), "limit": rng.randint(1, 80), "budget_chars": budget}
    if tool == "node_context":
        return {"node_id": rng.choice(plain), "limit": rng.randint(1, 30), "budget_chars": budget}
    if tool == "graph_ppr":
        return {"seed_node_id": rng.choice(plain), "top_k": rng.randint(1, 60)}
    if tool == "find_session_findings":
        return {"node_id": rng.choice(papers), "limit": rng.randint(1, 60)}
    if tool == "list_sessions":
        return {"limit": rng.randint(1, 10)}
    if tool == "fresh_insights":
        return {"limit": rng.randint(1, 60)}
    raise AssertionError(tool)


_FUZZED_TOOLS = (
    "search_nodes",
    "search_facts",
    "timeline",
    "node_context",
    "graph_ppr",
    "find_session_findings",
    "list_sessions",
    "fresh_insights",
)


@pytest.mark.parametrize("tool", _FUZZED_TOOLS)
def test_fuzzed_calls_never_break_a_cap_and_pages_reassemble_the_uncapped_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    key = mcp_envelope.TOOL_CAPS[tool].items_key
    assert key, f"{tool} must name its paged list"
    monkeypatch.setitem(
        mcp_envelope.TOOL_CAPS,
        tool,
        ToolCaps(key, max_items=_FUZZ_MAX_ITEMS, max_chars=_FUZZ_MAX_CHARS),
    )
    server, _graph_path, graph = _project(tmp_path)
    db = tmp_path / ".tesserae" / "sqlite.db"
    rng = random.Random(f"r7-{tool}")
    violations: List[str] = []
    paged_calls = 0
    for i in range(200):
        args = _random_args(tool, rng, graph)
        page_size = rng.choice((None, 1, 3, 7, 50))
        # Reads mutate the sidecar (access counts) and some rankings read it
        # back (fresh_insights orders by decay). Run the reference call, then
        # restore the sidecar, so both calls rank against the same state.
        saved = db.read_bytes() if db.exists() else None
        uncapped = server.call_tool(tool, dict(args))
        if saved is not None:
            db.write_bytes(saved)
        elif db.exists():
            db.unlink()
        call = dict(args)
        if page_size is not None:
            call["max_items"] = page_size
        got: List[object] = []
        response = server.call_tool(tool, call, envelope=True)
        pages = 1
        while True:
            size = len(wire_text(response))
            if size > _FUZZ_MAX_CHARS:
                violations.append(f"call {i} page {pages}: {size} chars")
            if len(response[key]) > min(page_size or _FUZZ_MAX_ITEMS, _FUZZ_MAX_ITEMS):
                violations.append(f"call {i} page {pages}: {len(response[key])} items")
            assert set(_ENVELOPE_KEYS) <= set(response)
            got.extend(response[key])
            if response["next_cursor"] is None:
                break
            assert response["truncated"] is True
            response = server.call_tool(tool, {"page_cursor": response["next_cursor"]}, envelope=True)
            pages += 1
            assert pages < 500, "paging never terminated"
        paged_calls += pages > 1
        assert got == uncapped[key], f"call {i} ({args}) pages differ from the uncapped result"
    assert violations == []
    # The fuzz must actually exercise paging, or equality above is vacuous.
    assert paged_calls >= 20, paged_calls


def test_in_process_calls_keep_their_exact_payload(tmp_path: Path) -> None:
    server, _gp, _g = _project(tmp_path)
    plain = server.call_tool("graph_summary", {})
    assert not set(_ENVELOPE_KEYS) & set(plain)
    wrapped = server.call_tool("graph_summary", {}, envelope=True)
    assert {k: v for k, v in wrapped.items() if k not in _ENVELOPE_KEYS} == plain
    assert wrapped["truncated"] is False and wrapped["next_cursor"] is None


def test_est_tokens_measures_the_wire_text(tmp_path: Path) -> None:
    server, _gp, _g = _project(tmp_path)
    response = server.call_tool("search_nodes", {"query": "splat", "mode": "bm25"}, envelope=True)
    chars = len(wire_text(response))
    # The estimate is taken before its own digits are filled in.
    assert abs(response["est_tokens"] - chars / mcp_envelope.CHARS_PER_TOKEN) <= 3


def test_transport_advertises_and_applies_the_envelope(tmp_path: Path) -> None:
    server, _gp, _g = _project(tmp_path)
    handler = MCPRequestHandler(server)
    tools = handler.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    for tool in tools["result"]["tools"]:
        props = tool["inputSchema"]["properties"]
        assert "page_cursor" in props and "max_items" in props, tool["name"]
    # The in-process listing is unchanged: only the transport honours them.
    assert "page_cursor" not in server.list_tools()[0]["inputSchema"]["properties"]
    called = handler.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "search_nodes", "arguments": {"query": "splat", "max_items": 2}},
        }
    )
    payload = json.loads(called["result"]["content"][0]["text"])
    assert len(payload["nodes"]) <= 2
    assert payload["next_cursor"] and payload["truncated"] is True


def test_a_tools_own_truncated_flag_is_never_reset() -> None:
    out, _ = apply_envelope("lint_report", {"truncated": True, "body": "x"}, pages=mcp_envelope.PageStore())
    assert out["truncated"] is True


def test_max_items_cannot_exceed_the_hard_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(mcp_envelope.TOOL_CAPS, "search_nodes", ToolCaps("nodes", max_items=3))
    payload = {"nodes": [{"id": i} for i in range(10)]}
    out, _ = apply_envelope("search_nodes", payload, max_items=500, pages=mcp_envelope.PageStore())
    assert len(out["nodes"]) == 3
    assert out["page"] == {"offset": 0, "returned": 3, "total": 10}


def test_an_unpageable_oversized_response_is_clamped_not_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(mcp_envelope.TOOL_CAPS, "lint_report", ToolCaps(max_chars=2_000))
    payload = {"exists": True, "body": "word " * 5_000, "truncated": False}
    out, _ = apply_envelope("lint_report", payload, pages=mcp_envelope.PageStore())
    assert len(wire_text(out)) <= 2_000
    assert out["truncated"] is True
    assert payload["body"] == "word " * 5_000, "the tool's own payload must not be mutated"


@pytest.mark.parametrize(
    "cursor, needle",
    [("garbage", "malformed"), ("pg_0000:3:5", "expired or unknown")],
)
def test_a_bad_cursor_is_a_structured_error(tmp_path: Path, cursor: str, needle: str) -> None:
    server, _gp, _g = _project(tmp_path)
    out = server.call_tool("search_nodes", {"page_cursor": cursor}, envelope=True)
    assert needle in out["error"]
    assert out["next_cursor"] is None


def test_a_cursor_cannot_be_replayed_against_another_tool(tmp_path: Path) -> None:
    server, _gp, _g = _project(tmp_path)
    first = server.call_tool(
        "search_nodes", {"query": "splat memory", "mode": "bm25", "max_items": 1}, envelope=True
    )
    assert first["next_cursor"]
    out = server.call_tool("search_facts", {"page_cursor": first["next_cursor"]}, envelope=True)
    assert "belongs to tool 'search_nodes'" in out["error"]


def _hub_project(tmp_path: Path, spokes: int = 30) -> Tuple[LLMWikiMCPServer, Path]:
    """A graph with one hub joined to ``spokes`` neighbours, and nothing else."""
    tess = tmp_path / ".tesserae"
    tess.mkdir(parents=True, exist_ok=True)
    hub = ResearchNode(id="Concept:hub", name="hub", type=ResearchNodeType.CONCEPT)
    nodes = [hub] + [
        ResearchNode(
            id=f"Concept:s{i:03d}",
            name=f"spoke {i}",
            type=ResearchNodeType.CONCEPT,
            description="spoke " * 20,
        )
        for i in range(spokes)
    ]
    edges = [
        ResearchEdge(source=hub.id, target=n.id, type="uses", evidence="ev " * 30)
        for n in nodes[1:]
    ]
    graph_path = tess / "graph.json"
    graph_path.write_text(ResearchGraph(nodes=nodes, edges=edges).to_json(indent=2), encoding="utf-8")
    return LLMWikiMCPServer(default_graph_path=graph_path), tess / "sqlite.db"


def _accessed(db: Path) -> Dict[str, int]:
    return {k: v.access_count for k, v in read_memory(db).items() if v.access_count}


def test_only_delivered_nodes_count_as_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node the agent never received is not a read, page by page."""
    monkeypatch.setenv(READ_AUDIT_ENV, "1")
    monkeypatch.setitem(mcp_envelope.TOOL_CAPS, "node_context", ToolCaps("neighbors", max_items=10))
    server, db = _hub_project(tmp_path)
    args = {"name": "hub", "limit": 200, "budget_chars": 0}
    first = server.call_tool("node_context", args, envelope=True)
    delivered = {"Concept:hub"} | {n["id"] for n in first["neighbors"]}
    assert len(first["neighbors"]) == 10 and first["next_cursor"]
    assert set(_accessed(db)) == delivered
    assert set(read_audit_rows(db)[0].node_ids) == delivered

    response = first
    while response["next_cursor"]:
        cursor = response["next_cursor"]
        response = server.call_tool("node_context", {"page_cursor": cursor}, envelope=True)
        delivered |= {n["id"] for n in response["neighbors"]}
        assert set(_accessed(db)) == delivered
        # Replaying a cursor delivers nothing new, so it counts nothing new.
        server.call_tool("node_context", {"page_cursor": cursor}, envelope=True)
    assert len(delivered) == 31
    assert set(_accessed(db).values()) == {1}, "every delivered node counted exactly once"
    audited = [nid for row in read_audit_rows(db) for nid in row.node_ids]
    assert sorted(audited) == sorted(delivered)


def test_a_call_that_does_not_page_counts_every_read(tmp_path: Path) -> None:
    server, db = _hub_project(tmp_path, spokes=5)
    out = server.call_tool("node_context", {"name": "hub", "budget_chars": 0}, envelope=True)
    assert out["next_cursor"] is None
    assert set(_accessed(db)) == {"Concept:hub"} | {n["id"] for n in out["neighbors"]}


def test_follow_up_pages_do_not_resend_what_page_one_carried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(mcp_envelope.TOOL_CAPS, "node_context", ToolCaps("neighbors", max_items=10))
    server, _db = _hub_project(tmp_path)
    first = server.call_tool(
        "node_context", {"name": "hub", "limit": 200, "budget_chars": 0}, envelope=True
    )
    assert len(first["edges"]) == 30 and first["node"]["id"] == "Concept:hub"
    second = server.call_tool("node_context", {"page_cursor": first["next_cursor"]}, envelope=True)
    assert "edges" not in second and "node" not in second
    assert set(second["omitted_keys"]) >= {"edges", "node"}
    assert len(second["neighbors"]) == 10


def test_envelope_pages_then_the_tools_own_cursor_visit_every_card_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """graph_map pages by its own ``cursor=K`` too; the two must compose."""
    from tesserae.mcp_server import _paginate_cards

    monkeypatch.setitem(mcp_envelope.TOOL_CAPS, "graph_map", ToolCaps("cards", max_items=40))
    cards = [{"id": f"card-{i:03d}", "summary": "s" * 40} for i in range(400)]
    pages = mcp_envelope.PageStore()
    seen: List[str] = []
    cursor, calls = 0, 0
    while True:
        calls += 1
        assert calls < 50, "the tool's cursor never advanced"
        tool_out = _paginate_cards({}, cards, 12_000, cursor)
        if calls == 1:
            kept = len(tool_out["cards"])
            assert 40 < kept < 400, "the tool must keep more than one envelope page"
        out, _ = apply_envelope("graph_map", tool_out, pages=pages)
        while True:
            seen.extend(card["id"] for card in out["cards"])
            if out["next_cursor"] is None:
                break
            # Only the last envelope page may carry the tool's continuation.
            assert "continuation" not in out
            out, _ = mcp_envelope.serve_page("graph_map", out["next_cursor"], pages=pages)
        continuation = out.get("continuation")
        if continuation is None:
            break
        assert continuation == tool_out["continuation"]
        cursor = int(continuation.rsplit("cursor=", 1)[1])
    assert seen == [card["id"] for card in cards]


def test_audit_rows_carry_the_calls_tokens_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(READ_AUDIT_ENV, "1")
    server, _gp, _g = _project(tmp_path)
    db = tmp_path / ".tesserae" / "sqlite.db"
    response = server.call_tool("search_nodes", {"query": "splat", "mode": "bm25"}, envelope=True)
    # fresh_insights bumps per node, so one call writes several rows.
    insights = server.call_tool("fresh_insights", {"limit": 5}, envelope=True)
    rows = read_audit_rows(db)
    by_tool: Dict[str, List[int]] = {}
    for row in reversed(rows):
        by_tool.setdefault(row.tool, []).append(row.est_tokens)
    assert by_tool["search_nodes"] == [response["est_tokens"]]
    assert len(by_tool["fresh_insights"]) > 1
    assert by_tool["fresh_insights"][0] == insights["est_tokens"]
    assert sum(by_tool["fresh_insights"][1:]) == 0
    # An in-process read is recorded too, just unmeasured.
    server.call_tool("search_nodes", {"query": "splat", "mode": "bm25"})
    assert read_audit_rows(db)[0].est_tokens == 0


def test_an_older_audit_table_gains_the_tokens_column(tmp_path: Path) -> None:
    db = tmp_path / "sqlite.db"
    con = sqlite3.connect(db)
    con.execute(
        "create table read_audit (id integer primary key autoincrement, at text not null,"
        " tool text not null default '', actor text not null default '',"
        " node_ids_json text not null default '[]', node_count integer not null default 0,"
        " tesserae_version text not null default '', schema_version integer not null default 1)"
    )
    con.execute("insert into read_audit (at, node_ids_json) values ('2026-01-01', '[\"a\"]')")
    con.commit()
    con.close()
    rows = read_audit_rows(db)
    assert [(r.node_ids, r.est_tokens) for r in rows] == [(("a",), 0)]


def test_an_overrun_is_flagged_and_logged_not_aborted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(mcp_envelope.TOOL_CAPS, "graph_summary", ToolCaps(timeout_s=1e-9))
    server, _gp, _g = _project(tmp_path)
    with caplog.at_level(logging.WARNING, logger="tesserae.mcp_server"):
        out = server.call_tool("graph_summary", {}, envelope=True)
    assert out["timeout_exceeded"] is True
    assert out["node_count"] > 0
    assert any("over its" in r.getMessage() for r in caplog.records)


def test_the_tokens_migration_survives_losing_the_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two processes open an old sidecar at once; the second ALTER must not fail."""
    from tesserae.graph_stores import sqlite as sqlite_store

    db = tmp_path / "sqlite.db"
    read_audit_rows(db)  # current schema, est_tokens already present
    real = sqlite_store._table_columns
    calls = {"n": 0}

    def stale_first_look(con: sqlite3.Connection, table: str) -> set:
        cols = real(con, table)
        calls["n"] += 1
        # The first check ran before the other process's ALTER landed.
        return cols - {"est_tokens"} if calls["n"] == 1 and table == "read_audit" else cols

    monkeypatch.setattr(sqlite_store, "_table_columns", stale_first_look)
    assert read_audit_rows(db) == []
    assert calls["n"] == 2, "the ALTER must have run and been re-checked"


def test_one_failed_audit_append_does_not_drop_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tesserae.graph_stores.sqlite import SqliteGraphStore
    from tesserae.memory.store import deferred_reads, flush_deferred_reads

    monkeypatch.setenv(READ_AUDIT_ENV, "1")
    db = tmp_path / "sqlite.db"
    with deferred_reads() as pending:
        for i in range(4):
            record_read(db, [f"n{i}"], f"2026-11-05T00:00:0{i}Z", tool="t")
    real = SqliteGraphStore.append_read_audit
    state = {"calls": 0}

    def locked_once(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        state["calls"] += 1
        if state["calls"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(SqliteGraphStore, "append_read_audit", locked_once)
    with caplog.at_level(logging.WARNING, logger="tesserae.memory.store"):
        flush_deferred_reads(pending, 77)
    rows = list(reversed(read_audit_rows(db)))
    assert [r.node_ids for r in rows] == [("n1",), ("n2",), ("n3",)]
    # The call's estimate lands on the first row that was written.
    assert [r.est_tokens for r in rows] == [77, 0, 0]
    assert any("1 of 4" in r.getMessage() for r in caplog.records)
