"""graph_write: append-only typed agent overlay replayed as a 5th producer.

The measured problem: the only way an agent could get a finding into the graph
was to write markdown and pay for an LLM extraction pass, and anything written
straight into ``graph.json`` was erased by the next compile. These tests pin
(a) the refusals — a typed write must never silently drop what an LLM compile
is allowed to drop — and (b) survival across every compile arm, which is the
part that is easy to claim and hard to keep.
"""

from __future__ import annotations

import concurrent.futures
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from tesserae.agent_write import (
    DENIED_NODE_TYPES,
    agent_anchor_id,
    record_agent_write,
    replay_agent_writes,
    validate_write,
)
from tesserae.llm_extractor import GraphJSONValidationError
from tesserae.project import ProjectWiki

WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"

AGENT = "test-agent"
PROV = {"agent": AGENT, "commit": "abc1234"}


def _payload(name="Retrieval Budgeting", edges=True):
    payload = {
        "nodes": [
            {
                "name": name,
                "type": "Concept",
                "description": "Bounding retrieval cost per query.",
            },
            {
                "name": "Budgeted retrieval beats global search on cost",
                "type": "Claim",
            },
        ],
        "provenance": dict(PROV),
    }
    if edges:
        payload["edges"] = [
            {
                "source": name,
                "target": "Budgeted retrieval beats global search on cost",
                "type": "supports_claim",
                "evidence": "331k tokens/query measured for MS GraphRAG global.",
            }
        ]
    return payload


def _seed_project(project_root: Path) -> ProjectWiki:
    project_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WIKI_CORPUS_ROOT / "data", project_root / "data")
    shutil.copytree(WIKI_CORPUS_ROOT / "docs", project_root / "docs")
    return ProjectWiki.init(project_root, name="agent_write_test")


def _graph(wiki: ProjectWiki) -> dict:
    return json.loads(wiki.paths.graph.read_text(encoding="utf-8"))


def _node_ids(wiki: ProjectWiki) -> set:
    return {n["id"] for n in _graph(wiki)["nodes"]}


def _written_ids(response) -> set:
    return {n["id"] for n in response["nodes"]}


# ---------------------------------------------------------------------------
# Refusals — nothing is silently dropped
# ---------------------------------------------------------------------------


def test_agent_write_rejects_unknown_edge_type():
    """``graph_from_llm_payload`` DROPS an unknown edge type and prints a note.

    Right for a 137-doc LLM compile, wrong for a typed API: a dropped edge is a
    silent lie about what was recorded.
    """
    payload = _payload()
    payload["edges"][0]["type"] = "used_by"
    with pytest.raises(GraphJSONValidationError) as exc:
        validate_write(payload, AGENT)
    assert "used_by" in str(exc.value)


def test_agent_write_rejects_edge_without_evidence():
    payload = _payload()
    payload["edges"][0]["evidence"] = "   "
    with pytest.raises(GraphJSONValidationError) as exc:
        validate_write(payload, AGENT)
    assert "evidence" in str(exc.value)


def test_agent_write_rejects_edge_endpoint_not_in_payload():
    payload = _payload()
    payload["edges"][0]["target"] = "A node nobody defined"
    with pytest.raises(GraphJSONValidationError):
        validate_write(payload, AGENT)


def test_agent_write_rejects_provenance_without_external_anchor():
    payload = _payload()
    payload["provenance"] = {"agent": AGENT}
    with pytest.raises(GraphJSONValidationError) as exc:
        validate_write(payload, AGENT)
    assert "external anchor" in str(exc.value)
    payload["provenance"] = {"agent": AGENT, "commit": "abc1234"}
    assert validate_write(payload, AGENT).write_id


@pytest.mark.parametrize("denied", ["Session", "CodeFile", "CommunitySummary", "Agent"])
def test_agent_write_rejects_producer_owned_node_type(denied):
    """No agent-written id may collide with a ``__producer__``-owned id."""
    assert denied in DENIED_NODE_TYPES
    payload = _payload(edges=False)
    payload["nodes"][0]["type"] = denied
    with pytest.raises(GraphJSONValidationError) as exc:
        validate_write(payload, AGENT)
    assert "producer" in str(exc.value)


def test_agent_write_rejects_unknown_node_type():
    payload = _payload(edges=False)
    payload["nodes"][0]["type"] = "Vibe"
    with pytest.raises(GraphJSONValidationError):
        validate_write(payload, AGENT)


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def test_agent_write_is_idempotent(tmp_path):
    jsonl = tmp_path / "agent-writes.jsonl"
    first = record_agent_write(jsonl, _payload(), AGENT)
    second = record_agent_write(jsonl, _payload(), AGENT)
    assert first["write_id"] == second["write_id"]
    assert first["status"] == "recorded"
    assert second["status"] == "duplicate"
    assert len(jsonl.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_agent_write_id_excludes_wall_clock(tmp_path):
    a = validate_write(_payload(), AGENT).as_record(written_at="2026-01-01T00:00:00Z")
    b = validate_write(_payload(), AGENT).as_record(written_at="2026-07-25T12:00:00Z")
    assert a["write_id"] == b["write_id"]
    assert a["written_at"] != b["written_at"]


def test_agent_write_reports_existing_refs(tmp_path):
    """``existing: false`` is the cheap entity-resolution guard an agent needs."""
    from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

    known_node = ResearchNode(
        id="Concept:already-there",
        name="Retrieval Budgeting",
        type=ResearchNodeType.CONCEPT,
    )
    graph = ResearchGraph(nodes=[known_node], edges=[])
    jsonl = tmp_path / "agent-writes.jsonl"
    result = record_agent_write(jsonl, _payload(), AGENT, graph=graph)
    by_name = {n["name"]: n for n in result["nodes"]}
    assert by_name["Retrieval Budgeting"]["existing"] is True
    # The reported id is the one the write will actually LAND on, not a fork.
    assert by_name["Retrieval Budgeting"]["id"] == known_node.id
    assert by_name["Budgeted retrieval beats global search on cost"]["existing"] is False


def test_agent_write_anchors_on_the_session_graph_agent_node(tmp_path):
    """The Agent anchor id must equal the one the session graph already mints."""
    from tesserae.session_graph_structural import _agent_pseudo

    jsonl = tmp_path / "agent-writes.jsonl"
    result = record_agent_write(jsonl, _payload(), AGENT)
    assert result["agent_node_id"] == _agent_pseudo(AGENT).id == agent_anchor_id(AGENT)
    overlay = replay_agent_writes(jsonl)
    assert any(n.id == result["agent_node_id"] for n in overlay.nodes)
    assert any(
        e.type == "performed_by" and e.target == result["agent_node_id"]
        for e in overlay.edges
    )


def test_agent_write_replay_order_independent(tmp_path):
    """Two agents appending in either interleaving must converge byte-identically."""
    a = _payload("Alpha Concept")
    b = _payload("Beta Concept")

    one = tmp_path / "one.jsonl"
    record_agent_write(one, a, AGENT)
    record_agent_write(one, b, AGENT)
    two = tmp_path / "two.jsonl"
    record_agent_write(two, b, AGENT)
    record_agent_write(two, a, AGENT)
    assert one.read_bytes() != two.read_bytes()  # append order genuinely differs

    def canon(path):
        graph = replay_agent_writes(path)
        return json.dumps(
            {
                "nodes": [n.model_dump() for n in graph.nodes],
                "edges": [e.model_dump() for e in graph.edges],
            },
            sort_keys=True,
            default=str,
        )

    assert canon(one) == canon(two)


def test_agent_write_concurrent_appends(tmp_path):
    jsonl = tmp_path / "agent-writes.jsonl"

    def write(i):
        return record_agent_write(jsonl, _payload(f"Concept {i}"), AGENT)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(8)))

    lines = [
        json.loads(line)
        for line in jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 8
    ids = {r["write_id"] for r in lines}
    assert len(ids) == 8
    assert ids == {r["write_id"] for r in results}


# ---------------------------------------------------------------------------
# Compile integration — the part that is easy to claim and hard to keep
# ---------------------------------------------------------------------------


def _write_and_compile(tmp_path, *, payload=None):
    wiki = _seed_project(tmp_path / "project")
    wiki.compile()
    response = record_agent_write(
        wiki.paths.agent_writes, payload or _payload(), AGENT
    )
    wiki.compile()
    return wiki, response


def test_agent_write_appears_after_compile(tmp_path):
    wiki, response = _write_and_compile(tmp_path)
    assert _written_ids(response) <= _node_ids(wiki)
    edges = {(e["source"], e["type"], e["target"]) for e in _graph(wiki)["edges"]}
    subject, obj = response["nodes"][0]["id"], response["nodes"][1]["id"]
    assert (subject, "supports_claim", obj) in edges


def test_agent_write_recompile_is_byte_idempotent(tmp_path):
    wiki, _response = _write_and_compile(tmp_path)
    first = wiki.paths.graph.read_bytes()
    wiki.compile()
    assert wiki.paths.graph.read_bytes() == first


def test_agent_write_survives_changed_only_noop_compile(tmp_path):
    """The overlay must merge OUTSIDE the batch path, so the no-op arm sees it."""
    wiki = _seed_project(tmp_path / "project")
    wiki.compile()
    response = record_agent_write(wiki.paths.agent_writes, _payload(), AGENT)
    report = wiki.compile(changed_only=True)
    assert report.get("processed", 0) == 0, "corpus was unchanged; expected the no-op arm"
    assert _written_ids(response) <= _node_ids(wiki)


def test_agent_write_survives_incremental_differ(tmp_path):
    wiki = _seed_project(tmp_path / "project")
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["incremental_compile"] = True
    wiki.paths.config.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    wiki.compile()
    response = record_agent_write(wiki.paths.agent_writes, _payload(), AGENT)
    wiki.compile()
    target = next(iter((wiki.project_root / "docs").glob("*.md")))
    target.write_text("# Edited\n\nA tracked file changed.\n", encoding="utf-8")
    wiki.compile(changed_only=True, changed_paths=[target])
    assert _written_ids(response) <= _node_ids(wiki), (
        "the incremental differ tombstoned agent-written nodes — they have no "
        "markdown owner, so they must be excluded as producer-owned"
    )


def test_agent_write_provenance_rows_survive_full_then_incremental(tmp_path):
    """Codex #6 shape: no ``__agent_write__`` rows ⇒ the reconcile strips them
    and every later incremental compile silently degrades."""
    wiki, response = _write_and_compile(tmp_path)
    written = _written_ids(response)

    def rows():
        with sqlite3.connect(str(wiki.paths.sqlite)) as con:
            return {
                node_id
                for (node_id,) in con.execute(
                    "select node_id from node_provenance where source_path = ?",
                    ("__agent_write__",),
                ).fetchall()
            }

    assert written <= rows()
    wiki.compile(changed_only=True)
    assert written <= rows()
    wiki.compile()
    assert written <= rows()


def test_agent_write_not_pruned_as_manifest_orphan(tmp_path):
    """Agent nodes have no manifest entry and no ``graphed`` stamp."""
    wiki, response = _write_and_compile(tmp_path)
    manifest = json.loads(wiki.paths.manifest.read_text(encoding="utf-8"))
    entries = manifest.get("documents", manifest)
    assert not any("Retrieval Budgeting" in str(k) for k in entries)
    wiki.compile(changed_only=True)
    assert _written_ids(response) <= _node_ids(wiki)


def test_agent_write_extraction_wins_payload_conflict(tmp_path):
    """An agent must not rename a curated node out from under extraction.

    ``merge_graphs([graph, overlay])`` puts extraction FIRST, so
    ``prefer_research_node``'s ``chosen = existing`` default keeps the
    extracted display name. The agent's lowercase spelling hashes to the same
    stable id, so it must merge onto the curated node — not fork a second one.
    """
    wiki = _seed_project(tmp_path / "project")
    wiki.compile()
    extracted = next(n for n in _graph(wiki)["nodes"] if n["type"] == "Paper")
    assert extracted["name"] != extracted["name"].lower()

    payload = {
        "nodes": [
            {
                "name": extracted["name"].lower(),
                "type": "Paper",
                "description": "AGENT OVERWRITE ATTEMPT",
            }
        ],
        "provenance": dict(PROV),
    }
    record_agent_write(wiki.paths.agent_writes, payload, AGENT)
    wiki.compile()
    papers = [n for n in _graph(wiki)["nodes"] if n["type"] == "Paper"]
    assert len(papers) == 1, "the agent write forked a duplicate curated node"
    assert papers[0]["id"] == extracted["id"]
    assert papers[0]["name"] == extracted["name"]


def test_agent_writes_jsonl_is_not_a_compile_output(tmp_path):
    """The overlay is an INPUT — it must stay out of every output-hash scope."""
    from tesserae import output_snapshot

    allowlisted = set(output_snapshot.GRAPH_LAYER_FILES) | set(
        output_snapshot.PROJECTION_LAYER_DIRS
    )
    assert "agent-writes.jsonl" not in allowlisted


# ---------------------------------------------------------------------------
# MCP surface
# ---------------------------------------------------------------------------


def test_graph_write_listed_in_list_tools():
    from tesserae.mcp_server import LLMWikiMCPServer

    tools = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}
    assert "graph_write" in tools
    schema = tools["graph_write"]["inputSchema"]
    assert set(schema["required"]) == {"nodes", "agent", "provenance"}
    assert schema["properties"]["materialize"]["default"] is False


def test_graph_write_dispatches_through_call_tool(tmp_path):
    from tesserae.mcp_server import LLMWikiMCPServer

    wiki = _seed_project(tmp_path / "project")
    wiki.compile()
    server = LLMWikiMCPServer(default_graph_path=wiki.paths.graph)
    payload = _payload()
    result = server.call_tool(
        "graph_write",
        {
            "graph_path": str(wiki.paths.graph),
            "agent": AGENT,
            "nodes": payload["nodes"],
            "edges": payload["edges"],
            "provenance": payload["provenance"],
        },
    )
    assert result["status"] == "recorded"
    assert result["materialized"] is False
    assert wiki.paths.agent_writes.exists()
    wiki.compile()
    assert _written_ids(result) <= _node_ids(wiki)


# ---------------------------------------------------------------------------
# Adversarial review fixes: determinism P1 and write-safety P2–P5
# ---------------------------------------------------------------------------


def test_performed_by_evidence_is_a_pure_function_of_the_finding(tmp_path):
    """determinism P1: the write id must NOT ride in the edge evidence.

    Two writes whose nodes later collapse (cross-type dedup fuses ``Claim:x``
    into ``Paper:x``) mint two ``performed_by`` edges that then collide on
    ``(source, type, target)``. If their evidence differed, the survivor would
    be picked by merge-input order — which differs between a full and an
    incremental compile — so ``graph.json`` oscillated forever and never
    converged.
    """
    jsonl = tmp_path / "agent-writes.jsonl"
    first = record_agent_write(jsonl, _payload("Alpha Concept"), AGENT)
    second = record_agent_write(jsonl, _payload("Beta Concept"), AGENT)
    assert first["write_id"] != second["write_id"]

    overlay = replay_agent_writes(jsonl)
    evidence = {e.evidence for e in overlay.edges if e.type == "performed_by"}
    assert evidence == {f"written by agent {AGENT}"}
    for write_id in (first["write_id"], second["write_id"]):
        assert not any(write_id in text for text in evidence)


def test_full_and_incremental_compile_agree_with_an_overlay(tmp_path):
    """determinism P1, end to end: alternating compile arms must not oscillate."""
    wiki = _seed_project(tmp_path / "project")
    # "Vision Banana" written once as a Paper and once as a Claim: the compile's
    # cross-type dedup collapses them, colliding their two performed_by edges.
    record_agent_write(
        wiki.paths.agent_writes,
        {
            "nodes": [{"name": "Vision Banana", "type": "Paper"}],
            "provenance": dict(PROV),
        },
        AGENT,
    )
    record_agent_write(
        wiki.paths.agent_writes,
        {
            "nodes": [{"name": "Vision Banana", "type": "Claim"}],
            "provenance": dict(PROV),
        },
        AGENT,
    )
    digests = []
    for changed_only in (False, True, False, True):
        wiki.compile(changed_only=changed_only)
        digests.append(wiki.paths.graph.read_bytes())
    assert len(set(digests)) == 1


def test_align_refuses_node_types_the_compile_refuses_to_merge(tmp_path):
    """write-safety P2: alignment must honour the dedup pass's own exemptions.

    ``_merge_same_type_aliased_duplicates`` never collapses two same-text
    ``SessionInsight`` nodes — "merging them loses the link back to which
    session produced each one". ``resolve_existing_id`` applied the key anyway,
    so an agent's independent observation was fused onto (and erased by) a
    session's finding that merely shared wording.
    """
    from tesserae.agent_write import align_overlay
    from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

    victim = ResearchNode(
        id="SessionInsight:sess-abc-0001",
        name="Retry the transient, name the real cause",
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": "sess-abc", "extractor": "session-structural"},
    )
    base = ResearchGraph(nodes=[victim], edges=[])
    jsonl = tmp_path / "agent-writes.jsonl"
    response = record_agent_write(
        jsonl,
        {
            "nodes": [
                {
                    "name": "retry the transient; name the REAL cause!",
                    "type": "SessionInsight",
                    "description": "the agent's own, unrelated observation",
                }
            ],
            "provenance": dict(PROV),
        },
        AGENT,
        graph=base,
    )
    assert response["nodes"][0]["id"] != victim.id
    assert response["nodes"][0]["existing"] is False

    overlay = align_overlay(replay_agent_writes(jsonl), base)
    assert victim.id not in {n.id for n in overlay.nodes}
    # ... and no performed_by edge was grafted onto the session's finding.
    assert not any(e.source == victim.id for e in overlay.edges)


def test_aligned_write_keeps_agent_provenance_and_alias(tmp_path):
    """write-safety P3: attribution must survive alignment.

    The module's central claim is that provenance travels ON the node. Dropping
    redirected nodes wholesale made that false on exactly the ``existing: true``
    path the API advertises as the good outcome.
    """
    from tesserae.agent_write import align_overlay
    from tesserae.batch import merge_graphs
    from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

    curated = ResearchNode(
        id="Concept:retrieval-budgeting:curated",
        name="Retrieval Budgeting",
        type=ResearchNodeType.CONCEPT,
        metadata={"extractor": "llm"},
    )
    base = ResearchGraph(nodes=[curated], edges=[])
    jsonl = tmp_path / "agent-writes.jsonl"
    response = record_agent_write(
        jsonl,
        {
            "nodes": [{"name": "retrieval-budgeting!!", "type": "Concept"}],
            "provenance": dict(PROV),
        },
        AGENT,
        graph=base,
    )
    assert response["nodes"][0]["id"] == curated.id

    merged = merge_graphs([base, align_overlay(replay_agent_writes(jsonl), base)])
    node = next(n for n in merged.nodes if n.id == curated.id)
    assert node.name == curated.name  # extraction still wins the display name
    assert node.metadata["agent_write_id"] == response["write_id"]
    assert node.metadata["agent_key"] == AGENT
    assert node.metadata["agent_write_provenance"] == PROV
    # ... and the loser's spelling is aliased, not discarded, exactly as
    # ``_merge_same_type_aliased_duplicates`` does with ``aliases_to_add``.
    assert "retrieval-budgeting!!" in node.aliases


def test_aligned_provenance_is_order_free(tmp_path):
    """Two writes onto one curated node must resolve identically either way."""
    from tesserae.agent_write import align_overlay
    from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

    curated = ResearchNode(
        id="Concept:retrieval-budgeting:curated",
        name="Retrieval Budgeting",
        type=ResearchNodeType.CONCEPT,
    )
    base = ResearchGraph(nodes=[curated], edges=[])

    def build(order):
        jsonl = tmp_path / f"{'-'.join(order)}.jsonl"
        for spelling in order:
            record_agent_write(
                jsonl,
                {
                    "nodes": [{"name": spelling, "type": "Concept"}],
                    "provenance": dict(PROV),
                },
                AGENT,
                graph=base,
            )
        overlay = align_overlay(replay_agent_writes(jsonl), base)
        node = next(n for n in overlay.nodes if n.id == curated.id)
        return node.metadata["agent_write_id"], tuple(node.aliases)

    assert build(["retrieval budgeting", "RETRIEVAL-BUDGETING"]) == build(
        ["RETRIEVAL-BUDGETING", "retrieval budgeting"]
    )


def test_malformed_jsonl_line_never_bricks_the_corpus(tmp_path, capsys):
    """write-safety P5: one bad line must not take every future compile down."""
    wiki = _seed_project(tmp_path / "project")
    wiki.compile()
    record_agent_write(wiki.paths.agent_writes, _payload("Concept A"), AGENT)
    record_agent_write(wiki.paths.agent_writes, _payload("Concept B"), AGENT)

    lines = wiki.paths.agent_writes.read_text(encoding="utf-8").splitlines()
    truncated = lines[:-1] + [lines[-1][: len(lines[-1]) // 2]]
    wiki.paths.agent_writes.write_text("\n".join(truncated) + "\n", encoding="utf-8")

    overlay = replay_agent_writes(wiki.paths.agent_writes)
    assert any(n.name == "Concept A" for n in overlay.nodes)  # good line survives
    assert "unreadable agent-write line" in capsys.readouterr().err
    wiki.compile()  # must not raise
    assert any(n["name"] == "Concept A" for n in _graph(wiki)["nodes"])


def test_hand_written_unusable_record_is_skipped_not_raised(tmp_path, capsys):
    """Same seam: a record the builder rejects is dropped with a warning."""
    jsonl = tmp_path / "agent-writes.jsonl"
    good = record_agent_write(jsonl, _payload("Concept A"), AGENT)
    with jsonl.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "write_id": "zzzzhandedited",
                    "agent": AGENT,
                    "nodes": [{"name": "Solo", "type": "Concept"}],
                    "edges": [
                        {
                            "source": "Solo",
                            "target": "Nowhere",
                            "type": "supports_claim",
                            "evidence": "e",
                        }
                    ],
                    "provenance": dict(PROV),
                }
            )
            + "\n"
        )
    overlay = replay_agent_writes(jsonl)
    assert any(n.name == "Concept A" for n in overlay.nodes)
    assert not any(n.name == "Solo" for n in overlay.nodes)
    assert "unusable agent write zzzzhandedited" in capsys.readouterr().err
    assert good["status"] == "recorded"


def test_returned_id_is_flagged_provisional_and_stays_resolvable(tmp_path):
    """write-safety P4: a minted id is a prediction, so say so and offer a fix.

    The agent writes a Paper by title before extraction has seen it; extraction
    then seeds the id from the arXiv id and the returned id dereferences
    nothing. ``write_id`` is the durable handle ``resolve_write_nodes`` reads.
    """
    from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
    from tesserae.agent_write import resolve_write_nodes

    seeded = _seed_project(tmp_path / "seed")
    seeded.compile()
    title = next(
        n["name"] for n in _graph(seeded)["nodes"] if n["type"] == "Paper"
    )

    wiki = _seed_project(tmp_path / "project")
    response = record_agent_write(
        wiki.paths.agent_writes,
        {"nodes": [{"name": title, "type": "Paper"}], "provenance": dict(PROV)},
        AGENT,
    )
    assert response["nodes"][0]["provisional"] is True
    stale = response["nodes"][0]["id"]

    wiki.compile()
    live = _graph(wiki)
    assert stale not in {n["id"] for n in live["nodes"]}  # the id really moved

    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id=n["id"],
                name=n["name"],
                type=ResearchNodeType(n["type"]),
                aliases=n.get("aliases") or [],
            )
            for n in live["nodes"]
        ],
        edges=[],
    )
    resolved = resolve_write_nodes(
        wiki.paths.agent_writes, response["write_id"], graph
    )
    assert resolved is not None
    assert resolved[0]["provisional"] is False
    assert resolved[0]["id"] in {n["id"] for n in live["nodes"]}
    # An unknown write answers NOT_FOUND rather than guessing.
    assert resolve_write_nodes(wiki.paths.agent_writes, "deadbeef", graph) is None
