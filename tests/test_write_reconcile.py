"""Write-time reconciliation (memory.reconcile, R3) with a fake classifier and
a fake embedder. No model, no network, no LLM.

The fake embedder maps each node to a hand-placed vector, so which pairs are
neighbours is decided by the test, not by a model. The fake classifier answers
from a table keyed on (existing id, new id), so the test also decides the
verdict — what is under test is everything around it: candidate generation,
EXISTING/NEW ordering, the verdict cache, the edge and sidecar writes, the
honest skips, and the demote-not-filter read surfaces.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

pytest.importorskip("numpy")

from tesserae.graph_filters import invalidations, suppressed_ids
from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.memory.reconcile import (
    ASSERTED_BY,
    LLMReconcileClassifier,
    ReconcileVerdict,
    reconcile_pass_enabled,
    run_reconcile_pass,
)
from tesserae.memory.store import read_proofs
from tesserae.memory.supersede import _candidate_pairs, embedding_candidate_pairs
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    graph_from_payload,
)
from tesserae.retrieval.hybrid import HashEmbeddingBackend
from tesserae.retrieval.vector_cache import node_embedding_text
from tesserae.verify import verify_claim

INSIGHT = ResearchNodeType.SESSION_INSIGHT


class FakeEmbedder:
    """Semantic-looking backend: returns the vector registered for a text."""

    name = "fake-semantic"
    dim = 3

    def __init__(self, by_text: Dict[str, List[float]]) -> None:
        self.by_text = by_text
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        self.calls += 1
        return [list(self.by_text.get(t, [0.0, 0.0, 1.0])) for t in texts]


class FakeClassifier:
    name = "fake-classifier-v1"

    def __init__(self, table: Dict[Tuple[str, str], str], *, raises: bool = False) -> None:
        self.table = table
        self.calls: List[Tuple[str, str]] = []
        self.raises = raises

    def classify(self, existing, new) -> Optional[ReconcileVerdict]:
        self.calls.append((existing.id, new.id))
        if self.raises:
            raise RuntimeError("classifier backend down")
        verdict = self.table.get((existing.id, new.id))
        return None if verdict is None else ReconcileVerdict(verdict, rationale="fake")


def finding(nid, name, *, when, ntype=INSIGHT, description="", path=None):
    return ResearchNode(
        id=nid,
        name=name,
        type=ntype,
        description=description,
        source_path=path,
        metadata={"first_seen_at": when, "session_id": f"s-{when}"},
    )


def embedder_for(vectors: Sequence[Tuple[ResearchNode, List[float]]]) -> FakeEmbedder:
    return FakeEmbedder({node_embedding_text(n): v for n, v in vectors})


# Two paraphrases with NO shared name token (Jaccard 0), one unrelated finding.
OLD = finding("SessionInsight:b-old", "Cache lives in redis", when="2026-01-01T00:00:00+00:00")
NEW = finding("SessionInsight:a-new", "Memcached replaced that store", when="2026-03-01T00:00:00+00:00")
FAR = finding("SessionInsight:c-far", "Tokenizer splits on hyphens", when="2026-02-01T00:00:00+00:00")
VECTORS = [(OLD, [1.0, 0.0, 0.0]), (NEW, [0.95, 0.31, 0.0]), (FAR, [0.0, 1.0, 0.0])]


def graph_of(*nodes, edges=()):
    return ResearchGraph(nodes=list(nodes), edges=list(edges))


def run(graph, table, tmp_path, *, embedder=None, **kw):
    classifier = kw.pop("classifier", None) or FakeClassifier(table)
    graph, stats = run_reconcile_pass(
        graph,
        classifier=classifier,
        embedder=embedder or embedder_for(VECTORS),
        cache_dir=tmp_path / "reconcile_cache",
        proof_db_path=kw.pop("proof_db_path", tmp_path / "sqlite.db"),
        recorded_at="2026-04-01T00:00:00+00:00",
        **kw,
    )
    return graph, stats, classifier


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------


def test_embedding_candidates_find_paraphrases_jaccard_cannot():
    assert _candidate_pairs([OLD, NEW, FAR], 0.55) == []
    pairs = embedding_candidate_pairs([OLD, NEW, FAR], embedder_for(VECTORS), min_similarity=0.6)
    assert [(a.id, b.id) for a, b, _ in pairs] == [(NEW.id, OLD.id)]
    assert pairs[0][2] == pytest.approx(0.95 / (0.95**2 + 0.31**2) ** 0.5)


def test_embedding_candidates_stay_inside_one_core_type_and_respect_top_s():
    claim = finding("Claim:x", "Cache lives in redis", when="2026-01-02", ntype=ResearchNodeType.CLAIM)
    near = [finding(f"SessionInsight:n{i}", f"near {i}", when=f"2026-01-0{i + 1}") for i in range(4)]
    vectors = [(claim, [1.0, 0.0, 0.0]), (OLD, [1.0, 0.0, 0.0])]
    vectors += [(n, [1.0, 0.01 * (i + 1), 0.0]) for i, n in enumerate(near)]
    pairs = embedding_candidate_pairs([claim, OLD, *near], embedder_for(vectors), top_s=1)
    ids = {(a.id, b.id) for a, b, _ in pairs}
    assert all("Claim:x" not in pair for pair in ids), "a claim never pairs with a finding"
    # Each node keeps only its single best neighbour; the union is deduped.
    assert len(ids) <= len([OLD, *near])


# ---------------------------------------------------------------------------
# Honest skips
# ---------------------------------------------------------------------------


def test_hash_stub_backend_skips_loudly_and_writes_nothing(tmp_path, caplog):
    graph = graph_of(OLD, NEW)
    before = graph.to_json()
    with caplog.at_level(logging.WARNING, logger="tesserae.memory.reconcile"):
        graph, stats, classifier = run(
            graph, {(OLD.id, NEW.id): "INVALIDATE"}, tmp_path, embedder=HashEmbeddingBackend()
        )
    assert stats.skipped and "hash-stub" in stats.skipped
    assert classifier.calls == []
    assert graph.to_json() == before
    assert any("skipped" in r.getMessage() for r in caplog.records)


def test_pass_is_opt_in(monkeypatch):
    monkeypatch.delenv("TESSERAE_RECONCILE_PASS", raising=False)
    assert reconcile_pass_enabled() is False
    monkeypatch.setenv("TESSERAE_RECONCILE_PASS", "1")
    assert reconcile_pass_enabled() is True


# ---------------------------------------------------------------------------
# Verdict effects
# ---------------------------------------------------------------------------


def test_invalidate_writes_dated_attested_edge_and_deletes_nothing(tmp_path):
    graph, stats, classifier = run(graph_of(OLD, NEW, FAR), {(OLD.id, NEW.id): "INVALIDATE"}, tmp_path)
    # Ordered by source time, not by id: NEW's id sorts first but it is newer.
    assert classifier.calls == [(OLD.id, NEW.id)]
    assert [n.id for n in graph.nodes] == [OLD.id, NEW.id, FAR.id]
    (edge,) = graph.edges
    assert (edge.source, edge.type, edge.target) == (NEW.id, "supersedes", OLD.id)
    assert edge.invalid_at == "2026-03-01T00:00:00+00:00"  # NEW's own valid time
    assert edge.status == "attested" and edge.asserted_by == ASSERTED_BY
    assert edge.provenance["invalid_at_basis"] == "source"
    assert stats.invalidations_minted == 1 and stats.verdicts["INVALIDATE"] == 1
    # Round-trips through graph.json.
    reloaded = graph_from_payload(json.loads(graph.to_json()))
    assert reloaded.edges[0].invalid_at == edge.invalid_at


def test_invalidate_falls_back_to_compile_reference_when_undated(tmp_path):
    older = ResearchNode(id="SessionInsight:b-old", name=OLD.name, type=INSIGHT)
    newer = ResearchNode(id="SessionInsight:a-new", name=NEW.name, type=INSIGHT)
    vectors = [(older, [1.0, 0.0, 0.0]), (newer, [0.95, 0.31, 0.0])]
    # No clock separates them, so the order is the stable id order.
    graph, stats, classifier = run(
        graph_of(older, newer), {(newer.id, older.id): "INVALIDATE"}, tmp_path,
        embedder=embedder_for(vectors),
    )
    assert classifier.calls == [(newer.id, older.id)]
    (edge,) = graph.edges
    assert edge.invalid_at == "2026-04-01T00:00:00+00:00"
    assert edge.provenance["invalid_at_basis"] == "compile_reference"


def test_update_appends_one_proof_per_evidence_and_reruns_do_not_double_count(tmp_path):
    new = finding(NEW.id, NEW.name, when="2026-03-01T00:00:00+00:00", description="Measured 2x faster.")
    vectors = [(OLD, [1.0, 0.0, 0.0]), (new, [0.95, 0.31, 0.0])]
    for _ in range(2):
        graph, stats, _ = run(
            graph_of(OLD, new), {(OLD.id, new.id): "UPDATE"}, tmp_path, embedder=embedder_for(vectors)
        )
    assert graph.edges == [], "UPDATE never touches graph.json"
    proofs = read_proofs(tmp_path / "sqlite.db")
    assert list(proofs) == [OLD.id]
    (proof,) = proofs[OLD.id]
    assert proof.evidence_node_id == new.id
    # The fake gave no span, so the verbatim fallback is NEW's name.
    assert proof.evidence_span == new.name
    assert stats.proofs_added == 0, "second run re-asserts the same proof"


def test_add_and_noop_write_nothing(tmp_path):
    for verdict in ("ADD", "NOOP"):
        graph, stats, _ = run(graph_of(OLD, NEW), {(OLD.id, NEW.id): verdict}, tmp_path / verdict)
        assert graph.edges == [] and stats.verdicts[verdict] == 1
        assert read_proofs(tmp_path / verdict / "sqlite.db") == {}


def test_verdicts_are_cached_by_content_so_reruns_are_free_and_identical(tmp_path):
    table = {(OLD.id, NEW.id): "INVALIDATE"}
    first, _, _ = run(graph_of(OLD, NEW), table, tmp_path)
    second_classifier = FakeClassifier({})  # would answer nothing if asked
    second, stats, _ = run(graph_of(OLD, NEW), {}, tmp_path, classifier=second_classifier)
    assert second_classifier.calls == []
    assert stats.cached == 1
    assert second.to_json() == first.to_json()


def test_existing_supersedes_edge_is_upgraded_and_opposite_edge_is_a_conflict(tmp_path):
    same = ResearchEdge(source=NEW.id, target=OLD.id, type="supersedes", metadata={"extractor": "memory.supersede"})
    graph, stats, _ = run(graph_of(OLD, NEW, edges=[same]), {(OLD.id, NEW.id): "INVALIDATE"}, tmp_path / "a")
    (edge,) = graph.edges
    assert edge.invalid_at and edge.metadata == {"extractor": "memory.supersede"}
    assert stats.invalidations_upgraded == 1 and stats.invalidations_minted == 0

    opposite = ResearchEdge(source=OLD.id, target=NEW.id, type="supersedes")
    graph, stats, _ = run(graph_of(OLD, NEW, edges=[opposite]), {(OLD.id, NEW.id): "INVALIDATE"}, tmp_path / "b")
    assert graph.edges == [opposite], "never suppress both sides of a pair"
    assert stats.conflicts == 1


def test_failures_and_budget_are_counted_logged_and_not_cached(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="tesserae.memory.reconcile"):
        _, stats, _ = run(
            graph_of(OLD, NEW), {}, tmp_path, classifier=FakeClassifier({}, raises=True)
        )
    assert stats.failed == 1 and not (tmp_path / "reconcile_cache").exists()
    assert any("classifier raised" in r.getMessage() for r in caplog.records)

    _, stats, classifier = run(graph_of(OLD, NEW), {(OLD.id, NEW.id): "ADD"}, tmp_path, max_calls=0)
    assert classifier.calls == [] and stats.over_budget == 1


# ---------------------------------------------------------------------------
# LLM classifier over the project's JSON client (fake client)
# ---------------------------------------------------------------------------


class FakeJsonClient:
    def __init__(self, reply):
        self.reply = reply
        self.prompts: List[str] = []

    def complete_json(self, *, system, user, schema_name, cache_key=None, max_retries=2):
        self.prompts.append(user)
        return self.reply


def test_llm_classifier_parses_and_rejects_bad_verdicts(caplog):
    good = LLMReconcileClassifier(FakeJsonClient({"verdict": "invalidate", "rationale": "r"}))
    assert good.classify(OLD, NEW) == ReconcileVerdict("INVALIDATE", rationale="r")
    assert "EXISTING" in good.client.prompts[0] and OLD.name in good.client.prompts[0]
    with caplog.at_level(logging.WARNING, logger="tesserae.memory.reconcile"):
        assert LLMReconcileClassifier(FakeJsonClient({"verdict": "delete"})).classify(OLD, NEW) is None
        assert LLMReconcileClassifier(FakeJsonClient(None)).classify(OLD, NEW) is None
    assert len([r for r in caplog.records if r.name == "tesserae.memory.reconcile"]) == 2


# ---------------------------------------------------------------------------
# Read surfaces: demote, never filter
# ---------------------------------------------------------------------------


def _invalidated_claim_graph():
    sentence = "Widget throughput doubled on the held-out split."
    paper = ResearchNode(id="Paper:p", name="Alpha", type=ResearchNodeType.PAPER)
    claim = ResearchNode(id="Claim:q", name="Beta", type=ResearchNodeType.CLAIM, source_path="docs/alpha.md")
    newer = ResearchNode(id="Claim:r", name="Gamma", type=ResearchNodeType.CLAIM)
    span = ResearchNode(
        id="EvidenceSpan:s1", name="span-1", type=ResearchNodeType.EVIDENCE_SPAN,
        description=sentence, source_path="docs/alpha.md",
    )
    return ResearchGraph(
        nodes=[paper, claim, newer, span],
        edges=[
            ResearchEdge(source=paper.id, target=claim.id, type="supports_claim", evidence=sentence),
            ResearchEdge(source=claim.id, target=span.id, type="evidenced_by", evidence=sentence),
            ResearchEdge(
                source=newer.id, target=claim.id, type="supersedes", status="attested",
                asserted_by=ASSERTED_BY, invalid_at="2026-03-01",
            ),
        ],
    )


def test_verify_claim_demotes_invalidated_endpoint_without_changing_verdict():
    graph = _invalidated_claim_graph()
    out = verify_claim(graph, subject="Paper:p", predicate="supports_claim", obj="Claim:q", reground=False)
    assert out["verdict"] == "SUPPORTED"
    assert out["advisory"]["invalidated_endpoints"] == {
        "Claim:q": [{"invalidated_by": "Claim:r", "invalid_at": "2026-03-01", "asserted_by": ASSERTED_BY}]
    }
    # A graph without invalidations returns the payload it always did.
    plain = ResearchGraph(nodes=graph.nodes, edges=graph.edges[:2])
    out = verify_claim(plain, subject="Paper:p", predicate="supports_claim", obj="Claim:q", reground=False)
    assert "invalidated_endpoints" not in out["advisory"]


def test_untrusted_invalidation_is_not_reported():
    graph = _invalidated_claim_graph()
    candidate = [e if e.type != "supersedes" else ResearchEdge(
        source=e.source, target=e.target, type=e.type, status="candidate", invalid_at=e.invalid_at
    ) for e in graph.edges]
    assert invalidations(ResearchGraph(nodes=graph.nodes, edges=candidate)) == {}


def test_raw_source_serves_whole_body_and_names_invalidated_facts(tmp_path):
    graph = _invalidated_claim_graph()
    root = tmp_path / "proj"
    (root / ".tesserae").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "alpha.md").write_text("# Alpha\n\nBeta holds.\n", encoding="utf-8")
    graph_path = root / ".tesserae" / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    out = server.call_tool("raw_source", {"source_path": "docs/alpha.md"})
    assert out["body"] == "# Alpha\n\nBeta holds.\n"
    assert list(out["invalidated_facts"]) == ["Claim:q"]
    # Search surfaces still suppress it by default; raw_source does not.
    assert "Claim:q" in suppressed_ids(graph)

    graph_path.write_text(
        ResearchGraph(nodes=graph.nodes, edges=graph.edges[:2]).to_json(indent=2), encoding="utf-8"
    )
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    assert "invalidated_facts" not in server.call_tool("raw_source", {"source_path": "docs/alpha.md"})


# ---------------------------------------------------------------------------
# Compile wiring (ProjectWiki._run_memory_passes)
# ---------------------------------------------------------------------------


def test_compile_runs_the_pass_only_when_opted_in_and_llm_gated(tmp_path, monkeypatch, caplog):
    import tesserae.retrieval.hybrid as hybrid
    from tesserae.project import ProjectWiki

    monkeypatch.setattr(hybrid, "active_embedding_backend", lambda *a, **k: embedder_for(VECTORS))
    wiki = ProjectWiki.init(tmp_path, name="t")
    client = FakeJsonClient({"verdict": "INVALIDATE", "rationale": "newer store"})

    monkeypatch.delenv("TESSERAE_RECONCILE_PASS", raising=False)
    graph, _ = wiki._run_memory_passes(graph_of(OLD, NEW), client)
    assert graph.edges == [] and client.prompts == [], "default compile never reconciles"

    monkeypatch.setenv("TESSERAE_RECONCILE_PASS", "1")
    monkeypatch.delenv("TESSERAE_ENABLE_LLM_PASSES", raising=False)
    with caplog.at_level(logging.WARNING, logger="tesserae.project"):
        graph, _ = wiki._run_memory_passes(graph_of(OLD, NEW), None)
    assert graph.edges == []
    assert any("no LLM passes client" in r.getMessage() for r in caplog.records)

    graph, rows = wiki._run_memory_passes(graph_of(OLD, NEW), client)
    (edge,) = graph.edges
    assert (edge.source, edge.target, edge.asserted_by) == (NEW.id, OLD.id, ASSERTED_BY)
    assert {r.node_id: r.superseded for r in rows} == {OLD.id: True, NEW.id: False}
