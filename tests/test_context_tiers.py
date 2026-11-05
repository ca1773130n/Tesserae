"""Progressive-disclosure tiers (R7b): L0/L1 extraction, the sidecar, and
``compile_context(tiers=True)``.

What is asserted is structure — caps, determinism, byte-identity of the
default path, that tail lines carry ids and respect the budget. Whether a
tiered bundle answers as well as a whole-body one is NOT asserted anywhere:
that needs the LME-S / 148-paper accuracy-vs-tokens sweep, which has not been
run, and is why tiers ship off by default.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import shutil
from pathlib import Path

import pytest

from tesserae import context_tiers
from tesserae.context_compiler import compile_context
from tesserae.context_tiers import (
    L0_MAX_CHARS,
    L1_MAX_CHARS,
    content_key,
    l0_line,
    l1_overview,
    render_sidecar,
    tier_lookup,
)
from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

_WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"


def _node(i: int, words: int = 120) -> ResearchNode:
    body = " ".join(f"word{j}" for j in range(words))
    return ResearchNode(
        id=f"Concept:c{i:02d}",
        name=f"Splat concept {i}",
        type=ResearchNodeType.CONCEPT,
        description=f"Splat concept {i} is about gaussian splatting. {body}.",
    )


def _graph(n: int = 30) -> ResearchGraph:
    nodes = [_node(i) for i in range(n)]
    edges = [
        ResearchEdge(source=nodes[0].id, target=nodes[i].id, type="shares_concept_with")
        for i in range(1, n)
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_l0_is_one_short_line_and_l1_a_bounded_overview() -> None:
    node = _node(3, words=400)
    l0 = l0_line(node)
    assert len(l0) <= L0_MAX_CHARS
    assert l0.startswith("Splat concept 3")
    assert "\n" not in l0
    l1 = l1_overview(node)
    assert len(l1) <= L1_MAX_CHARS
    assert l1.startswith("Splat concept 3 is about gaussian splatting.")


def test_a_node_without_a_description_still_gets_a_line() -> None:
    node = ResearchNode(id="Concept:bare", name="Bare", type=ResearchNodeType.CONCEPT)
    assert l0_line(node) == "Bare"
    assert l1_overview(node) == "Bare"


def test_the_sidecar_is_keyed_on_content_and_deterministic() -> None:
    graph = _graph(5)
    first = render_sidecar(graph)
    assert render_sidecar(graph) == first
    entries = json.loads(first)["entries"]
    assert set(entries) == {content_key(n) for n in graph.nodes}
    edited = dataclasses.replace(graph.nodes[0], description="changed")
    assert content_key(edited) != content_key(graph.nodes[0])


def test_lookup_reads_the_sidecar_and_recomputes_misses(tmp_path: Path) -> None:
    graph = _graph(3)
    (tmp_path / ".tesserae").mkdir()
    payload = json.loads(render_sidecar(graph))
    payload["entries"][content_key(graph.nodes[0])]["l0"] = "from the sidecar"
    (tmp_path / ".tesserae" / "context_tiers.json").write_text(json.dumps(payload))
    lookup, source = tier_lookup(tmp_path)
    assert source == "sidecar"
    assert lookup(graph.nodes[0])["l0"] == "from the sidecar"
    stranger = _node(99)
    assert lookup(stranger)["l0"] == l0_line(stranger)


def test_a_damaged_sidecar_is_logged_and_recomputed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / ".tesserae").mkdir()
    (tmp_path / ".tesserae" / "context_tiers.json").write_text("{not json")
    with caplog.at_level(logging.WARNING, logger="tesserae.context_tiers"):
        _lookup, source = tier_lookup(tmp_path)
    assert source == "computed"
    assert any("unreadable" in r.getMessage() for r in caplog.records)


def test_tiers_off_is_the_default_bundle() -> None:
    graph = _graph()
    default = compile_context(graph, query="splat", budget=4_000)
    explicit = compile_context(graph, query="splat", budget=4_000, tiers=False)
    assert explicit == default
    assert default.tail_nodes == [] and default.tokens_by_layer is None


@pytest.mark.parametrize("budget", [1_500, 4_000, 12_000])
def test_tiered_bundle_packs_full_heads_and_an_l0_tail_within_budget(budget: int) -> None:
    graph = _graph()
    whole = compile_context(graph, query="splat", budget=budget)
    tiered = compile_context(graph, query="splat", budget=budget, tiers=True, tier_full_ranks=2)
    assert len(tiered.selected_nodes) <= 2
    assert tiered.char_budget_used <= budget
    assert not set(tiered.selected_nodes) & set(tiered.tail_nodes)
    # Heads then tail follow the ranking, with nothing skipped in between.
    order = tiered.selected_nodes + tiered.tail_nodes
    assert order == [n for n in tiered.ranked_nodes if n in set(order)][: len(order)]
    for nid in tiered.tail_nodes:
        assert f"`{nid}`" in tiered.body
    # Same budget, more distinct nodes in front of the reader.
    assert len(order) >= len(whole.selected_nodes)
    assert set(tiered.tokens_by_layer) == {"full", "l0"}
    assert tiered.tokens_by_layer["l0"] > 0


def test_a_body_that_does_not_fit_becomes_a_line() -> None:
    graph = _graph()
    tiered = compile_context(graph, query="splat", budget=1_000, tiers=True, tier_full_ranks=50)
    assert tiered.tail_nodes, "the walk stopped instead of switching to L0 lines"
    assert tiered.char_budget_used <= 1_000


def _mcp_server(tmp_path: Path) -> LLMWikiMCPServer:
    tess = tmp_path / ".tesserae"
    tess.mkdir(parents=True, exist_ok=True)
    graph_path = tess / "graph.json"
    graph_path.write_text(_graph().to_json(indent=2), encoding="utf-8")
    return LLMWikiMCPServer(default_graph_path=graph_path)


def test_mcp_compile_context_reports_tiers_only_when_asked(tmp_path: Path) -> None:
    server = _mcp_server(tmp_path)
    plain = server.call_tool("compile_context", {"query": "splat", "budget": 3_000})
    for key in ("tail_node_ids", "tokens_by_layer", "tier_source"):
        assert key not in plain
    assert "tiers" not in plain["knobs"]
    tiered = server.call_tool(
        "compile_context", {"query": "splat", "budget": 3_000, "tiers": True, "tier_full_ranks": 1}
    )
    assert tiered["tier_source"] == "computed"
    assert tiered["tail_node_ids"]
    assert len(tiered["selected_node_ids"]) <= 1
    assert tiered["knobs"]["tiers"] == {"tier_full_ranks": 1}


def test_compile_writes_the_sidecar_only_when_opted_in_and_leaves_graph_json_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tesserae.project import ProjectWiki

    monkeypatch.setenv("TESSERAE_COMMUNITY_SUMMARIES", "false")
    root = tmp_path / "project"
    root.mkdir()
    shutil.copytree(_WIKI_CORPUS_ROOT / "data", root / "data")
    shutil.copytree(_WIKI_CORPUS_ROOT / "docs", root / "docs")
    wiki = ProjectWiki.init(root, name="tiers_test")
    sidecar = wiki.paths.graph.parent / context_tiers.SIDECAR_NAME

    monkeypatch.delenv(context_tiers.CONTEXT_TIERS_ENV, raising=False)
    wiki.compile()
    assert not sidecar.exists()
    graph_bytes = wiki.paths.graph.read_bytes()

    monkeypatch.setenv(context_tiers.CONTEXT_TIERS_ENV, "1")
    wiki.compile()
    assert sidecar.exists()
    assert wiki.paths.graph.read_bytes() == graph_bytes
    first = sidecar.read_bytes()
    wiki.compile()
    assert sidecar.read_bytes() == first
