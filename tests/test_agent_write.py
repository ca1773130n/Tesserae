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
