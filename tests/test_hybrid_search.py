"""Tests for the hybrid retrieval module and the upgraded MCP search_nodes."""

from __future__ import annotations

from typing import List

import pytest

from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.retrieval import hybrid as hybrid_mod
from tesserae.retrieval.hybrid import (
    HashEmbeddingBackend,
    active_embedding_backend,
    hybrid_search,
    reset_embedding_backend,
)


def _eight_node_graph() -> ResearchGraph:
    """Eight-node fixture exercising each retrieval lane independently."""
    nodes: List[ResearchNode] = [
        ResearchNode(
            id="Paper:dual-splat",
            name="DualSplat",
            type=ResearchNodeType.PAPER,
            description=(
                "Robust 3D Gaussian splatting for novel-view synthesis with "
                "improved shape regularisation across many scenes."
            ),
            metadata={"arxiv_id": "2601.17835"},
        ),
        ResearchNode(
            id="MethodologicalConcept:gaussian-splatting",
            name="Gaussian Splatting",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=["3DGS"],
            description="Point-based differentiable rendering technique.",
        ),
        ResearchNode(
            id="PerformanceClaim:best-shape",
            name="Best shape reconstruction claim",
            type=ResearchNodeType.PERFORMANCE_CLAIM,
            description="DualSplat reports best-in-class shape reconstruction.",
        ),
        ResearchNode(
            id="Paper:nerf",
            name="NeRF",
            type=ResearchNodeType.PAPER,
            description=(
                "Neural Radiance Fields representing scenes as continuous "
                "volumetric functions optimised from posed images."
            ),
            metadata={"arxiv_id": "2003.08934"},
        ),
        ResearchNode(
            id="MethodologicalConcept:bm25",
            name="BM25",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=["Okapi BM25"],
            description=(
                "Probabilistic ranking function widely used in information "
                "retrieval; the Okapi variant ships in nearly every search "
                "engine and remains a strong baseline."
            ),
        ),
        ResearchNode(
            id="MethodologicalConcept:rrf",
            name="Reciprocal Rank Fusion",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=["RRF"],
            description=(
                "Score-free rank aggregation used by LightRAG and other "
                "hybrid retrievers; k=60 is the canonical damping constant."
            ),
        ),
        ResearchNode(
            id="Concept:obsidian-vault",
            name="Obsidian Vault",
            type=ResearchNodeType.CONCEPT,
            description=(
                "Local-first markdown knowledge base that Tesserae projects "
                "the compiled wiki into for offline browsing."
            ),
        ),
        ResearchNode(
            id="OpenQuestion:hybrid-vs-graph",
            name="Hybrid retrieval vs pure graph traversal",
            type=ResearchNodeType.OPEN_QUESTION,
            description=(
                "When should we prefer graph neighbourhood expansion over "
                "BM25 + embedding fusion for knowledge-graph QA?"
            ),
        ),
    ]
    edges = [
        ResearchEdge(source=nodes[0].id, target=nodes[1].id, type="uses"),
        ResearchEdge(source=nodes[0].id, target=nodes[2].id, type="supports_claim"),
        ResearchEdge(source=nodes[5].id, target=nodes[4].id, type="references"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Direct hybrid_search() unit tests
# ---------------------------------------------------------------------------


def test_hybrid_search_returns_reasonable_top_k_with_default_mode():
    graph = _eight_node_graph()
    result = hybrid_search(graph, "gaussian splatting", top_k=3, backend=HashEmbeddingBackend())

    assert result.mode == "hybrid"
    assert 1 <= len(result.scored) <= 3
    top_ids = [item.node.id for item in result.scored]
    # The two splatting-related nodes should both be in the top window.
    assert "MethodologicalConcept:gaussian-splatting" in top_ids
    assert "Paper:dual-splat" in top_ids
    # Sanity: scores strictly decreasing
    scores = [item.score for item in result.scored]
    assert scores == sorted(scores, reverse=True)


def test_bm25_lane_prefers_lexical_matches():
    graph = _eight_node_graph()
    result = hybrid_search(
        graph, "okapi bm25", top_k=5, backend=HashEmbeddingBackend(), mode="bm25"
    )
    top_ids = [item.node.id for item in result.scored]
    assert top_ids[0] == "MethodologicalConcept:bm25"


def test_lexical_and_legacy_modes_match_substring_behaviour():
    graph = _eight_node_graph()
    lex = hybrid_search(graph, "obsidian", top_k=5, backend=HashEmbeddingBackend(), mode="lexical")
    legacy_ids = {item.node.id for item in lex.scored}
    assert "Concept:obsidian-vault" in legacy_ids


def test_embedding_lane_returns_something_for_paraphrase():
    """The hash backend has no semantics, but the embedding lane still has to
    *run* and produce a deterministic ranking that is non-empty."""
    graph = _eight_node_graph()
    result = hybrid_search(
        graph,
        "fusing search results from multiple ranked lists",
        top_k=5,
        backend=HashEmbeddingBackend(),
        mode="embedding",
    )
    assert len(result.scored) >= 1
    # Re-running yields the same ordering (determinism guarantee).
    again = hybrid_search(
        graph,
        "fusing search results from multiple ranked lists",
        top_k=5,
        backend=HashEmbeddingBackend(),
        mode="embedding",
    )
    assert [s.node.id for s in result.scored] == [s.node.id for s in again.scored]


def test_modes_produce_distinguishable_orderings():
    """Different lanes should favour different docs for the same query,
    otherwise the fusion is buying us nothing."""
    graph = _eight_node_graph()
    query = "ranking baseline used in search engines"
    backend = HashEmbeddingBackend()
    bm25_top = [s.node.id for s in hybrid_search(graph, query, top_k=8, backend=backend, mode="bm25").scored]
    lex_top = [s.node.id for s in hybrid_search(graph, query, top_k=8, backend=backend, mode="lexical").scored]
    emb_top = [s.node.id for s in hybrid_search(graph, query, top_k=8, backend=backend, mode="embedding").scored]
    hyb_top = [s.node.id for s in hybrid_search(graph, query, top_k=8, backend=backend, mode="hybrid").scored]

    # At least one ordering must differ from another — proves the lanes are
    # independent signals rather than three copies of the same scorer.
    orderings = {tuple(bm25_top), tuple(lex_top), tuple(emb_top), tuple(hyb_top)}
    assert len(orderings) >= 2


def test_empty_query_returns_first_top_k_without_failing():
    graph = _eight_node_graph()
    result = hybrid_search(graph, "", top_k=4, backend=HashEmbeddingBackend())
    assert len(result.scored) == 4
    assert [s.node.id for s in result.scored] == [n.id for n in graph.nodes[:4]]


def test_weights_override_disables_a_lane():
    graph = _eight_node_graph()
    # Force bm25-only via weights; result should mirror the bm25 mode for a
    # query that BM25 actually scores positively.
    res_weighted = hybrid_search(
        graph,
        "okapi bm25",
        top_k=3,
        backend=HashEmbeddingBackend(),
        weights={"bm25": 1.0, "lexical": 0.0, "embedding": 0.0},
    )
    res_bm25 = hybrid_search(
        graph, "okapi bm25", top_k=3, backend=HashEmbeddingBackend(), mode="bm25"
    )
    assert [s.node.id for s in res_weighted.scored] == [s.node.id for s in res_bm25.scored]


def test_unknown_mode_raises():
    graph = _eight_node_graph()
    with pytest.raises(ValueError):
        hybrid_search(graph, "x", mode="nope", backend=HashEmbeddingBackend())


# ---------------------------------------------------------------------------
# MCP integration tests
# ---------------------------------------------------------------------------


def _server_with_fixture(tmp_path) -> LLMWikiMCPServer:
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(_eight_node_graph().to_json(indent=2), encoding="utf-8")
    return LLMWikiMCPServer(default_graph_path=graph_path)


def test_mcp_search_nodes_default_mode_is_hybrid(tmp_path):
    server = _server_with_fixture(tmp_path)
    result = server.call_tool("search_nodes", {"q": "gaussian splatting"})
    assert result["mode"] == "hybrid"
    assert result["total_matches"] >= 1
    # Public contract preserved.
    assert {"query", "total_matches", "nodes"}.issubset(result.keys())
    for node in result["nodes"]:
        assert "id" in node and "name" in node and "type" in node


def test_mcp_search_nodes_legacy_mode_matches_old_substring_contract(tmp_path):
    server = _server_with_fixture(tmp_path)
    result = server.call_tool(
        "search_nodes",
        {"query": "3dgs shape", "types": ["MethodologicalConcept", "PerformanceClaim"], "mode": "legacy"},
    )
    names = [node["name"] for node in result["nodes"]]
    assert names == ["Gaussian Splatting", "Best shape reconstruction claim"]
    assert result["mode"] == "legacy"


def test_mcp_search_nodes_mode_changes_ordering(tmp_path):
    server = _server_with_fixture(tmp_path)
    # "splatting reconstruction" — BM25 weights "reconstruction" highly (rare
    # term in this corpus); lexical / legacy only counts term-presence so it
    # rewards both splatting docs equally and resolves ties by node order.
    bm25 = server.call_tool("search_nodes", {"q": "splatting reconstruction", "mode": "bm25", "limit": 8})
    lex = server.call_tool("search_nodes", {"q": "splatting reconstruction", "mode": "legacy", "limit": 8})
    bm25_ids = [n["id"] for n in bm25["nodes"]]
    lex_ids = [n["id"] for n in lex["nodes"]]
    # Both lanes should surface splatting-related material.
    assert any("splat" in nid.lower() for nid in bm25_ids)
    # The orderings or the candidate sets should differ — proves the lanes
    # are genuinely independent signals, not just two copies of one scorer.
    assert bm25_ids != lex_ids or set(bm25_ids) != set(lex_ids)


def test_mcp_embedding_status_tool(tmp_path):
    server = _server_with_fixture(tmp_path)
    status = server.call_tool("embedding_status", {})
    assert status["available"] is True
    assert isinstance(status["backend"], str) and status["backend"]
    assert "hybrid" in status["modes"] and "legacy" in status["modes"]


def test_mcp_search_nodes_tool_listed_with_mode(tmp_path):
    server = _server_with_fixture(tmp_path)
    tools = {tool["name"]: tool for tool in server.list_tools()}
    assert "embedding_status" in tools
    schema = tools["search_nodes"]["inputSchema"]["properties"]
    assert "mode" in schema
    assert schema["mode"]["enum"] == ["hybrid", "bm25", "lexical", "embedding", "legacy"]


def test_active_backend_resolver_returns_something(tmp_path):
    backend = active_embedding_backend()
    assert backend is not None
    assert hasattr(backend, "embed")
    sample = backend.embed(["hello world"])
    assert len(sample) == 1
    assert all(isinstance(x, float) for x in sample[0])


# ---------------------------------------------------------------------------
# Codex review fixes (3xP2) — regression tests
# ---------------------------------------------------------------------------


def test_partial_weight_override_only_disables_named_lane():
    """A caller passing ``weights={"embedding": 0}`` should disable *only*
    embeddings — BM25 and lexical must retain their defaults so the hybrid
    candidate-generation gate still has lexical evidence to admit results.

    Regression for codex P2: previously ``selected_weights`` was initialized
    from the override dict directly, so omitted lanes silently got weight 0
    and the gate found no candidates → empty results.
    """
    graph = _eight_node_graph()
    result = hybrid_search(
        graph,
        "gaussian splatting",
        top_k=5,
        backend=HashEmbeddingBackend(),
        weights={"embedding": 0},
    )
    # Must return real results — BM25 + lexical still active.
    assert len(result.scored) >= 1
    top_ids = [item.node.id for item in result.scored]
    assert "MethodologicalConcept:gaussian-splatting" in top_ids
    # The merged weights must show the override applied on top of defaults.
    assert result.weights["embedding"] == 0
    assert result.weights["bm25"] > 0
    assert result.weights["lexical"] > 0


def test_total_matches_reports_pre_slice_candidate_count(tmp_path):
    """``total_matches`` must reflect every candidate that survived the
    candidate-generation gate, not just the page size returned to the caller.

    Regression for codex P2: the MCP server previously set
    ``total_matches = len(nodes_out)`` (the limit-bounded slice), which hid
    the real match count from clients implementing pagination.
    """
    server = _server_with_fixture(tmp_path)
    # "splatting" hits both Paper:dual-splat and MethodologicalConcept:
    # gaussian-splatting (plus the PerformanceClaim that mentions DualSplat
    # via the description). Cap the page below that count.
    full = server.call_tool("search_nodes", {"q": "splatting", "limit": 100})
    expected_total = full["total_matches"]
    assert expected_total >= 2, "fixture must produce >=2 splatting matches"

    paged = server.call_tool("search_nodes", {"q": "splatting", "limit": 1})
    assert len(paged["nodes"]) == 1  # page size honoured
    assert paged["total_matches"] == expected_total  # but total is unbounded


def test_active_embedding_backend_is_cached_across_calls():
    """``active_embedding_backend()`` must memoise its result so the
    expensive ``SentenceTransformer`` model load only happens once per
    process. ``reset_embedding_backend()`` should clear the cache for tests.

    Regression for codex P2: previously each default-mode ``search_nodes``
    call constructed a fresh backend, reloading hundreds of MB of weights.
    """
    reset_embedding_backend()
    first = active_embedding_backend()
    second = active_embedding_backend()
    assert first is second  # identity, not just equality

    # The reset helper must drop the cache so tests that swap deps work.
    reset_embedding_backend()
    third = active_embedding_backend()
    assert third is not first  # post-reset yields a fresh instance
    # And the fresh resolution is itself memoised.
    assert active_embedding_backend() is third
    # Restore cache hygiene for any later tests in the suite.
    reset_embedding_backend()


# ---------------------------------------------------------------------------
# Persisted vectors (roadmap step 1) — a CACHE, never an index.
#
# The whole contract is that retrieval COST changes and retrieval RESULTS do
# not. Every test below is either "the same answer" or "the right thing was
# re-embedded"; none asserts a ranking that a cache could be allowed to move.
# ---------------------------------------------------------------------------


class _CountingBackend(HashEmbeddingBackend):
    """Hash stub that records every text it was actually asked to embed.

    Subclasses the shipped stub rather than inventing a backend so the
    candidate gate behaves exactly as it does in production (``hybrid_search``
    branches on ``isinstance(..., HashEmbeddingBackend)``).
    """

    def __init__(self) -> None:
        self.calls = 0
        self.embedded: List[str] = []

    def embed(self, texts):
        self.calls += 1
        self.embedded.extend(texts)
        return super().embed(texts)


def _project(tmp_path, name: str):
    root = tmp_path / name
    (root / ".tesserae").mkdir(parents=True)
    return root


def _scores(result):
    return [(item.node.id, item.score, dict(item.per_lane)) for item in result.scored]


def test_vector_cache_scores_are_identical_cold_warm_and_uncached(tmp_path):
    """Cold cache, warm cache and no cache must produce the SAME scores.

    This is the invariant that makes the cache safe to turn on everywhere: it
    removes the model call, nothing else. Exact float equality on purpose — a
    lossy round-trip would show up here and nowhere else.
    """
    from tesserae.retrieval.vector_cache import VectorCache

    graph = _eight_node_graph()
    root = _project(tmp_path, "proj")
    cache = VectorCache.for_project(root)
    assert cache is not None

    uncached = hybrid_search(graph, "gaussian splatting", backend=_CountingBackend())
    cold_backend = _CountingBackend()
    cold = hybrid_search(
        graph, "gaussian splatting", backend=cold_backend, vector_cache=cache
    )
    warm_backend = _CountingBackend()
    warm = hybrid_search(
        graph, "gaussian splatting", backend=warm_backend, vector_cache=cache
    )

    assert _scores(cold) == _scores(uncached)
    assert _scores(warm) == _scores(uncached)
    assert cold.total_matches == uncached.total_matches == warm.total_matches

    # And the cache actually served: the warm run made no model call at all
    # (the query is cached too — it is just another text).
    assert cold_backend.calls == 1
    assert warm_backend.calls == 0


def test_vector_cache_reembeds_changed_text_but_not_a_relocated_project(tmp_path):
    """The key is the embedded TEXT, not the node id or the project path.

    A changed description must re-embed exactly its own node; an unchanged node
    whose project moved on disk must not re-embed at all. Keying on node id
    would invert both halves of this.
    """
    import shutil

    from tesserae.retrieval.vector_cache import VectorCache, node_embedding_text

    nodes = [
        ResearchNode(
            id="Concept:alpha",
            name="Alpha",
            type=ResearchNodeType.CONCEPT,
            description="First concept.",
        ),
        ResearchNode(
            id="Concept:beta",
            name="Beta",
            type=ResearchNodeType.CONCEPT,
            description="Second concept.",
        ),
    ]
    origin = _project(tmp_path, "origin")
    backend = _CountingBackend()
    cache = VectorCache.for_project(origin)
    texts = [node_embedding_text(n) for n in nodes]
    cache.embed(backend, texts)
    assert backend.calls == 1
    assert cache.stats.misses == 2 and cache.stats.hits == 0

    # Relocate the project: same sidecar, new path. Nothing may re-embed.
    moved = tmp_path / "moved"
    shutil.copytree(origin, moved)
    moved_backend = _CountingBackend()
    moved_cache = VectorCache.for_project(moved)
    moved_cache.embed(moved_backend, texts)
    assert moved_backend.calls == 0
    assert moved_cache.stats.hits == 2 and moved_cache.stats.misses == 0

    # Re-describe ONE node: only that node's text is embedded again.
    edited = [
        ResearchNode(
            id=nodes[0].id,
            name=nodes[0].name,
            type=nodes[0].type,
            description="First concept, now explained at length.",
        ),
        nodes[1],
    ]
    edit_backend = _CountingBackend()
    edit_cache = VectorCache.for_project(moved)
    edit_cache.embed(edit_backend, [node_embedding_text(n) for n in edited])
    assert edit_backend.embedded == [node_embedding_text(edited[0])]
    assert edit_cache.stats.hits == 1 and edit_cache.stats.misses == 1


def test_vector_cache_ignores_node_id_and_dedups_within_a_batch(tmp_path):
    """Two nodes with identical text cost one model call, and an id rewrite costs none.

    Canonicalization rewrites ids every compile; if the cache were keyed on id
    the whole corpus would re-embed after every merge.
    """
    from tesserae.retrieval.vector_cache import VectorCache, node_embedding_text

    root = _project(tmp_path, "proj")
    duplicate = ResearchNode(
        id="Concept:one", name="Same", type=ResearchNodeType.CONCEPT, description="Text."
    )
    renamed = ResearchNode(
        id="Concept:canonical-one",
        name="Same",
        type=ResearchNodeType.CONCEPT,
        description="Text.",
    )
    backend = _CountingBackend()
    cache = VectorCache.for_project(root)
    vectors = cache.embed(backend, [node_embedding_text(duplicate), node_embedding_text(duplicate)])
    assert backend.embedded == [node_embedding_text(duplicate)]  # deduped in-batch
    assert vectors[0] == vectors[1]

    after_rewrite = VectorCache.for_project(root)
    rewrite_backend = _CountingBackend()
    after_rewrite.embed(rewrite_backend, [node_embedding_text(renamed)])
    assert rewrite_backend.calls == 0


def test_vector_cache_is_none_without_a_tesserae_sidecar(tmp_path):
    """No ``.tesserae/`` means no cache — a read must not create one as a side effect."""
    from tesserae.retrieval.vector_cache import VectorCache

    assert VectorCache.for_project(None) is None
    assert VectorCache.for_project(tmp_path) is None
    assert VectorCache.for_graph_path(tmp_path / "graph.json") is None
    assert not (tmp_path / ".tesserae").exists()

    root = _project(tmp_path, "proj")
    assert VectorCache.for_graph_path(root / ".tesserae" / "graph.json") is not None


def test_vector_cache_degrades_when_the_sidecar_is_unusable(tmp_path):
    """An unreadable sidecar costs a slow query, never a failed one — and says so."""
    from tesserae.retrieval.vector_cache import VectorCache

    broken = tmp_path / ".tesserae"
    broken.mkdir()
    (broken / "sqlite.db").write_text("this is not a database", encoding="utf-8")

    backend = _CountingBackend()
    cache = VectorCache(broken / "sqlite.db")
    vectors = cache.embed(backend, ["alpha", "beta"])

    assert vectors == backend.embed(["alpha", "beta"])
    assert cache.stats.errors > 0  # fail loud in the counters, not in the caller


def test_mcp_embedding_status_reports_the_vector_cache(tmp_path):
    """``embedding_status`` must expose cache depth and hit/miss, or a cold
    cache is indistinguishable from a fast path."""
    from tesserae.retrieval.vector_cache import reset_process_stats

    root = tmp_path / "proj"
    (root / ".tesserae").mkdir(parents=True)
    graph_path = root / ".tesserae" / "graph.json"
    graph_path.write_text(_eight_node_graph().to_json(indent=2), encoding="utf-8")
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    reset_process_stats()
    cold = server.call_tool("embedding_status", {"graph_path": str(graph_path)})
    assert cold["vectors_cached"] == 0
    assert cold["cache_hits"] == 0 and cold["cache_misses"] == 0

    first = server.call_tool("search_nodes", {"q": "gaussian splatting"})
    warm = server.call_tool("embedding_status", {"graph_path": str(graph_path)})
    assert warm["vectors_cached"] > 0
    assert warm["cache_misses"] > 0
    assert warm["vector_cache_db"].endswith("sqlite.db")

    # Same query again: the results are unchanged and the reads are now hits.
    second = server.call_tool("search_nodes", {"q": "gaussian splatting"})
    assert [n["id"] for n in second["nodes"]] == [n["id"] for n in first["nodes"]]
    assert second["total_matches"] == first["total_matches"]
    hot = server.call_tool("embedding_status", {"graph_path": str(graph_path)})
    assert hot["cache_hits"] > warm["cache_hits"]
    assert hot["cache_misses"] == warm["cache_misses"]  # nothing re-embedded


# ---------------------------------------------------------------------------
# Retrieval PROFILE (roadmap step 9) — opt-in, because measuring costs.
#
# Two contracts, and both are load-bearing: with ``profile`` unset the search
# behaves and answers exactly as before AND reads no clock; with it set the
# numbers describe the search that already ran, so they can never move a
# ranking. Every test below pins one of those two.
# ---------------------------------------------------------------------------


def test_profile_is_off_by_default_and_reads_no_clock(monkeypatch):
    """The default path must not pay for instrumentation it did not ask for.

    Asserting ``profile is None`` alone would still pass if someone timed the
    lanes unconditionally and merely withheld the report, so count the clock
    reads: zero when off, non-zero when on. That is the regression this guards.
    """
    import time as _time

    from tesserae.retrieval import hybrid as hybrid_mod

    reads = {"n": 0}

    class _CountingClock:
        """Stands in for the ``time`` module inside hybrid.py only — patching
        the real module would also count clock reads made by dependencies."""

        @staticmethod
        def perf_counter():
            reads["n"] += 1
            return _time.perf_counter()

    monkeypatch.setattr(hybrid_mod, "time", _CountingClock)

    graph = _eight_node_graph()
    default = hybrid_search(graph, "gaussian splatting", backend=HashEmbeddingBackend())
    assert default.profile is None
    assert reads["n"] == 0

    profiled = hybrid_search(
        graph, "gaussian splatting", backend=HashEmbeddingBackend(), profile=True
    )
    assert profiled.profile is not None
    assert reads["n"] > 0


def test_profile_never_changes_the_answer():
    """Profiling must be observationally inert on results AND on behaviour."""
    graph = _eight_node_graph()
    query = "ranking baseline used in search engines"
    plain = hybrid_search(graph, query, top_k=6, backend=HashEmbeddingBackend())
    profiled = hybrid_search(
        graph, query, top_k=6, backend=HashEmbeddingBackend(), profile=True
    )

    assert _scores(profiled) == _scores(plain)
    assert [dict(s.ranks) for s in profiled.scored] == [dict(s.ranks) for s in plain.scored]
    assert profiled.total_matches == plain.total_matches
    assert profiled.weights == plain.weights
    assert profiled.backend == plain.backend
    assert profiled.mode == plain.mode


def test_profile_reports_each_lane_and_attributes_every_winner():
    graph = _eight_node_graph()
    result = hybrid_search(
        graph, "gaussian splatting", top_k=4, backend=HashEmbeddingBackend(), profile=True
    )
    prof = result.profile
    assert prof is not None
    assert set(prof.lanes) == {"bm25", "lexical", "embedding"}
    assert prof.candidates_in == len(graph.nodes)
    assert prof.admitted == result.total_matches
    assert prof.returned == len(result.scored)
    for lane in prof.lanes.values():
        # Every lane is live in hybrid mode, so each one saw the whole corpus.
        assert lane.weight == 1.0
        assert lane.candidates_in == len(graph.nodes)
        assert 0 <= lane.scored <= len(graph.nodes)
        assert lane.ms >= 0.0

    # Winners line up with the returned page, in the same order, and their
    # lane attribution matches _fuse's contribution criterion (positive weight
    # AND a rank inside the corpus) rather than "produced a non-zero score".
    assert [w.node_id for w in prof.winners] == [s.node.id for s in result.scored]
    for winner, scored in zip(prof.winners, result.scored):
        assert winner.score == scored.score
        expected = tuple(
            lane
            for lane in ("bm25", "lexical", "embedding")
            if scored.ranks[lane] <= len(graph.nodes)
        )
        assert winner.lanes == expected
        assert winner.lanes, "a returned node must have been contributed by some lane"


def test_profile_distinguishes_a_lane_that_did_not_run_from_one_that_found_nothing():
    """A disabled lane reports candidates_in=0. Zeroing only ``scored`` would
    make "the embedding lane was off" and "the embedding lane matched nothing"
    the same reading, which is the whole point of accounting per lane."""
    graph = _eight_node_graph()
    result = hybrid_search(
        graph, "okapi bm25", top_k=5, backend=HashEmbeddingBackend(), mode="bm25", profile=True
    )
    prof = result.profile
    assert prof is not None
    assert prof.lanes["bm25"].weight == 1.0
    assert prof.lanes["bm25"].candidates_in == len(graph.nodes)
    for off in ("lexical", "embedding"):
        assert prof.lanes[off].weight == 0.0
        assert prof.lanes[off].candidates_in == 0
        assert prof.lanes[off].scored == 0
        assert prof.lanes[off].embed_calls == 0
    # Only the live lane may be credited with a win.
    assert all(w.lanes == ("bm25",) for w in prof.winners)


def test_profile_counts_the_uncached_model_call_rather_than_reporting_zero():
    """With no cache the counters do not exist, so the profile states the one
    ``backend.embed`` batch it made. Reporting 0/0/0 here would read as a
    perfectly warm cache on the most expensive path there is."""
    graph = _eight_node_graph()
    backend = _CountingBackend()
    result = hybrid_search(graph, "gaussian splatting", backend=backend, profile=True)
    prof = result.profile
    assert prof is not None
    assert prof.vector_cache is False
    embedding = prof.lanes["embedding"]
    assert embedding.embed_calls == backend.calls == 1
    assert embedding.cache_hits == 0 and embedding.cache_misses == 0


def test_profile_reports_cold_then_warm_cache_on_the_embedding_lane(tmp_path):
    """The step-1 acceptance evidence: a cold cache misses and embeds, a warm
    one hits and does not. Without this the cache ships unproven."""
    from tesserae.retrieval.vector_cache import VectorCache

    graph = _eight_node_graph()
    cache = VectorCache.for_project(_project(tmp_path, "proj"))
    assert cache is not None

    cold = hybrid_search(
        graph, "gaussian splatting", backend=_CountingBackend(),
        vector_cache=cache, profile=True,
    ).profile
    warm = hybrid_search(
        graph, "gaussian splatting", backend=_CountingBackend(),
        vector_cache=cache, profile=True,
    ).profile
    assert cold is not None and warm is not None

    assert cold.vector_cache is True and warm.vector_cache is True
    # query + one text per node, all unseen.
    assert cold.lanes["embedding"].cache_misses == len(graph.nodes) + 1
    assert cold.lanes["embedding"].cache_hits == 0
    assert cold.lanes["embedding"].embed_calls == 1

    assert warm.lanes["embedding"].cache_hits == len(graph.nodes) + 1
    assert warm.lanes["embedding"].cache_misses == 0
    assert warm.lanes["embedding"].embed_calls == 0

    # Only the embedding lane can embed; crediting the others would be a lie
    # that a consumer would read as three model calls.
    for lane in ("bm25", "lexical"):
        assert cold.lanes[lane].embed_calls == 0
        assert cold.lanes[lane].cache_misses == 0


def test_profile_of_a_search_that_short_circuits_reports_no_lanes():
    """Empty corpus and empty query return before any lane runs. Three zeroed
    lanes would claim they ran and scored nothing; an empty dict is the honest
    shape."""
    graph = _eight_node_graph()
    empty_query = hybrid_search(graph, "   ", backend=HashEmbeddingBackend(), profile=True)
    assert empty_query.profile is not None
    assert empty_query.profile.lanes == {}
    assert empty_query.profile.candidates_in == len(graph.nodes)
    assert empty_query.profile.winners == []

    empty_corpus = hybrid_search(
        graph, "gaussian", backend=HashEmbeddingBackend(), candidate_filter=[], profile=True
    )
    assert empty_corpus.profile is not None
    assert empty_corpus.profile.lanes == {}
    assert empty_corpus.profile.candidates_in == 0


# ---------------------------------------------------------------------------
# Vectorised embedding lane
# ---------------------------------------------------------------------------

_VECTOR_QUERY = "gaussian splatting retrieval index"


def _varied_corpus(size: int = 200) -> List[str]:
    """Deterministic corpus with enough spread to make an ORDERING meaningful.

    Eight nodes would let two paths agree by luck. Each document here mixes the
    query's terms at its own multiplicity plus unique filler, which spreads the
    cosine scores out — mostly distinct, with a handful of exact ties, so both
    the "untied documents never move" and the "ties may swap" halves of the
    contract have something to bite on.
    """
    docs: List[str] = []
    for i in range(size):
        words = (
            ["gaussian"] * (1 + i % 5)
            + ["splatting"] * (1 + i % 7)
            + ["retrieval"] * (1 + i % 3)
            + ["index"] * (1 + i % 11)
            + [f"filler{i * 3 + k}" for k in range(1 + i % 13)]
        )
        docs.append(" ".join(words))
    return docs


def _ranking(scores: List[float]) -> List[int]:
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


@pytest.mark.parametrize("cache_state", ["uncached", "cold", "warm"])
def test_vectorized_cosine_reproduces_the_python_lane_ordering(tmp_path, cache_state):
    """The vectorised lane may cost less; it may not answer differently.

    Bit-exact equality is impossible and claiming it would be a lie: BLAS
    reassociates the sums, so scores move by ~1e-16. This pins what that is
    allowed to do to a RANKING. Two documents whose true cosine differs keep
    their relative order, because the separation between distinct scores is
    orders of magnitude wider than the error. Two documents whose true cosine
    is EQUAL can swap, because the scalar path happens to land on the same
    float for both and the vectorised one does not — an arbitrary tie-break
    either way, and the only difference either path can produce.
    """
    pytest.importorskip("numpy")

    from tesserae.retrieval.vector_cache import VectorCache

    backend = HashEmbeddingBackend()
    corpus = _varied_corpus()
    cache = None
    if cache_state != "uncached":
        cache = VectorCache.for_project(_project(tmp_path, "proj"))
        assert cache is not None
        if cache_state == "warm":
            cache.embed(backend, [_VECTOR_QUERY, *corpus])

    scalar = hybrid_mod._embedding_scores(_VECTOR_QUERY, corpus, backend, cache)
    vectorized = hybrid_mod._embedding_scores_vectorized(
        _VECTOR_QUERY, corpus, backend, cache
    )

    assert vectorized is not None
    assert len(vectorized) == len(scalar)
    # 1e-12 is ~1000x the reassociation error measured on this project's own
    # 47,132 x 256 corpus, and ~40x below the tightest gap seen there between
    # two distinct cosine scores.
    assert max(abs(a - b) for a, b in zip(scalar, vectorized)) <= 1e-12

    order_scalar, order_vectorized = _ranking(scalar), _ranking(vectorized)
    # Nothing materially better was displaced: rank by rank, the two orderings
    # hold documents of the same score.
    for scalar_idx, vector_idx in zip(order_scalar, order_vectorized):
        assert abs(scalar[scalar_idx] - scalar[vector_idx]) <= 1e-12

    # THE ORDERING GUARANTEE, stated as what it actually is: a pair separated
    # by MORE than the tolerance keeps its order. A pair closer than that is
    # NUMERICALLY tied even when the two scalar scores are not bit-identical,
    # and reassociation may order it either way.
    #
    # An earlier version of this test asked for more than the implementation
    # can promise — it took "no other document shares this exact float" as
    # "a tie-break cannot touch this document", so two documents differing by
    # 1e-16 were required to hold their order. That held on macOS/arm64 and
    # failed on CI's x86 BLAS, which is the tell: the assertion was measuring
    # the platform's summation order, not the lane's behaviour.
    rank_vectorized = {idx: pos for pos, idx in enumerate(order_vectorized)}
    separated = 0
    for position, better in enumerate(order_scalar):
        for worse in order_scalar[position + 1:]:
            if scalar[better] - scalar[worse] <= 1e-12:
                continue  # numerically tied — either order is correct
            separated += 1
            assert rank_vectorized[better] < rank_vectorized[worse], (
                f"{better} outscores {worse} by "
                f"{scalar[better] - scalar[worse]:.3e} yet the vectorized "
                "lane ranked it lower"
            )
    assert separated, "fixture has no separated pair, so it proves nothing"


def test_embedding_lane_falls_back_to_python_when_numpy_is_absent(monkeypatch):
    """numpy is an OPTIONAL dependency, so its absence must cost speed only.

    The fallback also has to happen BEFORE any embedding: returning None after
    embedding would make the scalar path re-read the whole corpus and double
    the cache counters the profile reports.
    """
    backend = _CountingBackend()
    corpus = _varied_corpus(50)
    monkeypatch.setattr(hybrid_mod, "_numpy", lambda: None)

    assert (
        hybrid_mod._embedding_scores_vectorized(_VECTOR_QUERY, corpus, backend) is None
    )
    assert backend.calls == 0

    graph = _eight_node_graph()
    fallback = hybrid_search(
        graph, "gaussian splatting", backend=HashEmbeddingBackend(), profile=True
    )
    monkeypatch.undo()
    vectorized = hybrid_search(
        graph, "gaussian splatting", backend=HashEmbeddingBackend(), profile=True
    )

    assert fallback.profile.lanes["embedding"].vectorized is False
    assert vectorized.profile.lanes["embedding"].vectorized is bool(hybrid_mod._numpy())
    assert [item.node.id for item in fallback.scored] == [
        item.node.id for item in vectorized.scored
    ]


def test_profile_reports_the_vectorised_lane_only_on_the_lane_that_has_one():
    """``vectorized`` on bm25/lexical must read as "never had one", not as a
    fallback — the same distinction ``bm25_index`` makes for its own lane."""
    pytest.importorskip("numpy")
    result = hybrid_search(
        _eight_node_graph(), "gaussian splatting",
        backend=HashEmbeddingBackend(), profile=True,
    )
    prof = result.profile
    assert prof is not None
    assert prof.lanes["embedding"].vectorized is True
    assert prof.lanes["bm25"].vectorized is False
    assert prof.lanes["lexical"].vectorized is False
    assert prof.to_dict()["lanes"]["embedding"]["vectorized"] is True


def test_vectorized_lane_scores_the_filtered_candidate_subset_only(tmp_path):
    """Filter-first is the property that ruled an ANN index out; vectorising
    must not quietly reintroduce a whole-corpus scan."""
    pytest.importorskip("numpy")
    from tesserae.retrieval.vector_cache import VectorCache

    graph = _eight_node_graph()
    subset = list(graph.nodes)[:3]
    cache = VectorCache.for_project(_project(tmp_path, "proj"))
    backend = _CountingBackend()

    result = hybrid_search(
        graph, "gaussian splatting", backend=backend, mode="embedding",
        candidate_filter=subset, vector_cache=cache, profile=True,
    )

    assert result.profile is not None
    assert result.profile.lanes["embedding"].vectorized is True
    assert result.profile.candidates_in == len(subset)
    subset_ids = {node.id for node in subset}
    assert {item.node.id for item in result.scored} <= subset_ids
    # The query plus the three candidates, and nothing from the other five.
    assert len(backend.embedded) == len(subset) + 1


def test_mcp_search_nodes_explain_adds_a_profile_and_leaves_the_default_untouched(tmp_path):
    server = _server_with_fixture(tmp_path)
    plain = server.call_tool("search_nodes", {"q": "gaussian splatting", "limit": 4})
    explained = server.call_tool(
        "search_nodes", {"q": "gaussian splatting", "limit": 4, "explain": True}
    )

    assert "profile" not in plain
    # Same answer, key for key, once the profile is removed.
    assert {k: v for k, v in explained.items() if k != "profile"} == plain

    prof = explained["profile"]
    assert set(prof["lanes"]) == {"bm25", "lexical", "embedding"}
    assert prof["returned"] == len(explained["nodes"])
    assert prof["admitted"] == explained["total_matches"]
    assert [w["node_id"] for w in prof["winners"]] == [n["id"] for n in explained["nodes"]]
    assert all(w["lanes"] for w in prof["winners"])


def test_mcp_search_nodes_explain_on_legacy_reports_the_scan_it_actually_ran(tmp_path):
    """``explain`` must not be silently ignored on the one mode with no lanes."""
    server = _server_with_fixture(tmp_path)
    result = server.call_tool(
        "search_nodes", {"q": "3dgs shape", "mode": "legacy", "explain": True}
    )
    prof = result["profile"]
    assert prof["mode"] == "legacy"
    assert set(prof["lanes"]) == {"legacy"}
    assert prof["vector_cache"] is False
    assert prof["lanes"]["legacy"]["embed_calls"] == 0
    assert prof["returned"] == len(result["nodes"])
    assert [w["node_id"] for w in prof["winners"]] == [n["id"] for n in result["nodes"]]


def test_mcp_search_nodes_advertises_explain_with_its_cost_warning(tmp_path):
    server = _server_with_fixture(tmp_path)
    tools = {tool["name"]: tool for tool in server.list_tools()}
    for tool_name in ("search_nodes", "compile_context"):
        schema = tools[tool_name]["inputSchema"]["properties"]
        assert schema["explain"]["default"] is False
        # PROFILE's own posture: the flag costs time, and the description says so.
        assert "cost" in schema["explain"]["description"].lower()


# ---------------------------------------------------------------------------
# source_root: raw source text in the lexical lanes only
# ---------------------------------------------------------------------------


def _source_anchor_graph(root) -> ResearchGraph:
    """Two anchor nodes whose descriptions say nothing the query can match.

    The distinguishing words live only in the files, which is the whole point:
    extraction builds a node's searchable text from name + description, so a
    long document is otherwise reachable only through its summary.
    """
    (root / "alpha.md").write_text(
        "Alpha session. The customer upgraded to a 940 Mbps fibre plan.",
        encoding="utf-8",
    )
    (root / "beta.md").write_text(
        "Beta session. The customer discussed sourdough starter hydration.",
        encoding="utf-8",
    )
    return ResearchGraph(
        nodes=[
            ResearchNode(
                id=f"doc-{name}",
                name=f"{name} document",
                type=ResearchNodeType.SOURCE_DOCUMENT,
                description="a session transcript",
                source_path=str(root / f"{name}.md"),
            )
            for name in ("alpha", "beta")
        ]
    )


def test_source_root_makes_a_document_retrievable_through_its_own_text(tmp_path):
    graph = _source_anchor_graph(tmp_path)
    query = "Mbps fibre plan"

    without = hybrid_search(graph, query, top_k=2, mode="bm25")
    with_root = hybrid_search(graph, query, top_k=2, mode="bm25", source_root=tmp_path)

    # Neither description mentions Mbps, so BM25 matches nothing at all and
    # zero-scoring nodes never enter the result.
    assert without.scored == []
    # With the files in the lexical lane, alpha alone matches, on a term that
    # exists only inside its file.
    assert [s.node.id for s in with_root.scored] == ["doc-alpha"]
    assert with_root.scored[0].score > 0.0


def test_source_root_none_is_byte_identical_to_not_passing_it(tmp_path):
    graph = _source_anchor_graph(tmp_path)
    a = hybrid_search(graph, "session transcript", top_k=2)
    b = hybrid_search(graph, "session transcript", top_k=2, source_root=None)
    assert [(s.node.id, s.score) for s in a.scored] == [
        (s.node.id, s.score) for s in b.scored
    ]


def test_source_root_leaves_the_embedding_lane_reading_node_text(tmp_path):
    """The dense lane must not see raw text — pooling 8k chars into 256 dims is
    the ablation failure that cost it 0.7857 -> 0.6578.

    Spies on the VECTORISED implementation because that is the one that runs
    whenever numpy is importable; the scalar fallback is only reached without it.
    """
    from tesserae.retrieval import hybrid as hybrid_module

    graph = _source_anchor_graph(tmp_path)
    seen: List[List[str]] = []
    original = hybrid_module._embedding_scores_vectorized

    def _spy(query, texts, backend, cache):
        seen.append(list(texts))
        return original(query, texts, backend, cache)

    hybrid_module._embedding_scores_vectorized = _spy
    try:
        hybrid_search(graph, "fibre", top_k=2, mode="embedding", source_root=tmp_path)
    finally:
        hybrid_module._embedding_scores_vectorized = original

    assert seen, "the embedding lane never ran"
    assert not any("Mbps" in text for text in seen[0])
    # ...while the lexical lanes DO get it, so the gate is real and not a
    # coincidence of this fixture having no file text at all.
    lex = hybrid_module._lexical_texts(
        list(graph.nodes), [hybrid_module._node_text(n) for n in graph.nodes], tmp_path
    )
    assert any("Mbps" in text for text in lex)


def test_source_root_reads_nothing_outside_the_root(tmp_path):
    """``source_path`` is untrusted frontmatter. A node naming a file outside
    the root contributes no text — otherwise a crafted document pastes
    arbitrary files into a retrieval corpus and, downstream, an LLM prompt."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("SUPERSECRETTOKEN aardvark", encoding="utf-8")
    confined = tmp_path / "project"
    confined.mkdir()

    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id="doc-escape",
                name="escaping document",
                type=ResearchNodeType.SOURCE_DOCUMENT,
                description="a session transcript",
                source_path=str(confined / ".." / "outside" / "secret.md"),
            )
        ]
    )

    result = hybrid_search(
        graph, "SUPERSECRETTOKEN aardvark", top_k=1, mode="bm25", source_root=confined
    )
    # No text was read, so the query matches nothing and the node is not
    # returned. Had the file been read it would score above zero and rank first.
    assert result.scored == []


def test_source_root_ignores_non_anchor_nodes(tmp_path):
    """A concept extracted FROM a paper must not become retrievable through the
    paper's whole contents — every concept in it would then score alike."""
    (tmp_path / "paper.md").write_text("mentions telescopes throughout", encoding="utf-8")
    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id="concept-x",
                name="a concept",
                type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
                description="a concept lifted from a paper",
                source_path=str(tmp_path / "paper.md"),
            )
        ]
    )
    result = hybrid_search(graph, "telescopes", top_k=1, mode="bm25", source_root=tmp_path)
    assert result.scored == []
