import json

import pytest

from tesserae.graph_stores import SqliteGraphStore
from tesserae.graph_stores.url_resolver import resolve_graph_store
from tesserae.mcp_server import LLMWikiMCPServer, MCPRequestHandler
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def sample_graph_path(tmp_path):
    paper = ResearchNode(
        id="Paper:dual-splat",
        name="DualSplat",
        type=ResearchNodeType.PAPER,
        description="Robust Gaussian Splatting paper",
        metadata={"arxiv_id": "2601.17835"},
    )
    method = ResearchNode(
        id="MethodologicalConcept:gaussian-splatting",
        name="Gaussian Splatting",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        aliases=["3DGS"],
        description="Point-based 3D reconstruction method",
    )
    claim = ResearchNode(
        id="PerformanceClaim:best-shape",
        name="Best shape reconstruction claim",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="DualSplat reports strong shape reconstruction results",
    )
    graph = ResearchGraph(
        nodes=[paper, method, claim],
        edges=[
            ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="uses Gaussian Splatting"),
            ResearchEdge(source=paper.id, target=claim.id, type="supports_claim", evidence="reports best shape reconstruction"),
        ],
    )
    path = tmp_path / "graph.json"
    path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return path


def test_mcp_server_lists_research_tools():
    tools = LLMWikiMCPServer().list_tools()

    names = {tool["name"] for tool in tools}
    assert {"schema", "graph_summary", "search_nodes", "node_context"}.issubset(names)
    search_tool = next(tool for tool in tools if tool["name"] == "search_nodes")
    assert search_tool["inputSchema"]["properties"]["query"]["type"] == "string"


def test_graph_summary_returns_counts_by_type(tmp_path):
    graph_path = sample_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    summary = server.call_tool("graph_summary", {})

    assert summary["node_count"] == 3
    assert summary["edge_count"] == 2
    assert summary["node_types"]["Paper"] == 1
    assert summary["edge_types"]["uses"] == 1


def test_search_nodes_matches_name_alias_description_and_type(tmp_path):
    graph_path = sample_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool("search_nodes", {"query": "3dgs shape", "types": ["MethodologicalConcept", "PerformanceClaim"], "limit": 5})

    # The query matches across the requested fields: "3dgs" hits Gaussian
    # Splatting's *alias* ("3DGS") and "shape" hits the claim's name +
    # description. Both must be returned, and the type filter must exclude the
    # Paper. We assert membership + count rather than a specific order: the
    # default hybrid (BM25 + lexical + embedding via RRF) legitimately ranks
    # the claim and the concept differently than any single lane would, and the
    # relative order of two equally-one-term-matching nodes is not a stable
    # contract. (Use mode="legacy" if a deterministic substring order is needed.)
    names = {node["name"] for node in result["nodes"]}
    assert names == {"Gaussian Splatting", "Best shape reconstruction claim"}
    assert result["total_matches"] == 2


def test_node_context_returns_incident_edges_and_neighbor_nodes(tmp_path):
    graph_path = sample_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    context = server.call_tool("node_context", {"node_id": "Paper:dual-splat"})

    assert context["node"]["name"] == "DualSplat"
    assert {edge["type"] for edge in context["edges"]} == {"uses", "supports_claim"}
    assert {node["name"] for node in context["neighbors"]} == {"Gaussian Splatting", "Best shape reconstruction claim"}


def test_json_rpc_handler_responds_to_initialize_tools_list_and_tools_call(tmp_path):
    graph_path = sample_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    handler = MCPRequestHandler(server)

    init_response = handler.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    list_response = handler.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    call_response = handler.handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "graph_summary", "arguments": {}}}
    )

    assert init_response["result"]["serverInfo"]["name"] == "tesserae"
    assert any(tool["name"] == "search_nodes" for tool in list_response["result"]["tools"])
    payload = json.loads(call_response["result"]["content"][0]["text"])
    assert payload["node_count"] == 3


def test_mcp_server_exposes_temporal_fact_search_and_timeline(tmp_path):
    graph_path = sample_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    tools = {tool["name"] for tool in server.list_tools()}
    facts = server.call_tool("search_facts", {"query": "Gaussian", "limit": 5})
    timeline = server.call_tool("timeline", {"query": "DualSplat"})

    assert {"search_facts", "timeline"}.issubset(tools)
    assert facts["total_matches"] >= 1
    assert facts["facts"][0]["predicate"] == "uses"
    assert timeline["events"]
    assert timeline["events"][0]["valid_from"]


def as_of_graph_path(tmp_path, undated_noise=0):
    """A graph whose facts carry real validity intervals.

    ``sample_graph_path`` projects only undated facts, so an ``as_of`` pivot
    cannot exclude anything there. Here ``new supersedes old`` closes the old
    finding's interval at 2026-03-01, and a third finding stays undated so the
    ``undated_included`` counter has something to count.

    Facts projected (all matching the query "splatting"):
        discussed_in old      [2026-01-01, 2026-03-01)
        discussed_in new      [2026-03-01, open)
        discussed_in undated  [undated,    open)
        supersedes   new      [2026-03-01, open)

    ``undated_noise`` adds N undated facts that the pivot keeps but the query
    "splatting" does NOT match — they hang off their own "Kitten Doc" so not
    even the object name pulls them in. They exist to separate the three
    populations an as-of response could be describing: everything the pivot
    kept, everything the query matched, and the rows actually returned.
    """
    old = ResearchNode(
        id="SessionInsight:old",
        name="old splatting finding",
        type=ResearchNodeType.SESSION_INSIGHT,
        description="old finding about splatting",
        metadata={"first_seen_at": "2026-01-01"},
    )
    new = ResearchNode(
        id="SessionInsight:new",
        name="new splatting finding",
        type=ResearchNodeType.SESSION_INSIGHT,
        description="new finding about splatting",
        metadata={"first_seen_at": "2026-03-01"},
    )
    undated = ResearchNode(
        id="SessionInsight:undated",
        name="undated splatting finding",
        type=ResearchNodeType.SESSION_INSIGHT,
        description="undated finding about splatting",
    )
    doc = ResearchNode(id="Paper:doc", name="Splatting Doc", type=ResearchNodeType.PAPER)
    kitten_doc = ResearchNode(id="Paper:kitten-doc", name="Kitten Doc", type=ResearchNodeType.PAPER)
    nodes = [old, new, undated, doc]
    edges = [
        ResearchEdge(source=old.id, target=doc.id, type="discussed_in"),
        ResearchEdge(source=new.id, target=doc.id, type="discussed_in"),
        ResearchEdge(source=undated.id, target=doc.id, type="discussed_in"),
        ResearchEdge(source=new.id, target=old.id, type="supersedes"),
    ]
    if undated_noise:
        nodes.append(kitten_doc)
        for index in range(undated_noise):
            kitten = ResearchNode(
                id=f"SessionInsight:kitten-{index}",
                name=f"kitten finding {index}",
                type=ResearchNodeType.SESSION_INSIGHT,
                description="undated finding about kittens",
            )
            nodes.append(kitten)
            edges.append(ResearchEdge(source=kitten.id, target=kitten_doc.id, type="discussed_in"))
    graph = ResearchGraph(nodes=nodes, edges=edges)
    path = tmp_path / "as_of_graph.json"
    path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return path


def test_search_facts_as_of_time_travels_and_reports_undated(tmp_path):
    """``as_of`` filters to the facts live at the pivot, and says how many
    of the survivors are undated — an agent must never get an "as of DATE"
    answer that is mostly undated rows without being told."""
    server = LLMWikiMCPServer(default_graph_path=as_of_graph_path(tmp_path))

    everything = server.call_tool("search_facts", {"query": "splatting", "limit": 50})
    at_february = server.call_tool(
        "search_facts", {"query": "splatting", "limit": 50, "as_of": "2026-02-01"}
    )

    assert everything["total_matches"] == 4
    assert "undated_included" not in everything  # no pivot ran, nothing to report
    # Only the old finding was live in February; `new` had not started and the
    # undated one is carried through by design.
    assert at_february["total_matches"] == 2
    assert {f["subject_id"] for f in at_february["facts"]} == {
        "SessionInsight:old",
        "SessionInsight:undated",
    }
    assert at_february["as_of"] == "2026-02-01"
    assert at_february["undated_included"] == 1


def test_timeline_as_of_time_travels_and_reports_undated(tmp_path):
    server = LLMWikiMCPServer(default_graph_path=as_of_graph_path(tmp_path))

    everything = server.call_tool("timeline", {"query": "splatting", "limit": 50})
    at_february = server.call_tool(
        "timeline", {"query": "splatting", "limit": 50, "as_of": "2026-02-01"}
    )

    assert len(everything["events"]) == 4
    assert "undated_included" not in everything
    assert {e["subject_id"] for e in at_february["events"]} == {
        "SessionInsight:old",
        "SessionInsight:undated",
    }
    assert at_february["as_of"] == "2026-02-01"
    assert at_february["undated_included"] == 1


@pytest.mark.parametrize("tool", ["search_facts", "timeline"])
def test_as_of_unparseable_is_a_structured_error_not_a_crash(tmp_path, tool):
    """``facts_as_of`` raises rather than silently answering over the whole
    corpus; the dispatcher must turn that into a tool error, not a traceback
    that a client cannot distinguish from a server fault."""
    server = LLMWikiMCPServer(default_graph_path=as_of_graph_path(tmp_path))

    result = server.call_tool(tool, {"query": "splatting", "as_of": "last tuesday"})

    assert "error" in result
    assert "last tuesday" in result["error"]
    # Crucially NOT the full corpus dressed up as an as-of answer.
    assert "facts" not in result and "events" not in result


def _is_dated(valid_from):
    """True iff ``valid_from`` is a timestamp ``facts_as_of`` can pivot on.

    Undated facts carry the literal string "undated", but the predicate that
    matters is the one ``temporal.facts_as_of`` applies — parseability — so
    the tests assert against that rather than against the sentinel.
    """
    from tesserae.temporal import _parse_iso

    return _parse_iso(valid_from) is not None


@pytest.mark.parametrize(
    "tool, rows_key", [("search_facts", "facts"), ("timeline", "events")]
)
def test_undated_included_counts_the_rows_returned_not_the_corpus(tmp_path, tool, rows_key):
    """``undated_included`` must describe the answer the caller was handed.

    The pivot runs before the query filter and the limit cap, so counting
    undated rows where the pivot runs counts them across the whole as-of
    corpus. With 40 undated off-query facts in the graph, a two-row
    "splatting" answer would report 41 undated — inverting the very judgement
    the counter exists to support ("how thin is this answer?") and reading as
    though every returned row were undated with 39 more besides.
    """
    path = as_of_graph_path(tmp_path, undated_noise=40)
    server = LLMWikiMCPServer(default_graph_path=path)

    result = server.call_tool(
        tool, {"query": "splatting", "limit": 50, "as_of": "2026-02-01"}
    )

    rows = result[rows_key]
    # Two rows survive the pivot AND the query: dated `old`, undated `undated`.
    assert {r["subject_id"] for r in rows} == {
        "SessionInsight:old",
        "SessionInsight:undated",
    }
    assert result["undated_included"] == 1
    # Stated as an invariant, so any future filter added between the pivot and
    # the response cannot drift the count away from the rows again.
    assert result["undated_included"] == sum(
        1 for r in rows if not _is_dated(r["valid_from"])
    )


def test_undated_included_agrees_with_facts_as_of_when_nothing_else_filters(tmp_path):
    """``facts_as_of`` owns what "undated" means; the response must not drift.

    Strip away the query filter, the limit cap and the budget and the two
    populations coincide, so the count the server reports has to equal the one
    the projector counted.

    What this does NOT do, stated because the docstring used to claim it did:
    it does not pin the *predicate*. Swapping the server's parseability check
    for a test against the literal "undated" sentinel leaves all nine tests in
    this module green — verified by mutation, not assumed. The divergence is
    unreachable anyway: ``temporal.py`` normalises a missing ``valid_from`` to
    the sentinel and ``_latest_ts`` only ever returns a parseable candidate, so
    no fact reaches here carrying an unparseable non-sentinel timestamp. The
    behaviour is correct; the coverage claim was not, and a false claim of
    coverage is worse than none because it stops anyone looking again.
    """
    from tesserae.mcp_server import load_graph
    from tesserae.temporal import TemporalFactProjector, facts_as_of

    path = as_of_graph_path(tmp_path, undated_noise=40)
    projected = TemporalFactProjector().project(load_graph(path))
    _, oracle = facts_as_of(projected, "2026-02-01")

    server = LLMWikiMCPServer(default_graph_path=path)
    everything = server.call_tool(
        "search_facts", {"query": "", "limit": 100, "as_of": "2026-02-01", "budget_chars": 0}
    )

    assert everything["total_matches"] == len(everything["facts"])  # nothing dropped
    assert everything["undated_included"] == oracle == 41


def test_undated_included_shrinks_with_the_limit(tmp_path):
    """The count follows the page, not the match set.

    Three populations differ here by construction: 41 undated rows survive the
    pivot, 1 undated row survives the query, and 0 undated rows survive a
    limit of 1 (dated `old` sorts first). Only a count taken over the returned
    rows reads 0.
    """
    path = as_of_graph_path(tmp_path, undated_noise=40)
    server = LLMWikiMCPServer(default_graph_path=path)

    page = server.call_tool(
        "search_facts", {"query": "splatting", "limit": 1, "as_of": "2026-02-01"}
    )

    assert [f["subject_id"] for f in page["facts"]] == ["SessionInsight:old"]
    assert page["total_matches"] == 2  # the query matched more than the page shows
    assert page["undated_included"] == 0


def test_undated_included_counts_rows_that_survived_the_budget(tmp_path):
    """CTX-01 truncation drops whole rows, and the count must drop with them.

    ``_fit_payload_list`` runs after the search, so a count taken any earlier
    describes rows that were dropped behind the continuation line and are not
    in the response at all.
    """
    server = LLMWikiMCPServer(default_graph_path=as_of_graph_path(tmp_path))
    query = {"query": "splatting", "as_of": "2026-02-01"}

    assert server.call_tool("search_facts", dict(query))["undated_included"] == 1

    # Sweep rather than pin one magic budget: the exact byte at which
    # ``fit_to_budget`` stops admitting rows is its business, and a test that
    # hard-codes it breaks on unrelated payload changes.
    dropped_somewhere = False
    for budget in (600, 700, 800, 1200, 2000):
        capped = server.call_tool("search_facts", {**query, "budget_chars": budget})
        assert capped["undated_included"] == sum(
            1 for f in capped["facts"] if not _is_dated(f["valid_from"])
        )
        dropped_somewhere |= capped.get("continuation") is not None
    # Guard against the sweep going vacuous if budgets stop biting.
    assert dropped_somewhere


def test_as_of_with_current_only_is_refused_rather_than_double_filtered(tmp_path):
    """The two filters ask different questions and must not silently compose.

    ``as_of`` asks what was live at the pivot; ``current_only`` asks what is
    still live now. Together they drop exactly the facts that were the state
    of knowledge at the pivot and have since been superseded — which is most
    of what a time-travel query is for. Answering "0 matches" to "what was
    true on 2026-02-01" because the answer was later replaced is the silent
    degradation this surface exists to remove, so the combination is refused
    with an error the caller can act on.
    """
    server = LLMWikiMCPServer(default_graph_path=as_of_graph_path(tmp_path))

    # `old` was the live state of knowledge in February and has since been
    # superseded, so it is precisely the fact the combination would eat.
    at_february = server.call_tool(
        "search_facts", {"query": "old splatting", "as_of": "2026-02-01"}
    )
    assert "SessionInsight:old" in {f["subject_id"] for f in at_february["facts"]}

    refused = server.call_tool(
        "search_facts",
        {"query": "old splatting", "as_of": "2026-02-01", "current_only": True},
    )

    assert "facts" not in refused  # not a quietly narrowed answer
    assert "as_of" in refused["error"] and "current_only" in refused["error"]


def test_search_facts_advertises_that_as_of_and_current_only_do_not_compose():
    by_name = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}

    current_only = by_name["search_facts"]["inputSchema"]["properties"]["current_only"]

    assert "as_of" in current_only["description"]


def test_search_facts_and_timeline_advertise_as_of():
    by_name = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}

    facts_schema = by_name["search_facts"]["inputSchema"]
    timeline_schema = by_name["timeline"]["inputSchema"]

    # Both refuse unknown keys, so an unadvertised `as_of` is rejected at the
    # schema boundary rather than silently ignored.
    assert facts_schema["additionalProperties"] is False
    assert timeline_schema["additionalProperties"] is False
    assert facts_schema["properties"]["as_of"]["type"] == "string"
    assert timeline_schema["properties"]["as_of"]["type"] == "string"
    # timeline's query is optional and must stay that way.
    assert facts_schema["required"] == ["query"]
    assert "required" not in timeline_schema


def test_search_facts_and_timeline_advertise_the_dated_filter():
    """An unadvertised argument is rejected by ``additionalProperties: False``,
    so the filter is only reachable if it is in the schema — and only usable if
    the enum names all three states."""
    by_name = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}

    for tool in ("search_facts", "timeline"):
        dated = by_name[tool]["inputSchema"]["properties"]["dated"]
        assert dated["enum"] == ["any", "dated", "undated"]
        assert dated["default"] == "any"


@pytest.mark.parametrize("tool, rows_key", [("search_facts", "facts"), ("timeline", "events")])
def test_dated_filter_partitions_the_corpus(tmp_path, tool, rows_key):
    """`dated` and `undated` are complements, not two independent narrowings:
    every row the unfiltered call returns lands in exactly one of them."""
    server = LLMWikiMCPServer(default_graph_path=as_of_graph_path(tmp_path))
    query = {"query": "splatting", "limit": 50}

    everything = server.call_tool(tool, query)
    dated = server.call_tool(tool, {**query, "dated": "dated"})
    undated = server.call_tool(tool, {**query, "dated": "undated"})

    all_ids = [row["id"] for row in everything[rows_key]]
    dated_ids = [row["id"] for row in dated[rows_key]]
    undated_ids = [row["id"] for row in undated[rows_key]]

    assert set(dated_ids) | set(undated_ids) == set(all_ids)
    assert not set(dated_ids) & set(undated_ids)
    assert undated_ids  # the fixture's undated finding, so this is not vacuous
    assert all(_is_dated(row["valid_from"]) for row in dated[rows_key])
    assert not any(_is_dated(row["valid_from"]) for row in undated[rows_key])


@pytest.mark.parametrize("tool", ["search_facts", "timeline"])
def test_unknown_dated_state_is_a_structured_error_not_a_silent_any(tmp_path, tool):
    server = LLMWikiMCPServer(default_graph_path=as_of_graph_path(tmp_path))

    result = server.call_tool(tool, {"query": "splatting", "dated": "undatd"})

    assert "error" in result and "undatd" in result["error"]
    # Crucially NOT the unfiltered corpus wearing a filter's label.
    assert "facts" not in result and "events" not in result


def test_timeline_undated_events_counts_the_rows_that_survived_the_budget(tmp_path):
    """The undated bucket is the TAIL of the sort, so CTX-01 truncation eats it
    first. A count taken before truncation describes rows that shipped behind
    the continuation line and are not in the response at all."""
    server = LLMWikiMCPServer(default_graph_path=as_of_graph_path(tmp_path))
    query = {"query": "splatting", "limit": 50}

    assert server.call_tool("timeline", query)["undated_events"] == 1

    dropped_somewhere = False
    for budget in (400, 600, 800, 1200, 2000):
        capped = server.call_tool("timeline", {**query, "budget_chars": budget})
        assert capped["undated_events"] == sum(
            1 for e in capped["events"] if not _is_dated(e["valid_from"])
        )
        dropped_somewhere |= capped.get("continuation") is not None
    assert dropped_somewhere


def test_search_facts_does_not_match_ids_or_metadata_through_the_tool(tmp_path):
    """The tool surface must not re-open what the fact corpus closed: a node-id
    fragment used to match every fact carrying that id, because the score was
    counted over ``json.dumps(fact.model_dump())``."""
    server = LLMWikiMCPServer(default_graph_path=as_of_graph_path(tmp_path))

    by_id_fragment = server.call_tool("search_facts", {"query": "SessionInsight", "limit": 50})
    by_content = server.call_tool("search_facts", {"query": "splatting", "limit": 50})

    assert by_id_fragment["total_matches"] == 0
    assert by_content["total_matches"] == 4


def test_json_rpc_notifications_do_not_emit_response():
    handler = MCPRequestHandler(LLMWikiMCPServer())

    assert handler.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) is None


def _seed_sqlite_graph_store(db_path):
    """Seed a SqliteGraphStore mirroring sample_graph_path content."""
    store = SqliteGraphStore(db_path)
    paper = ResearchNode(
        id="Paper:dual-splat",
        name="DualSplat",
        type=ResearchNodeType.PAPER,
        description="Robust Gaussian Splatting paper",
        metadata={"arxiv_id": "2601.17835"},
    )
    method = ResearchNode(
        id="MethodologicalConcept:gaussian-splatting",
        name="Gaussian Splatting",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        aliases=["3DGS"],
        description="Point-based 3D reconstruction method",
    )
    claim = ResearchNode(
        id="PerformanceClaim:best-shape",
        name="Best shape reconstruction claim",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="DualSplat reports strong shape reconstruction results",
    )
    for node in (paper, method, claim):
        store.upsert_node(node)
    store.upsert_edge(
        ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="uses Gaussian Splatting")
    )
    store.upsert_edge(
        ResearchEdge(source=paper.id, target=claim.id, type="supports_claim", evidence="reports best shape reconstruction")
    )
    return store


def test_resolve_graph_store_sqlite_url(tmp_path):
    """Sqlite URL resolves to SqliteGraphStore."""
    db = tmp_path / "g.db"
    store = resolve_graph_store(f"sqlite:///{db}")
    assert isinstance(store, SqliteGraphStore)


def test_resolve_graph_store_postgres_url_requires_hypepaper(monkeypatch):
    """Postgres URLs lazy-import the HypePaper backend.

    When the HypePaper backend is NOT importable (the Tesserae repo's
    standalone test environment), resolve_graph_store should raise
    ImportError with a clear message pointing at the HypePaper
    integration. When it IS importable, it should return a
    GraphStore-conforming wrapper.
    """
    import builtins
    import importlib

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        # Block the HypePaper-side imports so we exercise the ImportError branch
        if name.startswith("src.features.wiki") or name.startswith("src.core.database"):
            raise ImportError(f"No module named {name!r} (test stub)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # Force re-import of the resolver under the fake_import scope so the
    # lazy import inside resolve_graph_store triggers our stub.
    importlib.invalidate_caches()

    for url in (
        "postgresql://localhost/x",
        "postgres://localhost/x",
        "postgresql+asyncpg://localhost/x",
        "hypepaper-postgres://localhost/x",
    ):
        with pytest.raises(ImportError, match="HypePaper"):
            resolve_graph_store(url)


def test_resolve_graph_store_unknown_scheme_raises_valueerror():
    """Unknown schemes raise ValueError."""
    with pytest.raises(ValueError, match="Unsupported"):
        resolve_graph_store("redis://localhost/0")


def test_mcp_server_search_nodes_with_graph_store(tmp_path):
    """End-to-end: server backed by SqliteGraphStore returns search results."""
    db = tmp_path / "g.db"
    _seed_sqlite_graph_store(db)
    server = LLMWikiMCPServer(graph_store=SqliteGraphStore(db))

    result = server.call_tool("search_nodes", {"query": "Gaussian", "limit": 5})

    names = [node["name"] for node in result["nodes"]]
    assert "Gaussian Splatting" in names


def test_mcp_server_graph_summary_with_graph_store(tmp_path):
    """Graph summary works against a GraphStore-backed server."""
    db = tmp_path / "g.db"
    _seed_sqlite_graph_store(db)
    server = LLMWikiMCPServer(graph_store=SqliteGraphStore(db))

    summary = server.call_tool("graph_summary", {})

    assert summary["node_count"] == 3
    assert summary["edge_count"] == 2
    assert summary["node_types"]["Paper"] == 1
    assert summary["edge_types"]["uses"] == 1


def test_mcp_server_node_context_with_graph_store(tmp_path):
    """node_context resolves edges and neighbors via GraphStore."""
    db = tmp_path / "g.db"
    _seed_sqlite_graph_store(db)
    server = LLMWikiMCPServer(graph_store=SqliteGraphStore(db))

    context = server.call_tool("node_context", {"node_id": "Paper:dual-splat"})

    assert context["node"]["name"] == "DualSplat"
    assert {edge["type"] for edge in context["edges"]} == {"uses", "supports_claim"}
    assert {node["name"] for node in context["neighbors"]} == {"Gaussian Splatting", "Best shape reconstruction claim"}


def test_main_accepts_graph_store_url_flag(tmp_path, monkeypatch):
    """The CLI accepts --graph-store-url and resolves it without erroring before serve."""
    from tesserae import mcp_server as mcp_module

    db = tmp_path / "g.db"
    _seed_sqlite_graph_store(db)

    captured = {}

    def fake_serve(server, *args, **kwargs):
        captured["server"] = server

    monkeypatch.setattr(mcp_module, "serve_stdio", fake_serve)
    rc = mcp_module.main(["--graph-store-url", f"sqlite:///{db}"])
    assert rc == 0
    assert captured["server"].graph_store is not None
    assert isinstance(captured["server"].graph_store, SqliteGraphStore)


def test_main_auth_token_resolves_user_and_scopes_postgres_store(monkeypatch):
    """--auth-token resolves to a user_id and is forwarded to the resolver.

    Mocks both the HypePaper-side token lookup and the resolver so we
    exercise the CLI plumbing without needing the HypePaper backend
    importable in the Tesserae test environment.
    """
    from tesserae import mcp_server as mcp_module

    captured = {}

    # Stub the auth-token lookup to return a stable user_id.
    monkeypatch.setattr(
        mcp_module,
        "_resolve_auth_token_to_user_id",
        lambda token: "11111111-2222-3333-4444-555555555555",
    )

    # Stub the resolver to capture the owner_user_id keyword.
    def fake_resolve_graph_store(url, *, owner_user_id=None):
        captured["url"] = url
        captured["owner_user_id"] = owner_user_id
        # Return a sentinel so LLMWikiMCPServer accepts it.
        sentinel = object()
        return sentinel

    monkeypatch.setattr(
        "tesserae.graph_stores.url_resolver.resolve_graph_store",
        fake_resolve_graph_store,
    )

    def fake_serve(server, *args, **kwargs):
        captured["server"] = server

    monkeypatch.setattr(mcp_module, "serve_stdio", fake_serve)

    rc = mcp_module.main(
        [
            "--graph-store-url",
            "hypepaper-postgres://user:pw@localhost/hypepaper",
            "--auth-token",
            "tok_abc123",
        ]
    )

    assert rc == 0
    assert captured["url"] == "hypepaper-postgres://user:pw@localhost/hypepaper"
    assert captured["owner_user_id"] == "11111111-2222-3333-4444-555555555555"


def test_main_auth_token_rejects_invalid_token(monkeypatch):
    """When --auth-token is invalid, main exits with a clear RuntimeError."""
    from tesserae import mcp_server as mcp_module

    def fake_resolver(token):
        raise RuntimeError("Auth token is invalid, expired, or revoked.")

    monkeypatch.setattr(mcp_module, "_resolve_auth_token_to_user_id", fake_resolver)

    with pytest.raises(RuntimeError, match="invalid"):
        mcp_module.main(
            [
                "--graph-store-url",
                "hypepaper-postgres://localhost/x",
                "--auth-token",
                "bogus",
            ]
        )


# ---------------------------------------------------------------------------
# Modernized MCP surface: ontology-aware filters and code-graph exclusion
# ---------------------------------------------------------------------------


def _project_with_wiki_and_lint(tmp_path):
    """Build a tmp project with .tesserae/graph.json + a wiki page + lint-report.

    Mirrors the canonical layout (``<root>/.tesserae/...``) so the MCP
    server's project-root inference and filesystem-backed tools (wiki_page,
    raw_source, lint_report) all resolve correctly. Includes a ``Synthesis``
    node with both ``synthesizes`` and ``summarizes`` edges and a
    ``CodeFunction`` so we can assert it never surfaces in search results.
    """
    paper = ResearchNode(
        id="Paper:vision-paper",
        name="Vision Paper",
        type=ResearchNodeType.PAPER,
        description="A paper about computer vision.",
        metadata={"arxiv_id": "2026.00001", "title_quality": "verified"},
    )
    concept = ResearchNode(
        id="MethodologicalConcept:gaussian-splatting",
        name="Gaussian Splatting",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        description="3D reconstruction method.",
    )
    syn = ResearchNode(
        id="Synthesis:pulse:abc",
        name="Daily Pulse",
        type=ResearchNodeType.SYNTHESIS,
        description="Synthesis prose tying things together.",
        metadata={"synthesis_kind": "pulse"},
    )
    # Code-graph node — must never appear in MCP search results.
    code_fn = ResearchNode(
        id="CodeFunction:tesserae/example.py:vision_helper",
        name="vision_helper",
        type=ResearchNodeType.CODE_FUNCTION,
        description="Helper for the Vision Paper code path.",
    )
    graph = ResearchGraph(
        nodes=[paper, concept, syn, code_fn],
        edges=[
            ResearchEdge(source=paper.id, target=concept.id, type="uses"),
            ResearchEdge(source=syn.id, target=paper.id, type="synthesizes"),
            ResearchEdge(source=syn.id, target=concept.id, type="summarizes"),
        ],
    )
    project_root = tmp_path / "proj"
    wiki_dir = project_root / ".tesserae"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    graph_path = wiki_dir / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")

    # Render a minimal wiki page for the Paper.
    papers_dir = wiki_dir / "wiki" / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    (papers_dir / "vision-paper.md").write_text(
        "---\ntitle: Vision Paper\nkind: papers\nnode_id: Paper:vision-paper\n---\n"
        "# Vision Paper\n\nThis paper introduces [[Gaussian Splatting]] for 3D vision.\n"
        "See also [related work](concepts/gaussian-splatting.md) and https://arxiv.org/abs/2026.00001.\n",
        encoding="utf-8",
    )

    # And a wiki page for the Synthesis (to exercise wiki_page on Synthesis).
    syn_dir = wiki_dir / "wiki" / "syntheses"
    syn_dir.mkdir(parents=True, exist_ok=True)
    (syn_dir / "daily-pulse.md").write_text(
        "---\ntitle: Daily Pulse\nkind: syntheses\n---\n# Daily Pulse\n\nSummary body.\n",
        encoding="utf-8",
    )

    # And a raw source file behind the paper.
    src_dir = project_root / "data" / "research" / "weekly" / "2026-W18"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "raw.md").write_text("# Raw paper notes\n\nbody body body\n", encoding="utf-8")

    # And a lint report.
    (wiki_dir / "lint-report.md").write_text(
        "# Lint report\n\n## Summary\n\n- Total findings: 0\n",
        encoding="utf-8",
    )

    return project_root, graph_path


def test_search_nodes_honours_singular_type_filter(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool("search_nodes", {"type": "Paper", "q": "vision"})

    types = {node["type"] for node in result["nodes"]}
    assert types == {"Paper"}
    assert all("vision" in (node["name"] + node.get("description", "")).lower() for node in result["nodes"])


def test_search_nodes_honours_kind_filter(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool("search_nodes", {"kind": "syntheses"})

    types = {node["type"] for node in result["nodes"]}
    assert types == {"Synthesis"}


def test_search_nodes_excludes_code_graph_nodes_even_on_name_match(tmp_path):
    """CodeFunction must never surface, even when q matches its name verbatim.

    Search the FIXTURE graph directly. Going through ``call_tool`` would let the
    graph resolver pick the current-directory/registered project's graph over
    the fixture (pytest's tmp dir lives under the registered repo), so the
    assertions must target the graph we built, not the host's live graph.
    """
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    graph = server._load_graph_cached(graph_path)

    # Deterministic lexical lane: the query matches the CodeFunction's name
    # verbatim, yet it is filtered out of the candidate pool before search, and
    # no public node contains "vision_helper" as a substring -> zero matches.
    legacy = server.search_nodes(graph, query="vision_helper", mode="legacy")
    assert legacy["total_matches"] == 0
    assert all(node["type"] != "CodeFunction" for node in legacy["nodes"])

    # And under the default hybrid lane (which may semantically surface the
    # Vision *Paper*), the CodeFunction still never appears.
    hybrid = server.search_nodes(graph, query="vision_helper")
    assert all(node["type"] != "CodeFunction" for node in hybrid["nodes"])
    assert not any(n["id"].startswith("CodeFunction:") for n in hybrid["nodes"])


def test_graph_summary_excludes_code_graph_types(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    summary = server.call_tool("graph_summary", {})

    assert "CodeFunction" not in summary["node_types"]
    # Paper + Concept + Synthesis = 3 (CodeFunction filtered).
    assert summary["node_count"] == 3


def test_schema_omits_code_graph_types_and_lists_wiki_kinds(tmp_path):
    server = LLMWikiMCPServer()
    schema = server.call_tool("schema", {})

    for hidden in ("CodeProject", "SourceFile", "CodeClass", "CodeFunction", "CodeModule", "Dependency"):
        assert hidden not in schema["node_types"], f"{hidden} leaked into MCP schema"
    for public_type in ("Paper", "Repository", "Concept", "Synthesis", "OpenQuestion", "SourceDocument"):
        assert public_type in schema["node_types"]
    assert "wiki_kinds" in schema
    assert "papers" in schema["wiki_kinds"]
    assert "syntheses" in schema["wiki_kinds"]


def test_node_context_for_synthesis_returns_synthesizes_and_summarizes_edges(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    ctx = server.call_tool("node_context", {"node_id": "Synthesis:pulse:abc"})

    edge_types = {edge["type"] for edge in ctx["edges"]}
    assert {"synthesizes", "summarizes"}.issubset(edge_types)
    neighbour_names = {n["name"] for n in ctx["neighbors"]}
    assert {"Vision Paper", "Gaussian Splatting"}.issubset(neighbour_names)


def test_wiki_page_returns_body_and_internal_links(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    page = server.call_tool("wiki_page", {"node_id": "Paper:vision-paper"})

    assert page["kind"] == "papers"
    assert page["slug"] == "vision-paper"
    assert "Vision Paper" in page["body"]
    hrefs = {link["href"] for link in page["internal_links"]}
    assert "Gaussian Splatting" in hrefs  # wikilink
    assert any(link["kind"] == "wikilink" for link in page["internal_links"])
    # External https link must not be in internal_links.
    assert all(not link["href"].startswith("http") for link in page["internal_links"])


def test_wiki_page_unknown_node_id_raises_clear_error(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    with pytest.raises(ValueError, match="not found"):
        server.call_tool("wiki_page", {"node_id": "Paper:does-not-exist"})


def test_wiki_page_for_node_without_public_kind_raises(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    with pytest.raises(ValueError, match="no public wiki page|wiki_page"):
        server.call_tool(
            "wiki_page",
            {"node_id": "CodeFunction:tesserae/example.py:vision_helper"},
        )


def test_raw_source_returns_markdown_body(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    out = server.call_tool(
        "raw_source",
        {"source_path": "data/research/weekly/2026-W18/raw.md"},
    )

    assert "Raw paper notes" in out["body"]
    assert out["truncated"] is False
    assert out["byte_count"] > 0


def test_raw_source_refuses_binary_instead_of_returning_mojibake(tmp_path):
    """``errors="ignore"`` used to drop every non-UTF-8 byte and hand back the
    wreckage with no signal. Detection is by DECODE, not by extension."""
    project_root, graph_path = _project_with_wiki_and_lint(tmp_path)
    (project_root / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xd8binary")
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    with pytest.raises(ValueError, match="not UTF-8 text"):
        server.call_tool("raw_source", {"source_path": "figure.png"})

    # ...and a .svg, which IS text, still reads back fine — the old extension
    # allowlist would have refused it.
    (project_root / "diagram.svg").write_text("<svg>ø</svg>", encoding="utf-8")
    out = server.call_tool("raw_source", {"source_path": "diagram.svg"})
    assert out["body"] == "<svg>ø</svg>"


def test_raw_source_survives_a_multibyte_char_split_by_the_cap(tmp_path):
    """The cap can land mid-codepoint. A legitimate UTF-8 file must not be
    mistaken for binary just because byte 16384 is half a character."""
    from tesserae.mcp_server import RAW_SOURCE_BYTE_CAP

    project_root, graph_path = _project_with_wiki_and_lint(tmp_path)
    # "가" is 3 bytes: pad so the cap slices it, then keep writing past it.
    filler = "a" * (RAW_SOURCE_BYTE_CAP - 2)
    (project_root / "long.md").write_text(filler + "가" * 100, encoding="utf-8")
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    out = server.call_tool("raw_source", {"source_path": "long.md"})

    assert out["truncated"] is True
    assert out["body"].startswith("aaa")
    assert len(out["body"].encode("utf-8")) <= RAW_SOURCE_BYTE_CAP


def test_raw_source_rejects_path_escape(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    with pytest.raises(ValueError, match="escapes|outside|not found"):
        server.call_tool("raw_source", {"source_path": "../../../etc/passwd"})


def test_lint_report_returns_body_when_present(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    out = server.call_tool("lint_report", {})

    assert out["exists"] is True
    assert "Lint report" in out["body"]
    assert out["byte_count"] > 0


def test_lint_report_returns_empty_when_absent(tmp_path):
    """A project with no lint-report.md returns exists=False with empty body."""
    project_root, graph_path = _project_with_wiki_and_lint(tmp_path)
    (project_root / ".tesserae" / "lint-report.md").unlink()
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    out = server.call_tool("lint_report", {})

    assert out["exists"] is False
    assert out["body"] == ""
    assert out["byte_count"] == 0


def test_new_tools_listed_in_tool_registry():
    tools = LLMWikiMCPServer().list_tools()
    names = {tool["name"] for tool in tools}
    assert {"wiki_page", "raw_source", "lint_report"}.issubset(names)
