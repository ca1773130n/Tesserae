"""Phase-6 real-embeddings contract guards (CI-safe, no model2vec needed).

These tests lock the Phase-6 behaviour so a future change cannot silently
re-introduce the hash-stub default, drop the fail-loud warning, break the
backend-gated candidate path, or leak embedding vectors into ``graph.json``.

Design constraints (from 06-03-PLAN.md):

* **CI-safe** — every behavioural guarantee that must always run uses either a
  deterministic in-test stub backend or monkeypatching. The one test that needs
  the heavy distilled model is gated behind ``pytest.importorskip("model2vec")``
  and skips cleanly when the dep is absent. No network, no torch.
* **Deterministic** — no wall-clock, no sleeps, no RNG. The stub backend maps
  text to a tiny fixed concept space; the idempotence guard compares sha256.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import warnings
from pathlib import Path
from typing import List

import pytest

from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.project import ProjectWiki
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.retrieval import hybrid
from tesserae.retrieval.hybrid import (
    HashEmbeddingBackend,
    active_embedding_backend,
    backend_is_semantic,
    hybrid_search,
    reset_embedding_backend,
)

WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"


# ---------------------------------------------------------------------------
# Fixtures: 8-node graph (trimmed copy of test_hybrid_search) + stub backend
# ---------------------------------------------------------------------------


def _eight_node_graph() -> ResearchGraph:
    """Eight-node fixture exercising each retrieval lane independently.

    Trimmed copy of ``tests/test_hybrid_search.py::_eight_node_graph`` — the
    ``MethodologicalConcept:rrf`` node is kept intact because the paraphrase
    gate-lift test targets it. Its description deliberately avoids the query's
    surface tokens ("reciprocal", "fusing", "ranked", "retrieval", "lists") so
    BM25/lexical alone cannot surface it; only a semantic backend can.
    """
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


class _StubSemanticBackend:
    """Deterministic non-hash backend for CI: maps texts to a small concept
    space so paraphrases land near their target. Proves the gate lift and the
    candidate path WITHOUT installing model2vec.

    It is NOT a ``HashEmbeddingBackend`` instance, so ``backend_is_semantic``
    reports True and ``hybrid_search``'s candidate gate treats it as a real
    semantic backend (admits embedding-only hits).
    """

    name = "stub-semantic:test"
    dim = 4
    # concept axes: [fusion/aggregation, ranking/retrieval, splatting/3d, vault/markdown]
    _LEX = {
        "fuse": 0, "fusing": 0, "fusion": 0, "aggregate": 0, "aggregation": 0,
        "combine": 0, "merge": 0, "reciprocal": 0, "rrf": 0, "rank": 1,
        "ranked": 1, "ranking": 1, "retrieval": 1, "search": 1, "bm25": 1,
        "okapi": 1, "splat": 2, "splatting": 2, "gaussian": 2, "render": 2,
        "3d": 2, "vault": 3, "obsidian": 3, "markdown": 3, "wiki": 3,
    }

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0, 0.0, 0.0, 0.0]
            for tok in (t or "").casefold().split():
                tok = "".join(ch for ch in tok if ch.isalnum())
                if tok in self._LEX:
                    v[self._LEX[tok]] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class _FakeModel2Vec:
    """A non-hash fake standing in for ``Model2VecBackend`` on the auto path."""

    name = "model2vec:fake"
    dim = 3

    def embed(self, texts):
        return [[0.0, 0.0, 0.0] for _ in texts]


@pytest.fixture(autouse=True)
def _reset_backend_cache():
    """Keep the module-level backend cache clean around every test so a
    monkeypatched constructor in one case never bleeds into another."""
    reset_embedding_backend()
    yield
    reset_embedding_backend()


# ---------------------------------------------------------------------------
# Task 1 — RETR-01 selection + fail-loud, RETR-02 candidate gate lift
# ---------------------------------------------------------------------------


def test_active_backend_warns_and_falls_back_when_no_real_backend(monkeypatch):
    """RETR-01 fail-loud: with BOTH real constructors raising ImportError, the
    ``auto`` path must emit a UserWarning naming ``tesserae[semantic]`` and then
    degrade to the hash stub (NOT silently)."""

    def _boom(*_args, **_kwargs):
        raise ImportError("simulated missing optional dependency")

    monkeypatch.setattr(hybrid, "Model2VecBackend", _boom)
    monkeypatch.setattr(hybrid, "SentenceTransformersBackend", _boom)
    reset_embedding_backend()

    with pytest.warns(UserWarning, match=r"tesserae\[semantic\]"):
        backend = active_embedding_backend("auto")

    assert isinstance(backend, HashEmbeddingBackend)
    assert backend_is_semantic(backend) is False


def test_active_backend_prefers_real_when_available(monkeypatch):
    """RETR-01 positive: model2vec is tried FIRST on the auto path; when it
    constructs, it wins with no warning."""
    monkeypatch.setattr(hybrid, "Model2VecBackend", _FakeModel2Vec)
    reset_embedding_backend()

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning fails the test
        backend = active_embedding_backend("auto")

    assert backend.name == "model2vec:fake"
    assert backend_is_semantic(backend) is True


def test_real_model2vec_backend_if_installed():
    """When model2vec IS installed, the real backend embeds to its own ``dim``
    and self-identifies as semantic. Skips cleanly in CI without the model."""
    pytest.importorskip("model2vec")
    from tesserae.retrieval.hybrid import Model2VecBackend

    b = Model2VecBackend()
    vec = b.embed(["hello"])[0]
    assert len(vec) == b.dim
    assert b.name.startswith("model2vec:")
    assert backend_is_semantic(b) is True


def test_real_backend_lifts_candidate_gate():
    """RETR-02 core: a paraphrase with near-zero lexical overlap with the RRF
    node surfaces it ONLY because the non-hash backend lifts the candidate gate.
    The same query under the hash backend's gate rejects the embedding-only hit.
    """
    graph = _eight_node_graph()
    # "combine merge" are pure fusion-axis synonyms: they live on the stub's
    # fusion concept axis but appear in NO node's text, so the rrf node has zero
    # lexical (BM25/lexical) overlap with the query. It can only surface via the
    # semantic embedding lane — the exact paraphrase case RETR-02 must admit.
    query = "combine merge"
    rrf = "MethodologicalConcept:rrf"

    stub_result = hybrid_search(
        graph, query, top_k=8, backend=_StubSemanticBackend(), mode="hybrid"
    )
    stub_ids = [s.node.id for s in stub_result.scored]
    assert rrf in stub_ids, "non-hash backend must admit the embedding-only rrf hit"

    # The rrf node qualified via the embedding lane, not lexical evidence.
    rrf_scored = next(s for s in stub_result.scored if s.node.id == rrf)
    assert rrf_scored.per_lane["embedding"] > 0
    assert rrf_scored.per_lane["bm25"] == 0
    assert rrf_scored.per_lane["lexical"] == 0

    # Same query, hash backend: its gate requires lexical evidence, so the
    # embedding-only rrf candidate is rejected. The stub candidate set is a
    # strict superset wrt the embedding-only hit.
    hash_result = hybrid_search(
        graph, query, top_k=8, backend=HashEmbeddingBackend(), mode="hybrid"
    )
    hash_ids = {s.node.id for s in hash_result.scored}
    assert rrf not in hash_ids, "hash gate must reject the embedding-only rrf hit"
    assert set(stub_ids) > hash_ids or rrf not in hash_ids


# ---------------------------------------------------------------------------
# Task 2 — byte-idempotence guard + embedding_status semantic flag
# ---------------------------------------------------------------------------


def _seed_project(project_root: Path) -> ProjectWiki:
    shutil.copytree(WIKI_CORPUS_ROOT / "data", project_root / "data")
    shutil.copytree(WIKI_CORPUS_ROOT / "docs", project_root / "docs")
    return ProjectWiki.init(project_root, name="phase6_real_embeddings")


def _looks_like_vector(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)
    )


_EMBED_SENTINELS = ("embedding", "vector", "embed_vec")


def test_search_does_not_break_byte_idempotence(tmp_path):
    """Pitfall-5 path-specific guard: compile -> hybrid search (exercising the
    embedding lane) -> compile must leave graph.json byte-identical, and no
    embedding vectors may be serialized into graph.json / node.metadata."""
    project_root = tmp_path / "proj"
    wiki = _seed_project(project_root)

    wiki.compile()
    graph_path = wiki.paths.graph
    assert graph_path.exists(), "first compile must produce graph.json"
    first_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()

    # Drive a real retrieval over the compiled graph through the MCP server —
    # default hybrid weights include embedding=1.0, so the embedding lane runs.
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # hash fallback warning asserted in Task 1
        result = server.call_tool(
            "search_nodes", {"q": "reciprocal fusion ranking", "mode": "hybrid"}
        )
    assert result["mode"] == "hybrid"

    # Compile again: the search must not have mutated persisted graph state.
    wiki.compile()
    second_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    assert second_hash == first_hash, "search must not perturb graph.json bytes"

    # No embedding-vector leakage into graph.json or any node.metadata.
    raw = graph_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    for node in parsed.get("nodes", []):
        meta = node.get("metadata") or {}
        for key, value in meta.items():
            lowered = key.lower()
            assert not any(s in lowered for s in _EMBED_SENTINELS), (
                f"node {node.get('id')!r} leaked embedding-like metadata key {key!r}"
            )
            assert not _looks_like_vector(value), (
                f"node {node.get('id')!r} metadata {key!r} looks like a float vector"
            )


def test_embedding_status_reports_semantic_flag(tmp_path, monkeypatch):
    """``embedding_status.semantic`` is False for the hash stub (default CI env)
    and True for a real/stub semantic backend."""
    project_root = tmp_path / "proj"
    wiki = _seed_project(project_root)
    wiki.compile()
    server = LLMWikiMCPServer(default_graph_path=wiki.paths.graph)

    # Default env (no model2vec): hash-bucket stub, not semantic.
    reset_embedding_backend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # fail-loud warning asserted in Task 1
        status = server.call_tool("embedding_status", {})
    assert status["available"] is True
    assert status["backend"] == "hash-bucket"
    assert status["semantic"] is False

    # Swap in a non-hash backend via the resolver and re-check the flag.
    monkeypatch.setattr(hybrid, "Model2VecBackend", _FakeModel2Vec)
    reset_embedding_backend()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a real backend must NOT warn
        status2 = server.call_tool("embedding_status", {})
    assert status2["semantic"] is True
    assert status2["backend"] == "model2vec:fake"

    reset_embedding_backend()
