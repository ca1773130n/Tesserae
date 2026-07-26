"""Tests for ``tesserae.memory.contrast`` — the reasoning-edge pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.memory.contrast import (
    CLAIM_BLOCK,
    candidate_pairs,
    contrast_pass_enabled,
    run_contrast_pass,
)
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


# --------------------------------------------------------------------- helpers


class FakeClient:
    """Counting fake ``LLMJsonClient``. ``responses`` may be a dict or a constant."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def complete_json(self, *, system, user, schema_name, cache_key=None, max_retries=2):
        self.calls.append(user)
        if callable(self._response):
            return self._response(user)
        return self._response


def _claim(node_id: str, name: str, description: str = "") -> ResearchNode:
    return ResearchNode(
        id=node_id,
        name=name,
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description=description,
    )


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setenv("TESSERAE_CONTRAST_PASS", "1")
    monkeypatch.delenv("TESSERAE_CONTRAST_MAX_PAIRS", raising=False)


# ------------------------------------------------------------------- blocking


def test_contrast_blocking_drops_common_tokens():
    """The df cap, not min_shared alone, is what prevents the blowup.

    Three claims all say "model". Only two of them additionally share three
    rare tokens, so exactly one candidate pair must survive.
    """
    shared_rare = "quantised kv-cache throughput"
    nodes = [
        _claim("Claim:a", f"model {shared_rare} rises"),
        _claim("Claim:b", f"model {shared_rare} falls"),
    ]
    # Pad the block with claims that ONLY share the common word, so "model"
    # crosses the df cap and cannot by itself pair anything.
    nodes += [
        _claim(f"Claim:pad{i}", f"model note number{i} alpha{i} beta{i}")
        for i in range(60)
    ]
    graph = ResearchGraph(nodes=nodes, edges=[])

    pairs = candidate_pairs(graph, block=CLAIM_BLOCK)

    assert [(p.lo_id, p.hi_id) for p in pairs] == [("Claim:a", "Claim:b")]
    assert pairs[0].shared == 3


def test_contrast_candidate_rank_is_order_independent():
    """Ranking + budget cut must not depend on input node-list order."""
    nodes = []
    for i in range(50):
        nodes.append(_claim(f"Claim:x{i}", f"alpha{i} bravo{i} charlie{i} delta{i}"))
        nodes.append(_claim(f"Claim:y{i}", f"alpha{i} bravo{i} charlie{i} echo{i}"))
    forward = candidate_pairs(ResearchGraph(nodes=list(nodes), edges=[]), block=CLAIM_BLOCK)
    reverse = candidate_pairs(
        ResearchGraph(nodes=list(reversed(nodes)), edges=[]), block=CLAIM_BLOCK
    )

    assert len(forward) == 50
    assert forward[:5] == reverse[:5]
    assert forward == reverse


# ---------------------------------------------------------------------- minting


def _pair_graph():
    a = _claim("Claim:a", "quantised kv-cache throughput rises", "throughput improves")
    b = _claim("Claim:b", "quantised kv-cache throughput falls", "throughput degrades")
    span = ResearchNode(
        id="EvidenceSpan:s",
        name="span",
        type=ResearchNodeType.EVIDENCE_SPAN,
        description="Quantised KV-cache raised throughput by 18%.",
    )
    return ResearchGraph(
        nodes=[a, b, span],
        edges=[ResearchEdge(source=a.id, target=span.id, type="evidenced_by")],
    )


@pytest.mark.parametrize(
    "relation,edge_type",
    [("contradicts", "contradicts_claim"), ("criticizes", "criticizes")],
)
def test_contrast_mints_typed_reasoning_edge_with_direction(
    tmp_path: Path, relation: str, edge_type: str
):
    graph = _pair_graph()
    client = FakeClient(
        {"relation": relation, "direction": "b_to_a", "rationale": "b rebuts a"}
    )

    run_contrast_pass(graph, llm=client, cache_dir=tmp_path / "contrast_cache")

    minted = [e for e in graph.edges if e.type == edge_type]
    assert len(minted) == 1
    assert (minted[0].source, minted[0].target) == ("Claim:b", "Claim:a")
    assert minted[0].metadata["extractor"] == "memory.contrast"
    assert minted[0].metadata["block"] == "claims"
    assert minted[0].evidence == "b rebuts a"


def test_contrast_prompt_carries_verbatim_evidence_span_text(tmp_path: Path):
    """The judge must read the quoted source sentence, not a summary of it."""
    graph = _pair_graph()
    client = FakeClient({"relation": "none", "direction": "a_to_b"})

    run_contrast_pass(graph, llm=client, cache_dir=tmp_path / "contrast_cache")

    assert client.call_count == 1
    assert "Quantised KV-cache raised throughput by 18%." in client.calls[0]


@pytest.mark.parametrize("relation", ["compares_against", "supersedes"])
def test_contrast_rejects_out_of_vocab_relation(tmp_path: Path, relation: str):
    """compares_against is Method<->Method; supersedes is supersede.py's."""
    graph = _pair_graph()
    before = len(graph.edges)
    client = FakeClient({"relation": relation, "direction": "a_to_b"})

    run_contrast_pass(graph, llm=client, cache_dir=tmp_path / "contrast_cache")

    assert len(graph.edges) == before


def test_contrast_noop_without_client_or_flag(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "contrast_cache"

    graph = _pair_graph()
    before = graph.to_json()
    run_contrast_pass(graph, llm=None, cache_dir=cache_dir)  # flag on, no client
    assert graph.to_json() == before
    assert not cache_dir.exists()

    monkeypatch.setenv("TESSERAE_CONTRAST_PASS", "0")
    assert contrast_pass_enabled() is False
    client = FakeClient({"relation": "contradicts", "direction": "a_to_b"})
    run_contrast_pass(graph, llm=client, cache_dir=cache_dir)  # client, flag off
    assert graph.to_json() == before
    assert client.call_count == 0
    assert not cache_dir.exists()


# ------------------------------------------------------------------- caching


def test_contrast_warm_cache_is_byte_idempotent(tmp_path: Path):
    cache_dir = tmp_path / "contrast_cache"
    client = FakeClient(
        {"relation": "contradicts", "direction": "b_to_a", "rationale": "b rebuts a"}
    )

    g1 = _pair_graph()
    run_contrast_pass(g1, llm=client, cache_dir=cache_dir)
    after_first = client.call_count
    first_json = g1.to_json()

    g2 = _pair_graph()
    run_contrast_pass(g2, llm=client, cache_dir=cache_dir)

    assert after_first == 1
    assert client.call_count == after_first  # zero new calls on the warm run
    assert g2.to_json() == first_json


def test_contrast_cache_key_is_content_not_id(tmp_path: Path):
    """Reminted ids for unchanged content must still hit the warm cache."""
    cache_dir = tmp_path / "contrast_cache"
    client = FakeClient(
        {"relation": "contradicts", "direction": "b_to_a", "rationale": "b rebuts a"}
    )

    first = _pair_graph()
    run_contrast_pass(first, llm=client, cache_dir=cache_dir)
    assert client.call_count == 1

    # Same CONTENT, different id scheme.
    renamed = ResearchGraph(
        nodes=[
            _claim("Claim:zz-a", "quantised kv-cache throughput rises", "throughput improves"),
            _claim("Claim:zz-b", "quantised kv-cache throughput falls", "throughput degrades"),
        ],
        edges=[],
    )
    run_contrast_pass(renamed, llm=client, cache_dir=cache_dir)

    assert client.call_count == 1  # zero new calls
    minted = [e for e in renamed.edges if e.type == "contradicts_claim"]
    assert len(minted) == 1
    assert (minted[0].source, minted[0].target) == ("Claim:zz-b", "Claim:zz-a")


def test_contrast_skips_existing_edge(tmp_path: Path):
    graph = _pair_graph()
    graph.edges.append(
        ResearchEdge(source="Claim:b", target="Claim:a", type="contradicts_claim")
    )
    before = len(graph.edges)
    client = FakeClient({"relation": "contradicts", "direction": "b_to_a"})

    run_contrast_pass(graph, llm=client, cache_dir=tmp_path / "contrast_cache")

    assert len(graph.edges) == before


# -------------------------------------------------------------------- budget


def _many_pairs_graph(count: int = 50) -> ResearchGraph:
    nodes = []
    for i in range(count):
        nodes.append(_claim(f"Claim:x{i:02d}", f"alpha{i} bravo{i} charlie{i} delta{i}"))
        nodes.append(_claim(f"Claim:y{i:02d}", f"alpha{i} bravo{i} charlie{i} echo{i}"))
    return ResearchGraph(nodes=nodes, edges=[])


def test_contrast_budget_caps_calls(tmp_path: Path):
    graph = _many_pairs_graph(50)
    expected = candidate_pairs(graph, block=CLAIM_BLOCK)[:5]
    client = FakeClient({"relation": "none", "direction": "a_to_b"})

    run_contrast_pass(graph, llm=client, cache_dir=tmp_path / "c", max_pairs=5)

    assert client.call_count == 5
    judged = [call.split("\n")[1] for call in client.calls]
    for pair, prompt_line in zip(expected, judged):
        assert prompt_line.startswith(
            next(n.name for n in graph.nodes if n.id == pair.lo_id)
        )


def test_contrast_budget_env_var_zero_disables(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TESSERAE_CONTRAST_MAX_PAIRS", "0")
    graph = _many_pairs_graph(5)
    client = FakeClient({"relation": "contradicts", "direction": "a_to_b"})

    run_contrast_pass(graph, llm=client, cache_dir=tmp_path / "c")

    assert client.call_count == 0


def test_contrast_cache_payload_is_id_free(tmp_path: Path):
    cache_dir = tmp_path / "contrast_cache"
    client = FakeClient(
        {"relation": "contradicts", "direction": "b_to_a", "rationale": "b rebuts a"}
    )
    run_contrast_pass(_pair_graph(), llm=client, cache_dir=cache_dir)

    files = sorted(cache_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["direction"] in {"lo_to_hi", "hi_to_lo"}
    assert "Claim:a" not in files[0].read_text(encoding="utf-8")


# ------------------------------------------------------- compile-path wiring


def test_contrast_pass_is_wired_into_run_memory_passes(tmp_path, monkeypatch):
    """The pass must actually run at step (4.5) of the compile choke point."""
    from tesserae.project import ProjectWiki

    wiki = ProjectWiki.init(tmp_path, name="t")
    graph = _pair_graph()
    seen = {}

    def _spy(g, *, llm, cache_dir, max_pairs=None):
        seen["cache_dir"] = cache_dir
        seen["llm"] = llm
        return g

    monkeypatch.setattr("tesserae.memory.contrast.run_contrast_pass", _spy)
    client = FakeClient({"relation": "none", "direction": "a_to_b"})
    wiki._run_memory_passes(graph, client)

    assert seen["llm"] is client
    assert seen["cache_dir"] == wiki.paths.root / "contrast_cache"


def test_contrast_pass_is_skipped_when_flag_is_off(tmp_path, monkeypatch):
    from tesserae.project import ProjectWiki

    monkeypatch.setenv("TESSERAE_CONTRAST_PASS", "0")
    wiki = ProjectWiki.init(tmp_path, name="t")
    calls = []
    monkeypatch.setattr(
        "tesserae.memory.contrast.run_contrast_pass",
        lambda g, **kw: calls.append(kw) or g,
    )
    wiki._run_memory_passes(_pair_graph(), FakeClient({"relation": "none"}))

    assert calls == []
